"""What every page of the web interface shares: the templates and their helpers, and `Svc`,
the app's services in a route (`def page(request: Request, svc: Svc)`)."""

from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Row, func, select

from adacar import __version__
from adacar.db.models import Car, Run
from adacar.i18n import translate
from adacar.normalize.vocab import VOCABULARIES, label
from adacar.routing.commute import MAX_MINUTES, MODES
from adacar.scraper.sources import LABELS as SOURCE_LABELS
from adacar.search.service import TRAVEL_MODES
from adacar.services import Services

WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=WEB_DIR / "templates")
PAGE_SIZE = 30
# Colour scheme -> the Pico base it builds on (None: follows Windows light/dark).
THEMES = {
    "system": None,
    "light": "light",
    "dark": "dark",
    "ocean": "light",
    "forest": "light",
    "sunset": "light",
    "plum": "dark",
    "midnight": "dark",
}
POSTED_DAYS = (1, 3, 7, 14, 30)


def services_of(request: Request) -> Services:
    return request.app.state.services


Svc = Annotated[Services, Depends(services_of)]


def number(n: int | None) -> str:
    """ "1 234 567" with non-breaking spaces, as Czech writes thousands."""
    return f"{n:,}".replace(",", "\xa0") if n is not None else ""


def format_price(car: Car | Row, lang: str) -> str:
    if car.price is None:
        return translate(lang, "car.price_none")
    currency = "Kč" if car.price_currency == "CZK" else car.price_currency
    return f"{number(car.price)}\xa0{currency}"


def car_facts(car: Car, lang: str) -> list[str]:
    """The short facts a card shows: year, km, fuel, gearbox, power."""
    facts = [
        str(car.year) if car.year else "",
        f"{number(car.mileage_km)}\xa0km" if car.mileage_km is not None else "",
        label("fuels", car.fuel, lang),
        label("gearboxes", car.gearbox, lang),
        f"{car.power_kw}\xa0kW" if car.power_kw else "",
    ]
    return [f for f in facts if f]


def _safe_url(url: str | None) -> str:
    """A web address, or a picture the user added (routes/add.py)."""
    ok = url and (url.lower().startswith(("http://", "https://")) or url.startswith("/uploads/"))
    return url if url and ok else "#"


def _format_date(value: date | str | None) -> str:
    """A date, a time, or an ISO date as stored in JSON ("2026-11-18")."""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10]) if value else None
        except ValueError:
            return value  # not a date after all: shown as it came
    return f"{value.day}. {value.month}. {value.year}" if value else ""


def _format_datetime(value: datetime | None) -> str:
    return f"{_format_date(value)} {value:%H:%M}" if value else ""


def duration(minutes: float | None) -> str:
    """ "45 min", "2 h", "3 h 15 min" (trips across the country take hours)."""
    if minutes is None:
        return ""
    hours, rest = divmod(round(minutes), 60)
    if not hours:
        return f"{rest}\xa0min"
    return f"{hours}\xa0h {rest}\xa0min" if rest else f"{hours}\xa0h"


def local_time(utc: datetime) -> datetime:
    """A stored (naive UTC) time in this computer's time zone."""
    return datetime.fromtimestamp((utc - datetime(1970, 1, 1)).total_seconds())


TEMPLATES.env.filters["safe_url"] = _safe_url
TEMPLATES.env.filters["date"] = _format_date
TEMPLATES.env.filters["datetime"] = _format_datetime
TEMPLATES.env.filters["number"] = number
TEMPLATES.env.filters["duration"] = duration
TEMPLATES.env.filters["source_label"] = lambda source_id: SOURCE_LABELS.get(source_id, source_id)


def render(request: Request, name: str, **context) -> HTMLResponse:
    svc = services_of(request)
    settings = svc.settings
    lang = settings.language
    context |= {
        "settings": settings,
        "lang": lang,
        "theme_base": THEMES[settings.theme],
        "path": request.url.path,
        "version": __version__,
        "t": lambda key, **kw: translate(lang, key, **kw),
        "price": lambda car: format_price(car, lang),
        "facts": lambda car: car_facts(car, lang),
        "label": lambda vocab, code: label(vocab, code, lang),
        "vocab": VOCABULARIES,
        "commute_modes": MODES,
        "commute_limits": MAX_MINUTES,
        "travel_modes": TRAVEL_MODES,
        "commute_on": svc.travel.on(),
    }
    context.setdefault("times", {})  # travel times of the listed cars
    return TEMPLATES.TemplateResponse(request, name, context)


def query_string(request: Request) -> str:
    """The current filters as a query string, without paging."""
    paging = ("offset", "since", "cleared")
    return urlencode([(k, v) for k, v in request.query_params.multi_items() if k not in paging])


def status_context(svc: Services) -> dict:
    """The status line: cars, the last and the next search, the map."""
    with svc.sessions() as s:
        active = s.scalar(select(func.count(Car.id)).where(Car.status == "active")) or 0
        last = s.scalar(
            select(Run.finished_at)
            .where(Run.status.in_(("ok", "skipped")))
            .order_by(Run.id.desc())
            .limit(1)
        )
    next_check = svc.scheduler.next_check()
    paused = svc.settings.automation.paused_until if svc.scheduler.paused() else None
    return {
        "routing": svc.routing.status(),
        "active_cars": active,
        "last_run": local_time(last) if last else None,
        "next_check": local_time(next_check) if next_check else None,
        "paused_until": local_time(paused) if paused else None,
    }
