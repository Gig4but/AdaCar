"""Saved searches."""

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import QueryParams

from adacar.core import utcnow
from adacar.db.models import SavedSearch
from adacar.i18n import translate
from adacar.search.saved import MAX_SAVED, newest_car_id, with_new_counts
from adacar.search.service import filters_from
from adacar.services import Services
from adacar.web.pages import Svc, render

router = APIRouter()


def render_saved(request: Request, svc: Services, **context) -> HTMLResponse:
    with svc.sessions() as s:
        rows = with_new_counts(s, svc.travel.prepare)
    return render(request, "_saved.html", saved=rows, max_saved=MAX_SAVED, **context)


@router.get("/saved", response_class=HTMLResponse)
def saved_list(request: Request, svc: Svc) -> HTMLResponse:
    return render_saved(request, svc)


@router.post("/saved", response_class=HTMLResponse)
async def save_search(request: Request, svc: Svc) -> HTMLResponse:
    """The search form's filters under the name the user typed."""
    form = await request.form()
    defaults = svc.travel.defaults()
    f = filters_from(form, defaults)
    skip = ("view", "offset", "since", "cleared", "shown", "more", "saved_name")
    # Empty text, makes, price and start point are kept: without them, opening would use the
    # profile's. Sent before the filter panel loaded: the list showed the profile's makes and
    # price, so those are saved.
    keep = ("q", "make", "price_max", "at")
    items = form.multi_items()
    items += [(k, v) for k in ("make", "price_max") if k not in form
              for v in defaults.get(k, "").split(",")]  # fmt: skip
    query = urlencode([(k, v) for k, v in items if (v or k in keep) and k not in skip])
    name = str(form.get("saved_name") or "").strip() or f.q.strip()
    name = name[:100] or translate(svc.settings.language, "saved.default_name")
    with svc.sessions() as s:
        full = (s.scalar(select(func.count(SavedSearch.id))) or 0) >= MAX_SAVED
        if not full:
            seen = newest_car_id(s)
            s.add(SavedSearch(name=name, query=query, seen_up_to=seen, created_at=utcnow()))
            s.commit()
    # Counting may store travel times: not on the event loop.
    extra = {"full": True} if full else {}
    return await run_in_threadpool(render_saved, request, svc, **extra)


@router.get("/saved/{saved_id}")
def open_saved(svc: Svc, saved_id: int) -> RedirectResponse:
    """The saved filters on the search page; their matches stop counting as new."""
    with svc.sessions() as s:
        saved = s.get(SavedSearch, saved_id)
        if saved is None:
            return RedirectResponse("/", status_code=303)
        saved.seen_up_to = newest_car_id(s)
        s.commit()
        query = saved.query
    params = QueryParams(query)
    # A filter the saved search lacks stays off: not the profile's makes, price or start.
    missing = "&".join(f"{k}=" for k in ("q", "make", "price_max", "at") if k not in params)
    query = "&".join(p for p in (query, missing) if p)
    return RedirectResponse(f"/?{query}", status_code=303)


@router.post("/saved/{saved_id}/notify", response_class=HTMLResponse)
def toggle_saved_notify(request: Request, svc: Svc, saved_id: int) -> HTMLResponse:
    with svc.sessions() as s:
        saved = s.get(SavedSearch, saved_id)
        if saved is not None:
            saved.notify = not saved.notify
            s.commit()
    return render_saved(request, svc, open_list=True)


@router.post("/saved/{saved_id}/delete", response_class=HTMLResponse)
def delete_saved(request: Request, svc: Svc, saved_id: int) -> HTMLResponse:
    with svc.sessions() as s:
        saved = s.get(SavedSearch, saved_id)
        if saved is not None:
            s.delete(saved)
            s.commit()
    return render_saved(request, svc, open_list=True)
