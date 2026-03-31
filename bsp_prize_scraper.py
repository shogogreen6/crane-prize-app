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


BASE_URL = "https://bsp-prize.jp/search/"
SITE_ROOT = "https://bsp-prize.jp"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


@dataclass
class PrizeItem:
    keyword: str
    name: str
    date_text: str
    image_url: str
    page: int
    sort_date: date | None


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def normalize_image_url(src: str | None) -> str:
    if not src:
        return ""
    return urljoin(SITE_ROOT, src)


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


def fetch_page(session: requests.Session, keyword: str, page: int) -> list[PrizeItem]:
    response = session.get(
        BASE_URL,
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

        name = name_node.get_text(" ", strip=True) if name_node else ""
        date_text = date_node.get_text(" ", strip=True) if date_node else ""
        image_url = normalize_image_url(img.get("src") if img else None)

        items.append(
            PrizeItem(
                keyword=keyword,
                name=name,
                date_text=date_text,
                image_url=image_url,
                page=page,
                sort_date=parse_products_date(date_text),
            )
        )

    return items


def should_stop_early(
    page_items: Iterable[PrizeItem],
    start_date: date | None,
) -> bool:
    if start_date is None:
        return False

    sortable_dates = [item.sort_date for item in page_items if item.sort_date is not None]
    if not sortable_dates:
        return False

    oldest_on_page = min(sortable_dates)
    return oldest_on_page < start_date


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


def collect_prizes(
    keywords: list[str],
    start_date: date | None,
    end_date: date | None,
    delay: float,
) -> list[PrizeItem]:
    session = build_session()
    collected: list[PrizeItem] = []

    for keyword in keywords:
        page = 1
        while True:
            page_items = fetch_page(session, keyword, page)
            if not page_items:
                break

            matched = [item for item in page_items if in_period(item, start_date, end_date)]
            collected.extend(matched)

            if should_stop_early(page_items, start_date):
                break

            page += 1
            if delay > 0:
                time.sleep(delay)

    collected.sort(
        key=lambda item: (
            item.sort_date or date.min,
            item.name,
        ),
        reverse=True,
    )
    return collected


def render_html(items: list[PrizeItem], title: str) -> str:
    cards = []
    for item in items:
        sort_date_text = item.sort_date.isoformat() if item.sort_date else "不明"
        image_html = (
            f'<img src="{html.escape(item.image_url)}" alt="{html.escape(item.name)}">'
            if item.image_url
            else '<div class="no-image">No Image</div>'
        )
        cards.append(
            f"""
            <article class="card">
              <div class="thumb">{image_html}</div>
              <div class="meta">
                <p class="keyword">{html.escape(item.keyword)}</p>
                <h2>{html.escape(item.name)}</h2>
                <p class="date">{html.escape(item.date_text)}</p>
                <p class="sub">ソート用日付: {html.escape(sort_date_text)} / 取得ページ: {item.page}</p>
              </div>
            </article>
            """
        )

    body = "\n".join(cards) if cards else '<p class="empty">条件に一致するプライズは見つかりませんでした。</p>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f1e8;
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
    .keyword, .date, .sub, .empty {{
      margin: 0;
    }}
    .keyword {{
      color: var(--accent);
      font-weight: 700;
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
    <p class="summary">取得件数: {len(items)}件 / 新しい順に表示</p>
    <section class="grid">
      {body}
    </section>
  </main>
</body>
</html>
"""


def save_outputs(items: list[PrizeItem], html_path: Path, json_path: Path) -> None:
    html_path.write_text(
        render_html(items, "バンプレスト プライズ検索結果"),
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
        description="バンプレストの検索結果からプライズ情報を収集してHTML出力します。"
    )
    parser.add_argument(
        "--keyword",
        action="append",
        required=True,
        help="検索キーワード。複数指定したい場合は --keyword を繰り返してください。",
    )
    parser.add_argument(
        "--start-date",
        help="期間の開始日 (YYYY-MM-DD)。この日付以降のみ取得します。",
    )
    parser.add_argument(
        "--end-date",
        help="期間の終了日 (YYYY-MM-DD)。この日付以前のみ取得します。",
    )
    parser.add_argument(
        "--output-html",
        default="bsp_prizes.html",
        help="HTML出力先ファイル。",
    )
    parser.add_argument(
        "--output-json",
        default="bsp_prizes.json",
        help="JSON出力先ファイル。",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="ページ取得間隔（秒）。既定値: 0.5",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    start_date = parse_cli_date(args.start_date)
    end_date = parse_cli_date(args.end_date)
    if start_date and end_date and start_date > end_date:
        raise ValueError("start-date は end-date 以下にしてください。")

    keywords = [keyword.strip() for keyword in args.keyword if keyword.strip()]
    if not keywords:
        raise ValueError("少なくとも1つの keyword を指定してください。")

    items = collect_prizes(
        keywords=keywords,
        start_date=start_date,
        end_date=end_date,
        delay=args.delay,
    )

    html_path = Path(args.output_html)
    json_path = Path(args.output_json)
    save_outputs(items, html_path=html_path, json_path=json_path)

    print(f"取得件数: {len(items)}")
    print(f"HTML: {html_path.resolve()}")
    print(f"JSON: {json_path.resolve()}")
    if items:
        print(f"先頭データ: {items[0].name} / {items[0].date_text}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        raise
