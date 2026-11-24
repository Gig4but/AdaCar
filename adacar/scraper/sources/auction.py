"""What the auction sources share: an auction's time in UTC, the model named after the make in
a lot's title ("Dražba vozidla AUDI A4"), car facts as auction notices write them, and lots
that are not passenger cars."""

import re
from datetime import date, datetime, time, timedelta

from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_end

_DATE_TIME = re.compile(
    r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})(?:\s*,?\s*(?:v|od|ve)?\s*(\d{1,2})[:.](\d{2})(?!\d))?"
)
# Lots in car categories that are no passenger car: motorcycles, trailers, trucks, vans
# (by kind or by a van's model name), machines.
_NOT_CAR = re.compile(
    r"(?<![a-z])(?:motocyk\w*|motork\w*|skutr\w*|ctyrkolk\w*|privesn?\w*|naves\w*|nakladni"
    r"|tahac\w*|traktor\w*|autobus\w*|karavan\w*|sklapec\w*|valnik\w*|dodavk\w*|dodavkov\w*"
    r"|skrin\w*|uzitkov\w*|pracovni stroj\w*|bagr\w*|rypadl\w*|nakladac\w*|vysokozdviz\w*"
    r"|ducato|jumper|jumpy|peugeot\W+boxer|peugeot\W+expert|renault\W+master|movano|vivaro"
    r"|trafic|sprinter|crafter|transit|transporter|iveco|avia"
    r"|tatra|motorovych vozidel|v poctu \d+ kus\w*)(?![a-z])"
)

# Notices ("dražební vyhláška") write "datum 1. registrace vozidla: 20.11.2018", "Rok
# výroby: 1.6.2018", "přečtený stav počítadla ujetých kilometrů: 210 330 km".
_NOTICE_YEAR = re.compile(
    r"(?:rok vyroby(?: vozidla)?|registrac\w*(?: vozidla)?|evid\w*|vyrob\w*)\W{0,3}"
    r"(?:\d{1,2}\.\s*){0,2}(?:\d{1,2}/)?((?:19|20)\d\d)(?!\d)"
)
_IMPORTED = re.compile(
    r"(?i)(?:dovo[zž]|dovez|v[yý]vo[zž]|v[yý]vez)\w*\W+(?:\w+\W+){0,3}?(?:19|20)\d\d"
)
_NOTICE_KM = re.compile(
    r"(?:najeto|najezd\w*|ujetych kilometru|tachometr\w*|stav km)\W{0,3}"
    r"(\d{1,3}(?:[ .]\d{3})+|\d{1,7})(?:\s*km|(?![\d.,]))"
)
# The register's fuel codes: "palivo: NM" (motor diesel), "BA" (petrol).
_NOTICE_FUEL = re.compile(r"palivo\W{0,3}(nm|ba)(?![a-z])")
_VIN = re.compile(r"\bVIN\b(?:\s*(?:/|NEBO)\s*ČÍSLO PODVOZKU)?\W{0,5}([A-HJ-NPR-Z0-9]{17})\b")


def _last_sunday(year: int, month: int) -> date:
    end = date(year, month, 31)  # March and October have 31 days
    return end - timedelta(days=(end.weekday() + 1) % 7)


def prague_to_utc(local: datetime) -> datetime:
    """Czech local time as naive UTC: CET, or CEST from the last Sunday of March to the last
    Sunday of October."""
    summer = (
        datetime.combine(_last_sunday(local.year, 3), time(2))
        <= local
        < datetime.combine(_last_sunday(local.year, 10), time(3))
    )
    return local - timedelta(hours=2 if summer else 1)


def utc_to_prague(utc: datetime) -> datetime:
    """Naive UTC as Czech local time."""
    summer = (
        datetime.combine(_last_sunday(utc.year, 3), time(1))
        <= utc
        < datetime.combine(_last_sunday(utc.year, 10), time(1))
    )
    return utc + timedelta(hours=2 if summer else 1)


def czech_time(local: datetime) -> str:
    """ "3. 12. 2026 10:00", as auction pages write a time."""
    return f"{local.day}. {local.month}. {local.year} {local.hour}:{local.minute:02d}"


def auction_time(text: str | None) -> datetime | None:
    """The first date, with its time if given, in a text ("3. 12. 2026 10:00", "3.12.2026 v
    10:00"), as naive UTC; a date alone counts from midnight."""
    m = _DATE_TIME.search(text or "")
    if not m:
        return None
    try:
        local = datetime(int(m[3]), int(m[2]), int(m[1]), int(m[4] or 0), int(m[5] or 0))
    except ValueError:
        return None
    return prague_to_utc(local)


def not_a_car(*texts: str | None) -> bool:
    """A lot's title or category names a motorcycle, trailer, truck, van or machine."""
    return any(_NOT_CAR.search(fold(text or "")) for text in texts)


def model_after_make(text: str | None) -> str | None:
    """The word after the make in a lot's title: "Dražba vozidla AUDI A4" -> "A4",
    "Škoda Octavia 2.0TDi" -> "Octavia"; None when no make is named or nothing follows."""
    text = clean_spaces(text or "")
    end = make_end(text)
    if end is None:
        return None
    # Folding keeps the length of Czech words, so positions match the original text.
    word = re.match(r"[\s,:-]*(?:\d+[.,]\d+\s+)?([^\W_][\w-]*)", text[end:])
    return word[1] if word else None


def notice_year(*texts: str | None) -> int | None:
    """The year of manufacture or first registration, as a notice or the cars helper reads it."""
    for text in texts:
        m = _NOTICE_YEAR.search(fold(clean_spaces(text or "")))
        if m and 1950 <= int(m[1]) <= date.today().year + 1:
            return int(m[1])
    return cars.year_in_text(*(_IMPORTED.sub(" ", text or "") for text in texts[:2]))


def notice_km(text: str | None) -> int | None:
    m = _NOTICE_KM.search(fold(clean_spaces(text or "")))
    if m:
        return int(re.sub(r"\D", "", m[1])) or None
    return cars.km_in_text(None, text)


def notice_vin(text: str | None) -> str | None:
    m = _VIN.search(clean_spaces(text or "").upper())
    return m[1] if m else None


def notice_fuel(*texts: str | None) -> str | None:
    for text in texts:
        m = _NOTICE_FUEL.search(fold(clean_spaces(text or "")))
        if m:
            return "diesel" if m[1] == "nm" else "petrol"
    return cars.fuel_in_text(*texts)
