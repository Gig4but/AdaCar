"""The search page: the car list with its filters, new finds while a search runs, hidden
ads, and starting a search."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from adacar.core import utcnow
from adacar.db.models import Car, table
from adacar.normalize.vocab import VOCABULARIES
from adacar.scraper.check import unchecked
from adacar.scraper.sources import SWITCHABLE
from adacar.search.service import (
    batch_starts,
    cars_found_since,
    count_between,
    facet_counts,
    filters_from,
    new_after,
    search_cars,
    still_matching,
)
from adacar.services import Services
from adacar.web.pages import PAGE_SIZE, POSTED_DAYS, Svc, query_string, render, status_context
from adacar.web.routes.ai import ai_context

router = APIRouter()


def hidden_count(s: Session) -> int:
    stmt = select(func.count(Car.id)).where(Car.hidden_at.is_not(None), Car.status == "active")
    return s.scalar(stmt) or 0


def newest_id(svc: Services, s: Session) -> int | None:
    """While a search runs: the last stored car, from which the list asks for new ones.
    While a search starts over (the cars found before are deleted), none is listed (0)."""
    p = svc.runner.progress
    if not p.running:
        return None
    if p.restarting or p.phase == "clear":
        return 0
    return s.scalar(select(func.max(Car.id))) or 0


def unchecked_of(svc: Services, s: Session, car_ids) -> set[int]:
    """While a refresh checks the ads found before: those of the cars not checked yet."""
    p = svc.runner.progress
    if not (p.running and p.checked_since and p.checking and car_ids):
        return set()
    return unchecked(s, p.checking, p.checked_since, car_ids)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, svc: Svc) -> Response:
    if not svc.settings.wizard_completed:
        return RedirectResponse("/wizard/welcome", status_code=303)
    f = svc.travel.filters(request.query_params)
    svc.runner.focus = f  # a refresh checks the ads of this list first
    with svc.sessions() as s:
        since = newest_id(svc, s)
        result = search_cars(s, f, limit=PAGE_SIZE, up_to_id=since)
        hidden = hidden_count(s)
        times = svc.travel.times(s, f, result.cars)
        marks = unchecked_of(svc, s, [j.id for j in result.cars])
        fresh_from = new_after(s)
    return render(
        request,
        "search.html",
        times=times,
        unchecked=marks,
        new_after=fresh_from,
        commute=svc.travel.state(f),
        hidden=hidden,
        f=f,
        qs=query_string(request),
        result=result,
        since=since,
        offset=0,
        progress=svc.runner.progress,
        posted_days=POSTED_DAYS,
        view="map" if request.query_params.get("view") == "map" else "list",
        ai=ai_context(svc),
        **status_context(svc),
    )


@router.get("/results", response_class=HTMLResponse)
def results(request: Request, svc: Svc, offset: int = 0) -> HTMLResponse:
    f = svc.travel.filters(request.query_params)
    svc.runner.focus = f
    with svc.sessions() as s:
        since = newest_id(svc, s) if offset == 0 else None
        result = search_cars(s, f, limit=PAGE_SIZE, offset=offset, up_to_id=since)
        hidden = hidden_count(s)
        times = svc.travel.times(s, f, result.cars)
        marks = unchecked_of(svc, s, [j.id for j in result.cars])
        fresh_from = new_after(s)
    qs = query_string(request)
    response = render(
        request,
        "_results.html",
        times=times,
        unchecked=marks,
        new_after=fresh_from,
        commute=svc.travel.state(f),
        hidden=hidden,
        f=f,
        qs=qs,
        result=result,
        since=since,
        progress=svc.runner.progress,
        offset=offset,
    )
    if offset == 0:  # keep the filters in the address, so a reload keeps them
        response.headers["HX-Push-Url"] = f"/?{qs}" if qs else "/"
    return response


@router.post("/results/new", response_class=HTMLResponse)
async def new_results(
    request: Request, svc: Svc, since: int = 0, cleared: bool = False
) -> Response:
    """While a search runs: drops the listed cars that no longer match (expired, merged)
    and adds the new finds where they belong in the order found, after this search's
    earlier finds and above the cars found before. With another order the list reloads.

    `shown`: the ids listed, in order; `more`: the list ends with a "more" button."""
    p = svc.runner.progress
    if not p.running:
        return HTMLResponse("")  # the finished run reloads the list
    if p.cleared != cleared:
        return HTMLResponse("", headers={"HX-Trigger": "cars-updated"})
    form = await request.form()
    shown = [int(v) for v in str(form.get("shown", "")).split(",")[:5000] if v.isdigit()]
    has_more = form.get("more") == "1"

    def found(since: int) -> Response:  # off the event loop: a hide is not held up
        f = svc.travel.filters(request.query_params)  # may store travel times
        with svc.sessions() as s:
            newest = newest_id(svc, s) or 0
            still = still_matching(s, f, shown) if shown else set()
            listed = [i for i in shown if i in still]
            marks = unchecked_of(svc, s, listed)
            cars, total = cars_found_since(s, f, since, newest, PAGE_SIZE + 1, skip=listed)
            times = svc.travel.times(s, f, cars)
            if cars and not f.new_first():
                return HTMLResponse("", headers={"HX-Trigger": "cars-updated"})
            batch = (batch_starts(s) or [0])[-1]
            fresh_from = new_after(s)
            older = next((i for i in listed if i <= batch), None)
            if older is None and cars:
                # This search's finds continue on pages not loaded: the new ones go there too.
                loaded = sum(1 for i in listed if batch < i <= since)
                if count_between(s, f, batch, since) > loaded:
                    cars = []
        if older is not None:  # above the cars found before, a page at a time
            where, add_more = f"beforebegin:#car-{older}", False
            since = cars[PAGE_SIZE - 1].id if len(cars) > PAGE_SIZE else newest
            cars = cars[:PAGE_SIZE]
        else:  # at the end, filling the loaded pages; the rest is behind "more"
            pages = max(1, -(-len(listed) // PAGE_SIZE))
            room = max(0, pages * PAGE_SIZE - len(listed))
            where = "beforebegin:#car-list > .more" if has_more else "beforeend:#car-list"
            add_more = len(cars) > room and not has_more
            since, cars = newest, cars[:room]
        return render(
            request,
            "_new_results.html",
            times=times,
            cars=cars,
            gone=[i for i in shown if i not in still],
            where=where,
            add_more=add_more,
            total=total,
            qs=query_string(request),
            since=since,
            progress=p,
            unchecked=marks,
            new_after=fresh_from,
        )

    return await run_in_threadpool(found, since)


@router.post("/cars/{car_id}/hide", response_class=HTMLResponse)
async def hide_car(request: Request, svc: Svc, car_id: int) -> Response:
    """Hides an ad from the list; the hidden bar brings it back."""
    form = await request.form()  # the search form, when hidden from the list

    def hide() -> Response:  # off the event loop: the write may wait for a scrape's
        with svc.sessions() as s:
            if s.get(Car, car_id) is None:
                return Response(status_code=404)
            # Not through the ORM: a mark does not change what the filter counts cache holds.
            s.execute(update(table(Car)).where(Car.id == car_id).values(hidden_at=utcnow()))
            s.commit()
            total = None
            if "sort" in form:
                f = filters_from(form, svc.travel.defaults())
                total = search_cars(s, f, limit=0, up_to_id=newest_id(svc, s)).total
            hidden = hidden_count(s)
        return render(request, "_hidden.html", hidden=hidden, total=total, oob=True)

    return await run_in_threadpool(hide)


@router.post("/hidden/restore", response_class=HTMLResponse)
def restore_hidden(request: Request, svc: Svc, every: bool = False) -> HTMLResponse:
    """Brings back the ad hidden last (again and again: the one before), or all."""
    with svc.sessions() as s:
        ids = select(Car.id).where(Car.hidden_at.is_not(None), Car.status == "active")
        if not every:
            ids = ids.order_by(Car.hidden_at.desc(), Car.id.desc()).limit(1)
        chosen = list(s.scalars(ids))
        s.execute(update(table(Car)).where(Car.id.in_(chosen)).values(hidden_at=None))
        s.commit()
        hidden = hidden_count(s)
    response = render(request, "_hidden.html", hidden=hidden)
    response.headers["HX-Trigger"] = "cars-updated"  # the ads return to their places
    return response


@router.get("/facets", response_class=HTMLResponse)
def facets(request: Request, svc: Svc) -> HTMLResponse:
    """Filter panel with counts; loaded after the results so they appear first."""
    f = svc.travel.filters(request.query_params)
    with svc.sessions() as s:
        counts = facet_counts(s, f)
    settings = svc.settings
    return render(
        request,
        "_facets.html",
        f=f,
        sources_on=[sid for sid, on in SWITCHABLE if settings.sources.get(sid, on)],
        facets=counts,
        facet_vocab={
            "make": "makes",
            "fuel": "fuels",
            "gearbox": "gearboxes",
            "body": "bodies",
            "drive": "drives",
            "seller_kind": "seller_kinds",
        },
        all_makes=VOCABULARIES["makes"],
        posted_days=POSTED_DAYS,
    )


@router.get("/cars/{car_id}", response_class=HTMLResponse)
def car_detail(request: Request, svc: Svc, car_id: int) -> Response:
    """A car with its whole text: in the search page's dialog, or as a page of its own."""
    with svc.sessions() as s:
        car = s.get(Car, car_id)
        if car is None:
            return Response(status_code=404)
        _ = car.ads, car.images, car.location  # loaded for the template
    dialog = request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
    return render(request, "_car_detail.html" if dialog else "car.html", car=car, dialog=dialog)


@router.post("/scrape", response_class=HTMLResponse)
def scrape(request: Request, svc: Svc, fresh: bool = False) -> HTMLResponse:
    """A refresh: the cars found so far are checked and new ones added (not again while one
    runs). `fresh`: they are deleted and found again; a running search stops for it."""
    if fresh:
        svc.runner.start_over()
    else:
        svc.runner.start()
    response = render(request, "_progress.html", progress=svc.runner.progress)
    response.headers["HX-Trigger"] = "cars-updated"  # the list starts showing new finds
    return response


@router.get("/scrape/progress", response_class=HTMLResponse)
def scrape_progress(request: Request, svc: Svc) -> HTMLResponse:
    progress = svc.runner.progress
    response = render(request, "_progress.html", progress=progress)
    if not progress.running and progress.phase == "done":
        response.headers["HX-Trigger"] = "cars-updated"
    return response


@router.get("/status", response_class=HTMLResponse)
def status(request: Request, svc: Svc) -> HTMLResponse:
    return render(request, "_status.html", **status_context(svc))
