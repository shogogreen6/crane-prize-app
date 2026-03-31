from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - handled at runtime
    PlaywrightTimeoutError = None
    sync_playwright = None


BSP_SEARCH_URL = "https://bsp-prize.jp/search/"
BSP_SITE_ROOT = "https://bsp-prize.jp"
SEGA_SEARCH_URL = "https://segaplaza.jp/search/"
SEGA_SITE_ROOT = "https://segaplaza.jp"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


@dataclass
class PrizeItem:
    site: str
    keyword: str
    name: str
    date_text: str
    image_url: str
    page: int
    sort_date: date | None
    source_url: str


@dataclass
class ProgressReporter:
    enabled: bool = False

    def log(self, message: str) -> None:
        if self.enabled:
            print(f"[progress] {message}", file=sys.stderr, flush=True)


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def normalize_image_url(src: str | None, site_root: str) -> str:
    if not src:
        return ""
    return urljoin(site_root, src)


def parse_products_date(date_text: str) -> date | None:
    text = date_text.strip().replace(" ", "").replace("\u3000", "")

    full_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if full_match:
        year, month, day = map(int, full_match.groups())
        return date(year, month, day)

    month_match = re.search(r"(\d{4})年(\d{1,2})月", text)
    if month_match:
        year, month = map(int, month_match.groups())
        return date(year, month, 1)

    season_match = re.search(r"(\d{4})年(春|夏|秋|冬)", text)
    if season_match:
        year = int(season_match.group(1))
        season = season_match.group(2)
        season_month = {"春": 4, "夏": 7, "秋": 10, "冬": 1}[season]
        return date(year, season_month, 1)

    return None


def parse_cli_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def in_period(item: PrizeItem, start_date: date | None, end_date: date | None) -> bool:
    if start_date is None and end_date is None:
        return True
    if item.sort_date is None:
        return False
    if start_date and item.sort_date < start_date:
        return False
    if end_date and item.sort_date > end_date:
        return False
    return True


def should_stop_early(page_items: Iterable[PrizeItem], start_date: date | None) -> bool:
    if start_date is None:
        return False

    sortable_dates = [item.sort_date for item in page_items if item.sort_date is not None]
    if not sortable_dates:
        return False

    oldest_on_page = min(sortable_dates)
    return oldest_on_page < start_date


def fetch_bsp_page(
    session: requests.Session,
    keyword: str,
    page: int,
) -> list[PrizeItem]:
    response = session.get(
        BSP_SEARCH_URL,
        params={"kw": keyword, "page": page},
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    products_list = soup.select_one("div.products_list")
    if products_list is None:
        return []

    items: list[PrizeItem] = []
    for product in products_list.select("div.products_item"):
        img = product.select_one(".products_img img")
        name_node = product.select_one(".products_name")
        date_node = product.select_one(".products_date")
        link_node = product.select_one("a[href]")

        name = name_node.get_text(" ", strip=True) if name_node else ""
        date_text = date_node.get_text(" ", strip=True) if date_node else ""
        image_url = normalize_image_url(img.get("src") if img else None, BSP_SITE_ROOT)
        source_url = normalize_image_url(link_node.get("href") if link_node else None, BSP_SITE_ROOT)

        items.append(
            PrizeItem(
                site="BANDAI SPIRITS",
                keyword=keyword,
                name=name,
                date_text=date_text,
                image_url=image_url,
                page=page,
                sort_date=parse_products_date(date_text),
                source_url=source_url or response.url,
            )
        )

    return items


def collect_bsp_prizes(
    keywords: list[str],
    start_date: date | None,
    end_date: date | None,
    delay: float,
    reporter: ProgressReporter,
) -> list[PrizeItem]:
    session = build_session()
    collected: list[PrizeItem] = []

    for keyword in keywords:
        reporter.log(f"BSP start keyword='{keyword}'")
        page = 1
        while True:
            reporter.log(f"BSP fetching keyword='{keyword}' page={page}")
            page_items = fetch_bsp_page(session, keyword, page)
            if not page_items:
                reporter.log(f"BSP finished keyword='{keyword}' page={page} reason=empty_page")
                break

            matched_items = [
                item for item in page_items if in_period(item, start_date, end_date)
            ]
            collected.extend(matched_items)
            reporter.log(
                "BSP page complete "
                f"keyword='{keyword}' page={page} "
                f"page_items={len(page_items)} matched={len(matched_items)}"
            )

            if should_stop_early(page_items, start_date):
                reporter.log(
                    f"BSP finished keyword='{keyword}' page={page} reason=older_than_start_date"
                )
                break

            page += 1
            if delay > 0:
                time.sleep(delay)

    return collected


def collect_sega_prizes(
    keywords: list[str],
    start_date: date | None,
    end_date: date | None,
    delay: float,
    reporter: ProgressReporter,
) -> list[PrizeItem]:
    if sync_playwright is None or PlaywrightTimeoutError is None:
        raise RuntimeError(
            "SEGA Plaza scraping requires Playwright. "
            "Install it with 'pip install playwright' and "
            "run 'python -m playwright install chromium'."
        )

    collected: list[PrizeItem] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)

        try:
            for keyword in keywords:
                reporter.log(f"SEGA start keyword='{keyword}'")
                page = context.new_page()
                page_number = 1
                try:
                    reporter.log(f"SEGA opening search page keyword='{keyword}'")
                    page.goto(
                        SEGA_SEARCH_URL,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    page.goto(
                        f"{SEGA_SEARCH_URL}?q={keyword}&type=prize&limit=500",
                        wait_until="networkidle",
                        timeout=60000,
                    )

                    try:
                        page.wait_for_selector(".itemList", timeout=20000)
                    except PlaywrightTimeoutError:
                        pass

                    # Results are client-rendered, so a small extra wait makes
                    # the list more reliable after the container appears.
                    page.wait_for_timeout(int(max(delay, 0.3) * 1000))

                    items = page.query_selector_all(".itemList .item")
                    matched_count = 0
                    for item in items:
                        name_node = item.query_selector(".textInfo .itemName")
                        date_node = item.query_selector(".textInfo .tag-text-date")
                        image_node = item.query_selector(".thumbnail img")
                        link_node = item.query_selector("a[href]")

                        name = name_node.inner_text().strip() if name_node else ""
                        date_text = date_node.inner_text().strip() if date_node else ""
                        image_url = normalize_image_url(
                            image_node.get_attribute("src") if image_node else None,
                            SEGA_SITE_ROOT,
                        )
                        source_url = normalize_image_url(
                            link_node.get_attribute("href") if link_node else None,
                            SEGA_SITE_ROOT,
                        )

                        prize = PrizeItem(
                            site="SEGA Plaza",
                            keyword=keyword,
                            name=name,
                            date_text=date_text,
                            image_url=image_url,
                            page=page_number,
                            sort_date=parse_products_date(date_text),
                            source_url=source_url or page.url,
                        )
                        if in_period(prize, start_date, end_date):
                            collected.append(prize)
                            matched_count += 1
                    reporter.log(
                        "SEGA page complete "
                        f"keyword='{keyword}' page={page_number} "
                        f"page_items={len(items)} matched={matched_count}"
                    )
                finally:
                    page.close()
                    reporter.log(f"SEGA finished keyword='{keyword}'")
        finally:
            context.close()
            browser.close()

    return collected


def collect_prizes(
    keywords: list[str],
    start_date: date | None,
    end_date: date | None,
    delay: float,
    sites: list[str],
    reporter: ProgressReporter,
) -> list[PrizeItem]:
    collected: list[PrizeItem] = []
    reporter.log(
        f"collect start sites={','.join(sites)} keywords={len(keywords)} "
        f"start_date={start_date} end_date={end_date}"
    )

    if "bsp" in sites:
        collected.extend(
            collect_bsp_prizes(
                keywords=keywords,
                start_date=start_date,
                end_date=end_date,
                delay=delay,
                reporter=reporter,
            )
        )

    if "segaplaza" in sites:
        collected.extend(
            collect_sega_prizes(
                keywords=keywords,
                start_date=start_date,
                end_date=end_date,
                delay=delay,
                reporter=reporter,
            )
        )

    collected.sort(
        key=lambda item: (
            item.sort_date or date.min,
            item.site,
            item.name,
        ),
        reverse=True,
    )
    reporter.log(f"collect complete total_items={len(collected)}")
    return collected


def render_html(items: list[PrizeItem], title: str) -> str:
    cards = []
    for item in items:
        sort_date_text = item.sort_date.isoformat() if item.sort_date else "unknown"
        image_html = (
            f'<img src="{html.escape(item.image_url)}" alt="{html.escape(item.name)}">'
            if item.image_url
            else '<div class="no-image">No Image</div>'
        )
        source_html = (
            f'<a href="{html.escape(item.source_url)}" target="_blank" rel="noreferrer">source</a>'
            if item.source_url
            else ""
        )
        cards.append(
            f"""
            <article class="card">
              <div class="thumb">{image_html}</div>
              <div class="meta">
                <p class="site">{html.escape(item.site)}</p>
                <p class="keyword">{html.escape(item.keyword)}</p>
                <h2>{html.escape(item.name)}</h2>
                <p class="date">{html.escape(item.date_text)}</p>
                <p class="sub">sort_date: {html.escape(sort_date_text)} / page: {item.page} / {source_html}</p>
              </div>
            </article>
            """
        )

    body = "\n".join(cards) if cards else '<p class="empty">No matching prizes were found.</p>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f3ea;
      --panel: #fffdf8;
      --line: #d8ccb7;
      --text: #2e261d;
      --muted: #72614f;
      --accent: #b55d32;
      --shadow: 0 16px 40px rgba(76, 48, 23, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Yu Gothic", "Hiragino Sans", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(181, 93, 50, 0.15), transparent 28%),
        linear-gradient(180deg, #f7f2ea 0%, var(--bg) 100%);
    }}
    main {{
      width: min(1100px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 56px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(1.8rem, 3vw, 2.8rem);
    }}
    .summary {{
      margin: 0 0 24px;
      color: var(--muted);
    }}
    .grid {{
      display: grid;
      gap: 18px;
    }}
    .card {{
      display: grid;
      grid-template-columns: minmax(160px, 220px) 1fr;
      gap: 18px;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      box-shadow: var(--shadow);
      align-items: start;
    }}
    .thumb {{
      aspect-ratio: 1 / 1;
      overflow: hidden;
      border-radius: 14px;
      background: #f0e7da;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .thumb img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .site, .keyword, .date, .sub, .empty {{
      margin: 0;
    }}
    .site {{
      color: var(--accent);
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .keyword {{
      color: var(--muted);
      margin-bottom: 8px;
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: 1.15rem;
      line-height: 1.5;
    }}
    .date {{
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .sub {{
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .sub a {{
      color: inherit;
    }}
    .empty {{
      padding: 24px;
      border: 1px dashed var(--line);
      border-radius: 16px;
      background: rgba(255, 253, 248, 0.7);
    }}
    .no-image {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
    @media (max-width: 720px) {{
      .card {{
        grid-template-columns: 1fr;
      }}
      .thumb {{
        max-width: 260px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p class="summary">items: {len(items)} / newest first / merged across sites</p>
    <section class="grid">
      {body}
    </section>
  </main>
</body>
</html>
"""


def save_outputs(items: list[PrizeItem], html_path: Path, json_path: Path) -> None:
    html_path.write_text(
        render_html(items, "Prize Search Results"),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            [
                {
                    **asdict(item),
                    "sort_date": item.sort_date.isoformat() if item.sort_date else None,
                }
                for item in items
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect prize item data from BSP and SEGA Plaza and render the merged result as HTML."
    )
    parser.add_argument(
        "--keyword",
        action="append",
        required=True,
        help="Search keyword. Repeat --keyword to search multiple anime titles.",
    )
    parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        help="End date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--site",
        action="append",
        choices=["bsp", "segaplaza"],
        help="Target site. Repeat to narrow the scrape target. Default is both.",
    )
    parser.add_argument(
        "--output-html",
        default="bsp_prizes.html",
        help="HTML output file.",
    )
    parser.add_argument(
        "--output-json",
        default="bsp_prizes.json",
        help="JSON output file.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests or render checks in seconds. Default: 0.5",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Show progress logs while scraping.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reporter = ProgressReporter(enabled=args.progress)

    start_date = parse_cli_date(args.start_date)
    end_date = parse_cli_date(args.end_date)
    if start_date and end_date and start_date > end_date:
        raise ValueError("start-date must be less than or equal to end-date.")

    keywords = [keyword.strip() for keyword in args.keyword if keyword.strip()]
    if not keywords:
        raise ValueError("At least one keyword is required.")

    sites = args.site or ["bsp", "segaplaza"]
    items = collect_prizes(
        keywords=keywords,
        start_date=start_date,
        end_date=end_date,
        delay=args.delay,
        sites=sites,
        reporter=reporter,
    )

    html_path = Path(args.output_html)
    json_path = Path(args.output_json)
    save_outputs(items, html_path=html_path, json_path=json_path)
    reporter.log(f"saved html='{html_path.resolve()}' json='{json_path.resolve()}'")

    print(f"items: {len(items)}")
    print(f"HTML: {html_path.resolve()}")
    print(f"JSON: {json_path.resolve()}")
    if items:
        print(f"first: {items[0].site} / {items[0].name} / {items[0].date_text}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
