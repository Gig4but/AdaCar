"""The first-run setup wizard (PLAN §7.1); each step saves like its Settings page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from adacar.normalize.vocab import MAKES
from adacar.search.service import TRAVEL_MODES
from adacar.services import Services
from adacar.web.pages import THEMES, Svc, render, status_context
from adacar.web.routes.ai import ai_context, apply_ai_choice
from adacar.web.routes.settings import (
    apply_automation,
    map_context,
    save_cars,
    save_map,
    save_profile,
)
from adacar.web.routes.sources import apply_source_choices, source_rows

router = APIRouter()

STEPS = ("welcome", "profile", "cars", "ai", "sources", "automation", "map", "done")


def render_step(request: Request, svc: Services, step: str, **context) -> HTMLResponse:
    if step == "ai":
        context.setdefault("ai", ai_context(svc))
    if step == "sources":
        context.setdefault("sources", source_rows(svc))
    if step == "map":
        context |= map_context(svc) | status_context(svc)
    return render(
        request,
        "wizard.html",
        step=step,
        step_no=STEPS.index(step) + 1,
        step_total=len(STEPS),
        modes=TRAVEL_MODES,
        makes=MAKES,
        themes=THEMES,
        **context,
    )


@router.get("/wizard/{step}", response_class=HTMLResponse)
def wizard(request: Request, svc: Svc, step: str) -> Response:
    if step not in STEPS:
        return RedirectResponse("/wizard/welcome", status_code=303)
    return render_step(request, svc, step)


@router.post("/wizard/profile")
async def wizard_profile(request: Request, svc: Svc) -> RedirectResponse:
    save_profile(svc, await request.form())
    return RedirectResponse("/wizard/cars", status_code=303)


@router.post("/wizard/cars")
async def wizard_cars(request: Request, svc: Svc) -> RedirectResponse:
    save_cars(svc, await request.form())
    return RedirectResponse("/wizard/ai", status_code=303)


@router.post("/wizard/ai")
async def wizard_ai(request: Request, svc: Svc) -> RedirectResponse:
    apply_ai_choice(svc.settings, await request.form())
    svc.ai.manager.start_download()  # in the background while the wizard continues
    return RedirectResponse("/wizard/sources", status_code=303)


@router.post("/wizard/sources", response_class=HTMLResponse)
async def wizard_sources(request: Request, svc: Svc) -> Response:
    form = await request.form()
    missing = apply_source_choices(svc.settings, form)
    if missing:
        rows = source_rows(svc, form)
        return render_step(request, svc, "sources", sources=rows, consent_missing=missing)
    await run_in_threadpool(svc.runner.scope_changed)
    return RedirectResponse("/wizard/automation", status_code=303)


@router.post("/wizard/automation")
async def wizard_automation(request: Request, svc: Svc) -> RedirectResponse:
    form = await request.form()
    apply_automation(svc.settings, form)
    svc.set_auto_update(form.get("auto_update") == "on")
    if svc.settings.wizard_completed:  # the wizard again: as if saved in Settings
        svc.set_automation()
    return RedirectResponse("/wizard/map", status_code=303)


@router.post("/wizard/map")
async def wizard_map(request: Request, svc: Svc) -> RedirectResponse:
    """ "Stáhnout mapu" starts the download and stays on the step to show its progress."""
    form = await request.form()
    save_map(svc, form)
    if form.get("download"):
        return RedirectResponse("/wizard/map", status_code=303)
    return RedirectResponse("/wizard/done", status_code=303)


@router.post("/wizard/done")
def wizard_done(svc: Svc) -> RedirectResponse:
    svc.settings.wizard_completed = True
    svc.settings.save()
    svc.runner.start()
    svc.set_automation()  # the next automatic check counts from this search
    return RedirectResponse("/", status_code=303)
