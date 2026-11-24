"""The app's own filter values. Every source maps onto these, so filters work across sources.

Make codes are sauto.cz's names in URLs ("mercedes-benz"); other sources map onto them
(`make_code` reads a make from free text too).
"""

import re

from adacar.normalize.text import fold

Labels = dict[str, dict[str, str]]  # code -> {"cs": ..., "en": ...}


def _same(*names: str) -> Labels:
    return {
        code: {"cs": name, "en": name} for code, name in zip(names[::2], names[1::2], strict=True)
    }


MAKES: Labels = _same(
    "abarth", "Abarth", "alfa-romeo", "Alfa Romeo", "alpina", "Alpina", "alpine", "Alpine",
    "aston-martin", "Aston Martin",
    "audi", "Audi", "bentley", "Bentley", "bmw", "BMW", "byd", "BYD", "cadillac", "Cadillac",
    "chevrolet", "Chevrolet", "chrysler", "Chrysler", "citroen", "Citroën", "cupra", "Cupra",
    "dacia", "Dacia", "daewoo", "Daewoo", "dodge", "Dodge", "ds", "DS", "ferrari", "Ferrari",
    "fiat", "Fiat", "ford", "Ford", "genesis", "Genesis", "gmc", "GMC", "gwm", "GWM",
    "honda", "Honda", "hummer", "Hummer", "hyundai", "Hyundai",
    "infiniti", "Infiniti", "isuzu", "Isuzu", "jaguar", "Jaguar", "jeep", "Jeep", "kia", "Kia",
    "lada", "Lada", "lamborghini", "Lamborghini", "lancia", "Lancia", "land-rover", "Land Rover",
    "leapmotor", "Leapmotor", "lexus", "Lexus", "lotus", "Lotus", "maserati", "Maserati",
    "maxus", "Maxus", "mazda", "Mazda", "mclaren", "McLaren", "mercedes-benz", "Mercedes-Benz",
    "mg", "MG",
    "mini", "Mini", "mitsubishi", "Mitsubishi", "nissan", "Nissan", "opel", "Opel",
    "peugeot", "Peugeot", "polestar", "Polestar", "porsche", "Porsche", "renault", "Renault",
    "rolls-royce", "Rolls-Royce", "saab", "Saab", "seat", "Seat", "skoda", "Škoda",
    "smart", "Smart", "ssangyong", "SsangYong", "subaru", "Subaru", "suzuki", "Suzuki",
    "tesla", "Tesla", "toyota", "Toyota", "volkswagen", "Volkswagen", "volvo", "Volvo",
    "xpeng", "XPeng",
)  # fmt: skip

FUELS: Labels = {
    "petrol": {"cs": "Benzín", "en": "Petrol"},
    "diesel": {"cs": "Nafta", "en": "Diesel"},
    "lpg": {"cs": "LPG + benzín", "en": "LPG + petrol"},
    "cng": {"cs": "CNG + benzín", "en": "CNG + petrol"},
    "hybrid": {"cs": "Hybrid", "en": "Hybrid"},
    "plugin_hybrid": {"cs": "Plug-in hybrid", "en": "Plug-in hybrid"},
    "electric": {"cs": "Elektro", "en": "Electric"},
    "hydrogen": {"cs": "Vodík", "en": "Hydrogen"},
    "other": {"cs": "Jiné", "en": "Other"},
}

GEARBOXES: Labels = {
    "manual": {"cs": "Manuální", "en": "Manual"},
    "automatic": {"cs": "Automatická", "en": "Automatic"},
}

BODIES: Labels = {
    "hatchback": {"cs": "Hatchback", "en": "Hatchback"},
    "sedan": {"cs": "Sedan", "en": "Saloon"},
    "liftback": {"cs": "Liftback", "en": "Liftback"},
    "combi": {"cs": "Kombi", "en": "Estate"},
    "suv": {"cs": "SUV", "en": "SUV"},
    "offroad": {"cs": "Terénní", "en": "Off-road"},
    "mpv": {"cs": "MPV", "en": "MPV"},
    "coupe": {"cs": "Kupé", "en": "Coupé"},
    "cabrio": {"cs": "Kabriolet / roadster", "en": "Convertible"},
    "pickup": {"cs": "Pick-up", "en": "Pick-up"},
    "van": {"cs": "Dodávka / VAN", "en": "Van"},
    "other": {"cs": "Jiná", "en": "Other"},
}

DRIVES: Labels = {
    "front": {"cs": "Přední", "en": "Front-wheel"},
    "rear": {"cs": "Zadní", "en": "Rear-wheel"},
    "4x4": {"cs": "4×4", "en": "All-wheel"},
}

CONDITIONS: Labels = {
    "used": {"cs": "Ojeté", "en": "Used"},
    "new": {"cs": "Nové", "en": "New"},
    "demo": {"cs": "Předváděcí", "en": "Demonstrator"},
    "damaged": {"cs": "Havarované / na díly", "en": "Damaged / for parts"},
}

SELLER_KINDS: Labels = {
    "private": {"cs": "Soukromý prodejce", "en": "Private seller"},
    "dealer": {"cs": "Autobazar / dealer", "en": "Dealer"},
}

VOCABULARIES: dict[str, Labels] = {
    "makes": MAKES,
    "fuels": FUELS,
    "gearboxes": GEARBOXES,
    "bodies": BODIES,
    "drives": DRIVES,
    "conditions": CONDITIONS,
    "seller_kinds": SELLER_KINDS,
}


def label(vocab: str, code: str | None, lang: str = "cs") -> str:
    if not code:
        return ""
    entry = VOCABULARIES[vocab].get(code)
    return entry.get(lang) or entry["cs"] if entry else code


# Free-text spellings -> make code: the code itself, its name, and common short forms.
_MAKE_WORDS: dict[str, str] = {
    **{fold(code).replace("-", " "): code for code in MAKES},
    **{fold(names["cs"]).replace("-", " "): code for code, names in MAKES.items()},
    "vw": "volkswagen",
    "mercedes": "mercedes-benz",
    "merc": "mercedes-benz",
    "alfa": "alfa-romeo",
    "range rover": "land-rover",
    "landrover": "land-rover",
    "great wall": "gwm",
    "bmw alpina": "alpina",
}
_MAKE_RE = re.compile(
    r"(?<![a-z0-9])("
    + "|".join(re.escape(w) for w in sorted(_MAKE_WORDS, key=len, reverse=True))
    + r")(?![a-z0-9])"
)


def make_code(text: str | None) -> str | None:
    """The first make named in a text ("Prodám VW Golf 1.4" -> "volkswagen")."""
    if not text:
        return None
    m = _MAKE_RE.search(fold(text).replace("-", " "))
    return _MAKE_WORDS[m[1]] if m else None


def make_end(text: str) -> int | None:
    """Where the first make named in a text ends ("AUDI A4" -> 4); None without a make."""
    m = _MAKE_RE.search(fold(text).replace("-", " "))
    return m.end() if m else None
