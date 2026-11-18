"""Car facts from free text: price, year, mileage, power, fuel, gearbox and body as private
sellers write them ("r.v. 2015, najeto 183 xxx km, 2.0 TDI 110 kW, DSG, kombi").

Portals with structured fields (sauto.cz) do not need this; classifieds (bazos.cz) do. The
rules take only clear statements; the AI later reads what they miss.
"""

import re
from datetime import date

from adacar.normalize.text import clean_spaces, fold

MIN_YEAR = 1950
MIN_PRICE = 1000  # "1 Kč", "10 Kč": placeholders, not prices
MAX_KM = 1_500_000

_CURRENCIES = {"kc": "CZK", "czk": "CZK", "eur": "EUR", "€": "EUR"}
_NO_PRICE = re.compile(r"dohodou|v textu|nabidnete|info v|cena na|na dotaz|zdarma")
_AMOUNT = re.compile(r"\d{1,3}(?:[ .  ]\d{3})+|\d+")


def _number(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def parse_price(text: str | None) -> tuple[int, str] | None:
    """A price field: "309 900 Kč" -> (309900, "CZK"); "Dohodou", "1 Kč" -> None."""
    if not text:
        return None
    folded = fold(clean_spaces(text))
    if _NO_PRICE.search(folded):
        return None
    m = _AMOUNT.search(folded)
    if m is None or _number(m[0]) < MIN_PRICE:
        return None
    currency = next((code for key, code in _CURRENCIES.items() if key in folded), "CZK")
    return _number(m[0]), currency


_PRICE_IN_TEXT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ .]\d{3})+|\d{4,9})\s*(?:kc|czk|,-)")


def price_in_text(*texts: str | None) -> int | None:
    """A pasted ad's price (sources have their own field): the amount after "cena", else the
    largest one ("splátka 5 000 Kč, 289 000 Kč" -> 289000: instalments, deposits and fees are
    smaller)."""
    folded = " ".join(fold(clean_spaces(text or "")) for text in texts)
    filler = r"(?:(?:je|jen|za|auta?|vozu|vozidla|celkem|pouze|konecna|nova)\W{1,3}){0,3}"
    labelled = re.search(r"cen[ay]\W{0,3}" + filler + _PRICE_IN_TEXT.pattern, folded)
    if labelled and _number(labelled[1]) >= MIN_PRICE:
        return _number(labelled[1])
    amounts = [_number(m[1]) for m in _PRICE_IN_TEXT.finditer(folded)]
    return max((a for a in amounts if a >= MIN_PRICE), default=None)


def _plausible_year(year: int) -> bool:
    return MIN_YEAR <= year <= date.today().year + 1


_YEAR_LABELLED = re.compile(
    r"(?:rok\s+vyroby|r\.\s*v\.|(?<![a-z])rv|rok\s+vyr\.?|vyrob\w*|r\.\s*vyr\.?|1\.\s*registrac\w*"
    r"|prvni\s+registrac\w*|registrac\w*|model\s+rok|rok)\s*[:\-]?\s*(?:\d{1,2}[./]\s*)?"
    r"(19\d{2}|20\d{2})(?!\d)"
)
# A year alone, not part of a number, volume or mileage ("2.0", "2000 km", "1998 ccm").
_YEAR_ALONE = re.compile(
    r"(?<![\d.,/])(19[5-9]\d|20[0-4]\d)(?![\d/]|[.,]\d)(?!\s*(?:km|kc|ccm|cm3|cm|kg|kw|,-|xxx))"
)


# Models named by a number that looks like a year ("Peugeot 2008", "BMW 2002").
MODEL_NUMBERS = {"peugeot": {2008}, "bmw": {2002}}


def year_in_text(title: str | None, text: str | None = None, make: str | None = None) -> int | None:
    """ "Rok výroby: 2020", "r.v. 2015", "1. registrace 3/2016"; else a year alone in the
    title ("Octavia 2.0 TDI 110kW 2020"), unless it is the name of the `make`'s model."""
    for part in (title, text):
        m = _YEAR_LABELLED.search(fold(part or ""))
        if m and _plausible_year(int(m[1])):
            return int(m[1])
    models = MODEL_NUMBERS.get(make or "", set())
    for m in _YEAR_ALONE.finditer(fold(title or "")):
        if _plausible_year(int(m[1])) and int(m[1]) not in models:
            return int(m[1])
    return None


_KM_LABEL = re.compile(r"najet|najezd|tachometr|stav\s+km|km\s*:")
# "183 xxx km", "120 000 km", "95 tkm", "95 tis. km", "120tis km", "95k km": a number, maybe a
# thousands mark, then "km".
_KM = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ .]\d{3})+|\d{1,7})\s*(xxx|tis\.?|t\.?|k)?\s*km(?![a-z])")


def _km_value(m: re.Match) -> int | None:
    value = _number(m[1]) * (1000 if m[2] else 1)
    return value if 0 < value <= MAX_KM else None


def km_in_text(title: str | None, text: str | None = None) -> int | None:
    """Mileage on a line that names it ("Najeto: 183 xxx km"), else in the title
    ("Superb 2.0 TDI, jen 95 tkm")."""
    for line in (text or "").splitlines():
        folded = fold(line)
        if label := _KM_LABEL.search(folded):
            for m in _KM.finditer(folded, label.start()):
                if (value := _km_value(m)) is not None:
                    return value
    for m in _KM.finditer(fold(title or "")):
        if (value := _km_value(m)) is not None and value >= 1000:
            return value
    return None


_POWER = re.compile(r"(?<![\d.])(\d{2,3})\s*kw(?![a-z])")


def power_in_text(*texts: str | None) -> int | None:
    """ "2.0 TDI 110 kW" -> 110."""
    for text in texts:
        m = _POWER.search(fold(text or ""))
        if m and 20 <= int(m[1]) <= 900:
            return int(m[1])
    return None


# Checked in order: an "LPG" or "hybrid" car also names its petrol engine.
_FUEL_WORDS = (
    ("plugin_hybrid", r"plug-?in|phev"),
    ("hybrid", r"hybrid|hev(?![a-z])"),
    ("lpg", r"lpg"),
    ("cng", r"cng|g-tec"),
    ("electric", r"elektromobil|elektrina|elektro(?![a-z-])|(?<![a-z])ev(?![a-z])|\bbev\b"),
    ("diesel", r"nafta|naftov|diesel|tdi|crdi|dci|hdi|cdi|tdci|jtd|d4d|multijet|bluehdi|cdti"),
    ("petrol", r"benzin|tsi|tfsi|fsi|mpi|tce|gdi|ecoboost|vtec"),
)
_GEARBOX_WORDS = (
    (
        "automatic",
        r"automat(?![a-z])|automatick\w* (?:prevod|razeni)|prevodovk\w*:? *automat"
        r"|dsg|tiptronic|s-?tronic|steptronic|cvt|multitronic|edc",
    ),
    ("manual", r"manual(?![a-z])|manualni prevod|prevodovk\w*:? *manual|rucni prevod"),
)
_BODY_WORDS = (
    (
        "combi",
        r"kombi(?![a-z])|combi(?![a-z])|variant|touring|avant|estate|sportswagon"
        r"|(?<![a-z])sw(?![a-z])",
    ),
    ("suv", r"suv"),
    ("mpv", r"mpv|minivan"),
    ("liftback", r"liftback"),
    ("hatchback", r"hatchback|hatch"),
    ("sedan", r"sedan|limuzin"),
    ("coupe", r"kupe|coupe"),
    ("cabrio", r"kabrio|cabrio|roadster|spider"),
    ("pickup", r"pick-?up"),
    ("van", r"dodavk|(?<![a-z])van(?![a-z])"),
)


def _first(words: tuple[tuple[str, str], ...], *texts: str | None) -> str | None:
    for text in texts:
        folded = fold(text or "")
        for code, pattern in words:
            if re.search(rf"(?<![a-z]){pattern}", folded):
                return code
    return None


def _every(words: tuple[tuple[str, str], ...], *texts: str | None) -> set[str]:
    folded = " ".join(fold(text or "") for text in texts)
    return {code for code, pattern in words if re.search(rf"(?<![a-z]){pattern}", folded)}


def fuels_in_text(*texts: str | None) -> set[str]:
    """Every fuel the text names ("1.4 TSI LPG" -> petrol and lpg)."""
    return _every(_FUEL_WORDS, *texts)


def bodies_in_text(*texts: str | None) -> set[str]:
    return _every(_BODY_WORDS, *texts)


def fuel_in_text(*texts: str | None) -> str | None:
    return _first(_FUEL_WORDS, *texts)


def gearbox_in_text(*texts: str | None) -> str | None:
    return _first(_GEARBOX_WORDS, *texts)


def body_in_text(*texts: str | None) -> str | None:
    return _first(_BODY_WORDS, *texts)


# Ads in car categories that do not sell a car: buyers, scrap dealers, parts.
_NOT_OFFER = (
    ("wanted", r"^(?:koupim|vykoupim|shanim|poptavam|hledam(?! (?:noveho )?majitel))"),
    ("buyout", r"vykup\w* (?:aut|vozid|vozu)|(?<![a-z])vykup(?![a-z])"),
    # A title that starts with a part: "Motor 1.9 TDI", "Kola 16 na Octavii".
    (
        "parts",
        r"^(?:prodam )?(?:nahradni dily|dily|rozpredam|na dily|motor|prevodovk[ay]"
        r"|(?:sada )?(?:(?:zimni|letni)\w* )?(?:alu )?(?:kola|kol|disky|pneu\w*)"
        r"|naraznik|kapota|dvere|blatnik|svetl[ao]|sedacky|tazne zarizeni)(?![a-z])",
    ),
)


def not_offer_reason(title: str | None) -> str | None:
    """Why a classifieds ad in a car category is not a car for sale (None: it is one)."""
    folded = fold(clean_spaces(title or ""))
    return next((code for code, pattern in _NOT_OFFER if re.search(pattern, folded)), None)


# Warning signs in an ad's own words (PLAN §6); the AI finds them said otherwise too.
# Words just before ("bez zálohy", "nepožaduji zálohu", "žádná záloha") or just after
# ("servisní knížka chybí") that say the opposite.
_NEGATED_BEFORE = re.compile(r"(?:^|\W)(?:bez|zadn\w*|ne[a-z]*)\W+(?:[a-z]+\W+)?$")
_NEGATED_AFTER = re.compile(r"^\w*\W+(?:[a-z]+\W+)?(?:chybi|neni|nema|nebyl\w*|ztracen\w*)")


def said(pattern: str, folded: str) -> bool:
    """The folded text says it: a match not negated by the words around it."""
    for m in re.finditer(pattern, folded):
        before, after = folded[max(0, m.start() - 30) : m.start()], folded[m.end() : m.end() + 30]
        if not _NEGATED_BEFORE.search(before) and not _NEGATED_AFTER.search(after):
            return True
    return False


_KM_WORD = r"(?:km|kilometr\w*|tachometr\w*|najet\w*)"
_NOT_SURE = (
    r"(?:nelze|nemohu|nemuzu|nedokazu|nemuzeme|neumim) (?:\w+ )?(?:garantovat|zarucit|dolozit"
    r"|overit)|nezaruc\w*|negarant\w*|neoveren\w*|nedolozen\w*|stocen\w*|bez (?:zaruky|garance)"
)
# Phrases of warning signs, checked with `said`, except the mileage one (a negation itself).
WARNING_WORDS = {
    "deposit": r"zaloh\w* (?:\w+ )?(?:predem|pred prohlidkou|na ucet|prevodem)"
    r"|(?:zaslat|poslat|posilat|poslete|zaslete|uhradit|zaplatit) (?:\w+ )?zaloh"
    r"|platb\w* predem",
    "abroad": r"(?:jsem|pracuji|bydlim|zijeme|zijem|zdrzuji se|nachazim se) v zahranici"
    r"|(?:je|stoji|nachazi se) (?:v|ve) (?:nemecku|rakousku|anglii|italii|spanelsku"
    r"|nizozemsku|belgii|francii|polsku)",
    "km_unverified": rf"(?:{_NOT_SURE})\W+(?:\w+\W+){{0,4}}?{_KM_WORD}"
    rf"|{_KM_WORD}\W+(?:\w+\W+){{0,4}}?(?:{_NOT_SURE})",
}


def warning_said(code: str, folded: str) -> bool:
    pattern = WARNING_WORDS[code]
    return bool(re.search(pattern, folded)) if code == "km_unverified" else said(pattern, folded)


def warnings_in_text(*texts: str | None) -> list[str]:
    """ "Pošlete zálohu předem, auto je v Německu" -> ["deposit", "abroad"]."""
    folded = " ".join(fold(clean_spaces(t or "")) for t in texts)
    return [code for code in WARNING_WORDS if warning_said(code, folded)]
