"""Vehicle and seller checks (PLAN §4.5): a car's check section and its dealer's card in the
car's detail, loaded after it, and Settings → Prověrky with the user's keys."""

import logging
import re
from typing import Annotated
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Form, Request
from fastapi import Path as InPath
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from adacar.checks import official, register, vin
from adacar.checks.google import FREE_DETAILS
from adacar.checks.register import imported_on
from adacar.checks.service import GOOGLE_KEY, REGISTER_KEY
from adacar.db.models import Car, Dealer, VehicleCheck
from adacar.i18n import translate
from adacar.normalize.vocab import label
from adacar.services import Services
from adacar.web.pages import Svc, render

log = logging.getLogger(__name__)
router = APIRouter()

Id = Annotated[int, InPath(ge=1, le=2**63 - 1)]  # larger: not an SQLite integer
API_KEY = re.compile(r"[\w.:-]{8,200}", re.ASCII)
KEYS = {
    REGISTER_KEY: ("checks.key.register", register.SIGN_UP),
    GOOGLE_KEY: (
        "checks.key.google",
        "https://developers.google.com/maps/documentation/places/web-service/get-api-key",
    ),
}
# Where a buyer checks a VIN by hand: these sites ask for a CAPTCHA, so the app cannot.
VIN_LINKS = {
    "km": "https://kontrolatachometru.cz/",
    "stolen": "https://aplikace.policie.cz/patrani-vozidla/",
    "register": "https://dataovozidlech.cz/vyhledavani",
}


# --- a car's checks ---------------------------------------------------------------------------


@router.get("/cars/{car_id}/checks", response_class=HTMLResponse)
def car_checks(request: Request, svc: Svc, car_id: Id, refresh: bool = False) -> HTMLResponse:
    """The VIN section: what the register and the app's own history say. The register is
    asked now when its record is missing or 30 days old (with the user's key)."""
    try:
        failed = not svc.checks.check_car(car_id, force=refresh)
    except Exception:
        log.exception("check of car %s failed", car_id)
        failed = True
    with svc.sessions() as s:
        car = s.get(Car, car_id)
        code = vin.clean(car.vin) if car else None
        record = s.get(VehicleCheck, code) if code else None
    if car is None:
        return HTMLResponse("")
    decoded = vin.decode(code, car.year or 2015) if code else None
    lang = svc.settings.language
    makes = sorted(label("makes", m, lang) for m in decoded.makes) if decoded else []
    reg = record.register if record else {}
    return render(
        request,
        "_vehicle_check.html",
        car=car,
        code=code,
        decoded=decoded,
        vin_makes=" / ".join(makes),
        record=record,
        reg=reg,  # not `facts`: render() has a global of that name
        imported=imported_on(reg) if reg else None,
        has_key=bool(svc.secrets.get(REGISTER_KEY)),
        bad_key=svc.checks.progress.message == "checks.register.bad_key",
        failed=failed,
        links=VIN_LINKS,
        codes=car.check_warnings or [],
    )


# --- a dealer's card ----------------------------------------------------------------------------


def dealer_links(d: Dealer) -> dict[str, str]:
    q = quote_plus(d.name)
    links = {
        "google": d.maps_url or f"https://www.google.com/maps/search/?api=1&query={q}",
        "mapy": f"https://mapy.com/cs/zakladni?q={q}",
        "firmy": f"https://www.firmy.cz/?q={q}",
    }
    if d.ico:
        links["ares"] = f"https://ares.gov.cz/ekonomicke-subjekty?ico={d.ico}"
        links["justice"] = f"https://or.justice.cz/ias/ui/rejstrik-$firma?ico={d.ico}"
    isir = next((p["url"] for p in d.insolvency or [] if p.get("url")), None)
    if isir and str(isir).lower().startswith(("https://", "http://")):
        links["isir"] = isir
    return links


def dealer_card(request: Request, svc: Services, dealer_id: int, **extra) -> HTMLResponse:
    with svc.sessions() as s:
        d = s.get(Dealer, dealer_id)
        cars = s.scalar(
            select(func.count(Car.id)).where(Car.dealer_id == dealer_id, Car.status == "active")
        )
    if d is None:
        return HTMLResponse("")
    return render(
        request,
        "_dealer.html",
        d=d,
        cars=cars or 0,
        links=dealer_links(d),
        isir_state=official.insolvency_state,
        google_key=bool(svc.secrets.get(GOOGLE_KEY)),
        **extra,
    )


def _ask(svc: Services, action, *args) -> bool:
    """A register asked for an opened card; False when it could not be reached or answered
    something unexpected (the card shows what it has)."""
    try:
        action(*args)
    except (httpx.HTTPError, ValueError) as exc:
        log.info("dealer check failed: %s", exc)
        return False
    except Exception:
        log.exception("dealer check failed")
        return False
    return True


@router.get("/dealers/{dealer_id}", response_class=HTMLResponse)
def dealer_page(request: Request, svc: Svc, dealer_id: Id) -> HTMLResponse:
    """What is missing or 30 days old is asked now."""
    ok = _ask(svc, svc.checks.check_dealer, dealer_id)
    return dealer_card(request, svc, dealer_id, failed=not ok)


@router.post("/dealers/{dealer_id}/refresh", response_class=HTMLResponse)
def dealer_refresh(request: Request, svc: Svc, dealer_id: Id) -> HTMLResponse:
    ok = _ask(svc, svc.checks.check_dealer, dealer_id, True)
    return dealer_card(request, svc, dealer_id, failed=not ok)


@router.post("/dealers/{dealer_id}/correct", response_class=HTMLResponse)
def dealer_correct(
    request: Request, svc: Svc, dealer_id: Id, ico: Annotated[str, Form()] = ""
) -> HTMLResponse:
    """The user's pick of the company behind the dealer, by IČO."""
    ico = re.sub(r"\s", "", ico)
    problem: list[str | None] = [None]

    def correct() -> None:
        problem[0] = svc.checks.correct_dealer(dealer_id, ico)

    ok = _ask(svc, correct)
    return dealer_card(request, svc, dealer_id, problem=problem[0] if ok else None, failed=not ok)


# --- Settings → Prověrky -----------------------------------------------------------------------


def checks_summary(svc: Services) -> str:
    lang = svc.settings.language
    keys = [translate(lang, KEYS[k][0]) for k in KEYS if svc.secrets.get(k)]
    auto = translate(lang, "checks.auto_on" if svc.settings.checks.auto else "checks.auto_off")
    return " · ".join([auto, *keys]) if keys else f"{auto} · {translate(lang, 'checks.no_keys')}"


def checks_context(svc: Services) -> dict:
    with svc.sessions() as s:
        vins = s.scalar(select(func.count()).select_from(VehicleCheck)) or 0
        read = s.scalar(
            select(func.count()).where(VehicleCheck.register_status.is_not(None))
        )  # fmt: skip
        dealers = s.scalar(select(func.count()).select_from(Dealer)) or 0
        resolved = s.scalar(select(func.count()).where(Dealer.ico.is_not(None))) or 0
    keys = [
        {"id": k, "label": label, "url": url, "end": (svc.secrets.get(k) or "")[-4:]}
        for k, (label, url) in KEYS.items()
    ]
    return {
        "keys": keys,
        "counts": {"vins": vins, "read": read or 0, "dealers": dealers, "resolved": resolved},
        "progress": svc.checks.progress,
        "google_used": svc.checks.dealers.google_quota().details,
        "google_free": FREE_DETAILS,
    }


@router.get("/checks", response_class=HTMLResponse)
def checks_page(request: Request, svc: Svc, saved: bool = False, invalid: str = "") -> HTMLResponse:
    context = checks_context(svc)
    return render(request, "checks.html", saved=saved, invalid=invalid in KEYS, **context)


@router.post("/checks")
async def checks_save(request: Request, svc: Svc) -> RedirectResponse:
    """The background switch and the keys: a typed key replaces the saved one, a ticked
    "remove" deletes it; a key that cannot be one saves nothing."""
    form = await request.form()
    typed = {k: str(form.get(f"key_{k}") or "").strip() for k in KEYS}
    for k, key in typed.items():
        if key and not API_KEY.fullmatch(key):
            return RedirectResponse(f"/checks?invalid={k}", status_code=303)
    for k, key in typed.items():
        if form.get(f"remove_{k}") == "on":
            svc.secrets.delete(k)
        elif key:
            svc.secrets.set(k, key)
    svc.settings.checks.auto = form.get("auto") == "on"
    svc.settings.save()
    svc.checks.progress.message = ""  # a new key gets its chance
    svc.checks.wake()
    return RedirectResponse("/checks?saved=1", status_code=303)
