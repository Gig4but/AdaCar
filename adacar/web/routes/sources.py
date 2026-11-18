"""The Sources page: which sources are read, and consent for the risky ones."""

from collections.abc import Collection

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from adacar.config import Settings
from adacar.scraper.sources import KINDS, LABELS, NEEDS_CONSENT, SWITCHABLE
from adacar.services import Services
from adacar.web.pages import Svc, render

router = APIRouter()


def chosen_sources(form) -> dict[str, bool]:
    return {source_id: form.get(f"source_{source_id}") == "on" for source_id, _ in SWITCHABLE}


def apply_source_choices(
    settings: Settings, form, shown: Collection[str] | None = None
) -> str | None:
    """Saves the source switches (`shown`: only these; the others keep theirs); returns the
    label of a source that needs consent (sauto.cz) and was switched on without ticking its
    confirmation box."""
    chosen = chosen_sources(form)
    if shown is not None:
        chosen = {
            source_id: chosen[source_id]
            if source_id in shown
            else settings.sources.get(source_id, on)
            for source_id, on in SWITCHABLE
        }
    for source_id, _ in SWITCHABLE:
        newly_on = chosen[source_id] and not settings.sources.get(source_id, False)
        if source_id in NEEDS_CONSENT and newly_on and form.get(f"consent_{source_id}") != "on":
            return LABELS[source_id]
    settings.sources = chosen
    settings.save()
    return None


def source_rows(svc: Services, form=None) -> list[dict]:
    settings = svc.settings
    choices = settings.sources if form is None else chosen_sources(form)
    return [
        {
            "id": source_id,
            "label": LABELS[source_id],
            "on": choices.get(source_id, default),
            "consented": settings.sources.get(source_id, False)
            or (form is not None and form.get(f"consent_{source_id}") == "on"),
            "consent": source_id in NEEDS_CONSENT,
            "kind": KINDS[source_id],
        }
        for source_id, default in SWITCHABLE
    ]


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, svc: Svc, saved: bool = False) -> HTMLResponse:
    return render(request, "sources.html", sources=source_rows(svc), saved=saved)


@router.post("/sources", response_class=HTMLResponse)
async def sources_save(request: Request, svc: Svc) -> Response:
    form = await request.form()
    missing = apply_source_choices(svc.settings, form)
    if missing:
        rows = source_rows(svc, form)
        return render(request, "sources.html", sources=rows, consent_missing=missing)
    await run_in_threadpool(svc.runner.scope_changed)
    return RedirectResponse("/sources?saved=1", status_code=303)
