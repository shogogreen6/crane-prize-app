from django.shortcuts import render

from bsp_prize_scraper import ProgressReporter, collect_prizes

from .forms import PrizeSearchForm


def index(request):
    form = PrizeSearchForm(request.GET or None)
    items = []
    searched = False
    error_message = None

    if "keywords" in request.GET:
        searched = True
        if form.is_valid():
            try:
                items = collect_prizes(
                    keywords=form.cleaned_data["keywords"],
                    start_date=form.cleaned_data["start_date"],
                    end_date=form.cleaned_data["end_date"],
                    delay=0.5,
                    sites=["bsp"],
                    reporter=ProgressReporter(enabled=False),
                )
            except Exception as exc:
                error_message = str(exc)

    return render(
        request,
        "prizes/index.html",
        {
            "form": form,
            "items": items,
            "searched": searched,
            "error_message": error_message,
        },
    )
