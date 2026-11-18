"""The Ministry of Transport's vehicle register by VIN (PLAN §2.4, §4.5), through its public
API with the user's own free key (dataovozidlech.cz/registraceapi): first registration,
first registration in Czechia, owners, status, the make and engine on record.

The register has no mileage readings; those are on kontrolatachometru.cz behind a CAPTCHA,
so the card links there. The API allows 27 calls a minute per key.
"""

import re
import threading
import time
from datetime import date
from typing import Any

import httpx

from adacar.normalize.text import fold
from adacar.normalize.vocab import make_code

API = "https://api.dataovozidlech.cz/api/vehicletechnicaldata/v2"
SIGN_UP = "https://dataovozidlech.cz/registraceapi"
GAP = 60 / 25  # seconds between calls: under the 27 a minute, with some room
IMPORTED_AFTER = 90  # days between the first registration and the one in Czechia
MANY_OWNERS = 3  # "první majitel" against this many owners on record is a warning
POWER_SLACK = 0.05  # kW on record vs. the ad: a rounding, not another engine


class BadKey(Exception):
    """The register refused the key."""


class TooFast(Exception):
    """The register's limit of calls a minute was reached."""


class Register:
    # One gap for every caller: the background pass and opened cards share the key.
    _last = 0.0
    _turn = threading.Lock()

    def __init__(self, client: httpx.Client, key: str, sleep=time.sleep, clock=time.monotonic):
        self.client, self.key = client, key
        self.sleep, self.clock = sleep, clock

    def lookup(self, vin: str) -> dict[str, Any] | None:
        """The vehicle's facts (`facts`); None when the register has no such VIN."""
        with Register._turn:
            wait = Register._last + GAP - self.clock()
            if wait > 0:
                self.sleep(wait)
            Register._last = self.clock()
        resp = self.client.get(API, params={"vin": vin}, headers={"API_KEY": self.key}, timeout=30)
        if resp.status_code in (401, 403):
            raise BadKey(resp.text[:200])
        if resp.status_code == 429 or "maximální počet" in resp.text:
            raise TooFast(resp.text[:200])
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.json()
        data = body.get("Data") if isinstance(body, dict) else None
        return facts(data) if body.get("Status") == 1 and isinstance(data, dict) else None


def _day(value) -> str | None:
    """An ISO date of the register's ("2016-03-21T00:00:00"); None for anything else."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _count(value) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _yes(value) -> bool:
    """The register's flags are booleans; a text "ANO" is a yes too, "NE" a no."""
    return value is True or (isinstance(value, str) and fold(value).strip() in ("ano", "true"))


def facts(data: dict) -> dict[str, Any]:
    """What the card shows and the checks compare, from the register's record. The record
    is outside data: a field of an unexpected type becomes empty, never an error."""
    power = re.match(r"\s*(\d+(?:[.,]\d+)?)", _text(data.get("MotorMaxVykon")))
    return {
        "first_registration": _day(data.get("DatumPrvniRegistrace")),
        "first_registration_cz": _day(data.get("DatumPrvniRegistraceVCr")),
        "make": _text(data.get("TovarniZnacka")),
        "model": _text(data.get("ObchodniOznaceni")),
        "power_kw": round(float(power[1].replace(",", "."))) if power else None,
        "fuel": _text(data.get("Palivo")),
        "color": _text(data.get("VozidloKaroserieBarva")),
        "owners": _count(data.get("PocetVlastniku")),
        "operators": _count(data.get("PocetProvozovatelu")),
        "status": _text(data.get("StatusNazev")),
        "stk_until": _day(data.get("PravidelnaTechnickaProhlidkaDo")),
        "seized": _yes(data.get("OrvZadrzeno")) or _yes(data.get("RzZadrzena")),
    }


def imported_on(f: dict) -> str | None:
    """When the car came to Czechia, if it was registered abroad first."""
    first, here = _day(f.get("first_registration")), _day(f.get("first_registration_cz"))
    if not first or not here:
        return None
    days = (date.fromisoformat(here) - date.fromisoformat(first)).days
    return here if days > IMPORTED_AFTER else None


# "Koupeno v ČR", "zakoupeno nové v CZ", "koupeno CZ", "původ: Česká republika", "vůz je
# z ČR", "původem z ČR" (in folded text).
_HERE = r"(?:cr|cz|cesku|ceske republice|ceska republika|ceske republiky)\b"
BOUGHT_HERE = re.compile(
    rf"\b(?:za)?koupen\w*(?: nov\w*)?(?: v| ve)? {_HERE}"
    rf"|\bpuv(?:od|\.)?\W{{1,3}}{_HERE}|\bpuvodem z {_HERE}|\b(?:vuz|auto) je z {_HERE}"
)
# Before the claim, a sentence that says the opposite ("nejedná se o auto koupené v ČR").
_NOT_BOUGHT = re.compile(r"\b(?:nejedna|neni|nebyl\w*|nikoli)\b[^.,;!?]*$")


def said_bought_here(origin_country: str | None, text: str) -> bool:
    """The ad says the car was bought in Czechia (`text` folded). Only a negation of this
    claim counts against it: "nehavarované, původ ČR" still says it."""
    if origin_country and fold(origin_country) in ("ceska republika", "cesko", "cr"):
        return True
    return any(
        not _NOT_BOUGHT.search(text[max(0, m.start() - 40) : m.start()])
        for m in BOUGHT_HERE.finditer(text)
    )


SAME_MAKE = {"cupra": "seat"}  # early Cupras are registered as Seats
NO_POWER_CHECK = ("hybrid", "plugin_hybrid", "electric")  # system power vs. the engine's


def mismatches(f: dict, car) -> list[str]:
    """Warning codes where the register disagrees with the ad (`car`: a Car or a row of
    its columns)."""
    found = []
    if f.get("seized"):
        found.append("reg_seized")
    status = fold(f.get("status") or "")
    if status and not status.startswith(("provozovan", "docasne")):
        found.append("reg_status")  # e.g. "zaniklé", "vyřazené z provozu" for good
    make = make_code(f.get("make"))
    named = {make_code(car.title), car.make}
    if make and car.make and SAME_MAKE.get(make, make) not in {SAME_MAKE.get(m, m) for m in named}:
        found.append("reg_make")
    first = _day(f.get("first_registration"))
    if first and car.year and car.year > int(first[:4]) + 1:
        found.append("reg_year")  # the ad makes the car newer than its first registration
    kw = _count(f.get("power_kw"))
    other_power = kw and car.power_kw and abs(kw - car.power_kw) > max(2, kw * POWER_SLACK)
    if other_power and car.fuel not in NO_POWER_CHECK:
        found.append("reg_power")
    text = fold(f"{car.title}\n{car.description}")
    if imported_on(f) and said_bought_here(car.origin_country, text):
        found.append("reg_imported")
    owners = _count(f.get("owners"))
    if car.first_owner and owners and owners >= MANY_OWNERS:
        found.append("reg_owners")
    return found
