"""Reads a free-text car ad with the local model (PLAN §6).

One call per ad. The answer is a small JSON object whose shape is enforced by llama-server
(the schema becomes a grammar), with Czech enum values a small model handles well. Only the
fields the car still lacks are asked (fewer output tokens).

Everything the model says is checked against the ad before it is used:
- numbers (year, km, kW) must appear in the text and be plausible;
- the make, the model's name and the VIN must be written in the text;
- the fuel, body and gearbox need their words in the text (small models guess them, e.g.
  "kombi" for an SUV);
- the seller's claims (service book, first owner, accident) need their words in the text.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from adacar.ai.client import LlmClient, Timing
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import BODIES, FUELS, GEARBOXES, MAKES, make_code

PROMPT_VERSION = 5
MAX_AD_CHARS = 3000  # ~1k tokens; keeps the context within 4k on the smallest machines

# Answer value (Czech, as the model writes it) -> the app's code. Lowercase as ads write
# them: offered "Benzín", a small model wrote "Nafta" for "benzín" (the grammar forbids the
# lowercase word it wanted to start with).
MAKE_NAMES = {names["cs"]: code for code, names in MAKES.items()}
FUEL_NAMES = {names["cs"].lower(): code for code, names in FUELS.items() if code != "other"}
GEARBOX_NAMES = {names["cs"].lower(): code for code, names in GEARBOXES.items()}
BODY_NAMES = {names["cs"].lower(): code for code, names in BODIES.items() if code != "other"}
WARNINGS = {
    "záloha předem": "deposit",
    "prodejce v zahraničí": "abroad",
    "nezaručené km": "km_unverified",
}

SYSTEM = """Jsi pečlivý asistent, který čte inzeráty na prodej aut v češtině a vrací údaje
jako JSON. Pravidla:
- Uveď jen to, co v inzerátu opravdu stojí. Co tam není, dej null.
- "nabidka": true, když se prodává celé auto; false, když někdo auto shání, vykupuje,
  prodává jen díly nebo jde o jiný text.
- "znacka", "model": značka a název modelu, jak jsou v textu (model bez motoru a výbavy,
  např. "Octavia", "Golf", "Focus").
- "rok": rok výroby nebo první registrace (čtyři číslice).
- "km": stav tachometru v km jako celé číslo ("185 tis. km" = 185000).
- "palivo", "prevodovka", "karoserie": jen když to z textu plyne (např. TDI = Nafta,
  DSG = automatická, Variant = kombi).
- "vykon_kw": výkon motoru v kW (ne v koních).
- "vin": 17místné VIN, jen když je v textu celé.
- "servisni_knizka", "prvni_majitel": true jen když to inzerát uvádí, jinak null.
- "havarovano": false, když inzerát píše nehavarováno / bez nehody; true, když auto bylo
  bourané; jinak null.
- "varovani": jen zjevné varovné signály (prodejce chce zálohu předem, je v zahraničí
  a auto pošle, stav km nelze zaručit)."""

# Field groups asked only when the car lacks them (`Car` column -> answer keys).
FIELDS = (
    "make",
    "model",
    "year",
    "mileage_km",
    "fuel",
    "gearbox",
    "power_kw",
    "body",
    "vin",
    "service_book",
    "first_owner",
    "crashed",
)
KEYS = {
    "make": "znacka",
    "model": "model",
    "year": "rok",
    "mileage_km": "km",
    "fuel": "palivo",
    "gearbox": "prevodovka",
    "power_kw": "vykon_kw",
    "body": "karoserie",
    "vin": "vin",
    "service_book": "servisni_knizka",
    "first_owner": "prvni_majitel",
    "crashed": "havarovano",
}


def schema(fields: tuple[str, ...] = FIELDS, untrusted: bool = False) -> dict:
    """`untrusted`: classifieds, which may not sell a car at all, or may be scams: also asks
    "nabidka" and "varovani"."""
    nullable = lambda values: {"enum": [*values, None]}  # noqa: E731
    number = {"type": ["integer", "null"]}
    yes = {"enum": [True, None]}
    specs = {
        "make": nullable(MAKE_NAMES),
        "model": {"type": ["string", "null"], "maxLength": 30},
        "year": number,
        "mileage_km": number,
        "fuel": nullable(FUEL_NAMES),
        "gearbox": nullable(GEARBOX_NAMES),
        "power_kw": number,
        "body": nullable(BODY_NAMES),
        "vin": {"type": ["string", "null"], "maxLength": 17},
        "service_book": yes,
        "first_owner": yes,
        "crashed": {"type": ["boolean", "null"]},
    }
    properties: dict = {"nabidka": {"type": "boolean"}} if untrusted else {}
    properties |= {KEYS[name]: specs[name] for name in FIELDS if name in fields}
    if untrusted:
        properties["varovani"] = {
            "type": "array",
            "items": {"enum": list(WARNINGS)},
            "maxItems": 3,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


@dataclass
class Extraction:
    is_offer: bool = True
    values: dict = field(default_factory=dict)  # Car column -> checked value
    warnings: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # model claims that failed the checks
    raw: dict = field(default_factory=dict)  # the answer as given (cached, checked again)
    timing: Timing = field(default_factory=lambda: Timing(0, 0.0, 0, 0.0, 0.0))


def trim(text: str, limit: int = MAX_AD_CHARS) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > limit // 2 else limit] + "\n…"


def user_message(text: str) -> str:
    return "Inzerát:\n" + trim(text)


def extract(
    client: LlmClient, text: str, fields: tuple[str, ...] = FIELDS, untrusted: bool = False
) -> Extraction:
    reply = client.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_message(text)}],
        schema=schema(fields, untrusted),
        max_tokens=200,
    )
    result = validate(reply.data, text)
    result.timing = reply.timing
    return result


# --- checks -------------------------------------------------------------------------------

# The seller's claims, said and not negated (cars.said: "bez servisní knížky" is no claim).
SERVICE_BOOK = r"servisn\w* (?:knizk|histori)|servisovan"
FIRST_OWNER = r"(?:prvni|1\.?) ?majitel|od noveho|kupovan\w* (?:jako )?nov"
NOT_CRASHED = r"nehavarovan|nebourane|bez nehod|nebyl\w* (?:bourane|havarovan)"
CRASHED = r"(?<!ne)havarovan|(?<!ne)bourane|po nehode"
VIN = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
# A gearbox needs its own words: small models guess one otherwise ("převodovka v pořádku" is
# no answer).
GEARBOXES_SAID = {
    "automatic": r"automat|dsg|tiptronic|s-?tronic|steptronic|cvt|multitronic|edc",
    "manual": r"manual|rucni prevod|\d[ -]?(?:st\.?|stup\w*|kvalt\w*) |(?:peti|sesti)stup",
}
# "Not a car for sale" needs a buyer's or a parts seller's words at the start of the title
# (2B models took real ads for none in the eval; "Prodám Octavii, nové pneu" sells a car).
NOT_OFFER = (
    r"^\W*(?:(?:prodam|nabizim|predam)\W+)?(?:[a-z]+\.?\W+)?(?:koupim|vykoupim|vykup|shanim"
    r"|poptavam|hledam|nahradni dily|dily|na dily|rozpredam|disky|pneu|kola(?![a-z]))"
)
# A number with its unit: "103 kW" is no mileage, "STK do 2015" no year.
KM_UNIT = r"\s*(?:km|kilometr)"
KM_SHORT = r"\s*(?:tis\w*\.?|t\.?|k|xxx)\s*(?:km)?(?![a-z])"
KM_LABEL = r"(?:najet\w*|tachometr\w*|stav km|km)\W{1,3}"
NOT_A_YEAR = re.compile(r"(?:stk|tk|do|platn\w*)\W*$")


def _digits(text: str) -> str:
    """The ad's numbers with thousands separators removed: "185 000" and "185.000" -> 185000."""
    return re.sub(r"(?<=\d)[\s.  ](?=\d{3}\b)", "", text)


def number_in_text(value: int, text: str, after: str = "", before: str = "") -> bool:
    """The number in the folded text, with `after` (a unit) or `before` (a label) when given;
    also where a separator would join two numbers: "r.v. 2014 103 kW"."""
    folded = fold(text)
    for t in (folded, _digits(folded)):
        if re.search(rf"(?<!\d){value}(?!\d){after}", t):
            return True
        if before and re.search(rf"{before}{value}(?!\d)", t):
            return True
    return False


def km_in_text(value: int, text: str) -> bool:
    """ "185 000 km", "najeto: 185000", "185 tis. km", "185tkm", "185xxx"."""
    if number_in_text(value, text, KM_UNIT) or number_in_text(value, text, "x", KM_LABEL):
        return True
    return value % 1000 == 0 and number_in_text(value // 1000, text, KM_SHORT)


def year_in_text(value: int, text: str) -> bool:
    """The year, not as a date something lasts until ("STK do 2027")."""
    folded = _digits(fold(text)) + " " + fold(text)
    return any(
        not NOT_A_YEAR.search(folded[max(0, m.start() - 12) : m.start()])
        for m in re.finditer(rf"(?<!\d){value}(?!\d)", folded)
    )


def _in_text(words: str, folded: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(fold(words))}", folded))


def validate(data: dict, text: str) -> Extraction:
    folded = fold(text)
    offer = bool(data.get("nabidka", True)) or not re.search(NOT_OFFER, folded.split("\n")[0])
    out = Extraction(is_offer=offer, raw=data)
    if offer and data.get("nabidka") is False:
        out.dropped.append("nabidka=False")
    values = out.values

    def drop(key: str, value) -> None:
        out.dropped.append(f"{key}={value}")

    make = MAKE_NAMES.get(data.get("znacka") or "")
    if make and (make_code(text) == make or _in_text(MAKES[make]["cs"], folded)):
        values["make"] = make
    elif data.get("znacka"):
        drop("znacka", data["znacka"])
    model = clean_spaces(data.get("model") or "")
    first, _, rest = model.partition(" ")
    if rest and make_code(first):  # "Volvo V90" -> "V90"
        model = rest
    if model and _in_text(model, folded):
        values["model"] = model
    elif model:
        drop("model", model)

    limits = {
        "rok": ("year", cars.MIN_YEAR, date.today().year + 1, year_in_text),
        "km": ("mileage_km", 0, cars.MAX_KM, km_in_text),
        "vykon_kw": ("power_kw", 20, 900, lambda v, t: number_in_text(v, t, r"\s*kw")),
    }
    for key, (name, low, high, written) in limits.items():
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, int) and low <= value <= high and written(value, text):
            values[name] = value
        else:
            drop(key, value)

    for key, name, table in (
        ("palivo", "fuel", FUEL_NAMES),
        ("prevodovka", "gearbox", GEARBOX_NAMES),
        ("karoserie", "body", BODY_NAMES),
    ):
        if not (code := table.get(data.get(key) or "")):
            continue
        if name == "fuel":
            named = code in cars.fuels_in_text(text)
        elif name == "body":
            named = code in cars.bodies_in_text(text)
        else:
            named = cars.said(GEARBOXES_SAID[code], folded)
        if named:
            values[name] = code
        else:
            drop(key, data[key])

    vin = re.sub(r"\s", "", str(data.get("vin") or "")).upper()
    if vin and VIN.fullmatch(vin) and vin in re.sub(r"\s", "", text).upper():
        values["vin"] = vin
    elif vin:
        drop("vin", vin)

    for key, name, evidence in (
        ("servisni_knizka", "service_book", SERVICE_BOOK),
        ("prvni_majitel", "first_owner", FIRST_OWNER),
    ):
        if data.get(key) is True:
            if cars.said(evidence, folded):
                values[name] = True
            else:
                drop(key, True)
    crashed = data.get("havarovano")
    if crashed is not None:
        if cars.said(CRASHED, folded) if crashed else re.search(NOT_CRASHED, folded):
            values["crashed"] = bool(crashed)
        else:
            drop("havarovano", crashed)

    for answer in data.get("varovani") or ():
        code = WARNINGS.get(answer)
        if code and cars.warning_said(code, folded):
            out.warnings.append(code)
        elif code:
            drop("varovani", answer)
    return out
