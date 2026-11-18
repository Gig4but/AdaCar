"""Ask in a sentence ("Zeptat se větou", PLAN §6): a question in plain Czech becomes the
search filters, which the user then sees and can change like any other filter.

"dieselové kombi do 300 tisíc do hodiny od Kolína" ->
fuel=["diesel"], body=["combi"], price_max=300000, from "Kolín", travel_max=60.
"""

from dataclasses import dataclass

from adacar.ai.client import BadAnswer, LlmClient
from adacar.ai.extract import BODY_NAMES, FUEL_NAMES, GEARBOX_NAMES
from adacar.geo.places import KRAJE
from adacar.normalize.text import fold
from adacar.normalize.vocab import MAKES, SELLER_KINDS
from adacar.search.service import MAX_TRAVEL, Filters

PROMPT_VERSION = 2
POSTED_DAYS = (1, 3, 7, 14, 30)

SYSTEM = """Převeď dotaz uživatele na filtry pro hledání auta na prodej. Vrať JSON.
- "slova": 1–3 slova pro fulltext (model nebo výbava, např. "Octavia", "tažné"), jinak "".
  Značku, palivo, cenu, místo ani rok do slov nedávej, na ty jsou filtry.
- "znacka", "palivo", "prevodovka", "karoserie", "prodejce", "kraj": vyber jen to, co dotaz
  opravdu žádá; jinak [].
- "kraj": kraj, jen když dotaz chce auta v kraji nebo okolí místa ("na Kolínsku").
- "cena_od", "cena_do": cena v Kč ("300 tisíc" = 300000), jinak null.
- "rok_od": nejstarší rok výroby ("ne starší než 2015" = 2015), jinak null.
- "km_do": nejvýš najeto km ("do 150 tisíc km" = 150000), jinak null.
- "vykon_od": nejmenší výkon v kW, jinak null.
- "odkud", "minut": místo, odkud se k autu jede, a nejdelší cesta v minutách
  ("do hodiny od Kolína" = "Kolín", 60), jinak null.
- "dni": jak čerstvé inzeráty chce (počet dní), jinak null."""


def _labels(vocab: dict) -> dict[str, str]:
    """Czech label -> code."""
    return {entry["cs"]: code for code, entry in vocab.items()}


MAKE_BY_LABEL = _labels(MAKES)
FUEL_BY_LABEL = FUEL_NAMES  # lowercase, as in extract.py
GEARBOX_BY_LABEL = GEARBOX_NAMES
BODY_BY_LABEL = BODY_NAMES
SELLER_BY_LABEL = _labels(SELLER_KINDS)


@dataclass
class Asked:
    filters: Filters
    place: str = ""  # where the trip starts ("Kolín"); located by the caller


def schema() -> dict:
    array = lambda values, n: {  # noqa: E731
        "type": "array",
        "items": {"enum": list(values)},
        "maxItems": n,
    }
    number = {"type": ["integer", "null"]}
    return {
        "type": "object",
        "properties": {
            "slova": {"type": "string", "maxLength": 40},
            "znacka": array(MAKE_BY_LABEL, 3),
            "palivo": array(FUEL_BY_LABEL, 3),
            "prevodovka": array(GEARBOX_BY_LABEL, 1),
            "karoserie": array(BODY_BY_LABEL, 3),
            "prodejce": array(SELLER_BY_LABEL, 1),
            "kraj": array(KRAJE, 3),
            "cena_od": number,
            "cena_do": number,
            "rok_od": number,
            "km_do": number,
            "vykon_od": number,
            "odkud": {"type": ["string", "null"], "maxLength": 40},
            "minut": number,
            "dni": {"enum": [*POSTED_DAYS, None]},
        },
        "required": [
            "slova",
            "znacka",
            "palivo",
            "prevodovka",
            "karoserie",
            "prodejce",
            "kraj",
            "cena_od",
            "cena_do",
            "rok_od",
            "km_do",
            "vykon_od",
            "odkud",
            "minut",
            "dni",
        ],  # fmt: skip
        "additionalProperties": False,
    }


def _codes(data: dict, key: str, table: dict[str, str]) -> list[str]:
    return [table[x] for x in data.get(key) or () if x in table]


def _number(data: dict, key: str, low: int, high: int) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and low <= value <= high else None


def to_filters(data: dict) -> Asked:
    minutes = data.get("minut")
    place = (data.get("odkud") or "").strip()
    f = Filters(
        q=(data.get("slova") or "").strip(),
        make=_codes(data, "znacka", MAKE_BY_LABEL),
        fuel=_codes(data, "palivo", FUEL_BY_LABEL),
        gearbox=_codes(data, "prevodovka", GEARBOX_BY_LABEL),
        body=_codes(data, "karoserie", BODY_BY_LABEL),
        seller_kind=_codes(data, "prodejce", SELLER_BY_LABEL),
        kraj=[k for k in data.get("kraj") or () if k in KRAJE],
        price_min=_number(data, "cena_od", 1_000, 100_000_000),
        price_max=_number(data, "cena_do", 1_000, 100_000_000),
        year_min=_number(data, "rok_od", 1950, 2100),
        km_max=_number(data, "km_do", 1_000, 2_000_000),
        power_min=_number(data, "vykon_od", 10, 2000),
        posted_days=data.get("dni") if data.get("dni") in POSTED_DAYS else None,
        travel_max=minutes if place and _number(data, "minut", 5, MAX_TRAVEL) else None,
    )
    if f.price_min and f.price_max and f.price_min > f.price_max:
        f.price_min, f.price_max = f.price_max, f.price_min
    return Asked(f, place[:100])


def _asked_for(place: str, question: str) -> bool:
    """The place is in the question ("od Kolína" for "Kolín"): a small model invents one."""
    stem = fold(place)[: max(3, len(fold(place)) - 2)]
    return bool(stem) and stem in fold(question)


def ask(client: LlmClient, question: str) -> Asked:
    reply = client.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question[:300]}],
        schema=schema(),
        max_tokens=200,
    )
    try:
        asked = to_filters(reply.data)
    except (AttributeError, TypeError) as exc:  # a value of the wrong type
        raise BadAnswer(f"unexpected answer: {str(reply.data)[:200]}") from exc
    if asked.place and not _asked_for(asked.place, question):
        asked.place, asked.filters.travel_max = "", None
    return asked
