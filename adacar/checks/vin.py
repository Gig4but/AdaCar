"""What a VIN tells without going online (PLAN §4.5): the maker from its first three
characters (WMI) and, for the Volkswagen group, the model year from the tenth.

Other makers in Europe need not fill the model year, so only the VW group's is used.
"""

import re
from dataclasses import dataclass

from adacar.normalize.vocab import make_code

VALID = re.compile(r"[A-HJ-NPR-Z0-9]{17}")  # I, O and Q are never used

# World manufacturer identifiers -> make codes (normalize/vocab.MAKES). Several makes for a
# plant that builds more than one or for others (Seat and Cupra; vans of Renault and PSA sold
# as Nissan, Opel, Fiat, Toyota; Toyotas sold as Suzuki and Mazda; Ford's VW-built Transit
# Connect). An unknown WMI is no warning, so a doubt leaves a maker out.
_WMI = {
    "TMB": "skoda", "WVW": "volkswagen", "WVG": "volkswagen", "WV1": "volkswagen ford",
    "WV2": "volkswagen ford", "WV3": "volkswagen", "WV4": "volkswagen", "WV5": "volkswagen",
    "WAU": "audi", "WA1": "audi", "WUA": "audi", "TRU": "audi", "VSS": "seat cupra",
    "WP0": "porsche", "WP1": "porsche", "WBA": "bmw", "WBS": "bmw", "WBY": "bmw",
    "WMW": "mini", "WDB": "mercedes-benz", "WDC": "mercedes-benz", "WDD": "mercedes-benz",
    "WDF": "mercedes-benz", "W1K": "mercedes-benz", "W1N": "mercedes-benz",
    "W1V": "mercedes-benz", "WME": "smart",
    "VF1": "renault dacia nissan opel fiat mitsubishi", "UU1": "dacia",
    "VF3": "peugeot toyota opel fiat", "VR3": "peugeot", "VF7": "citroen ds toyota opel fiat",
    "VR7": "citroen", "VR1": "ds", "VXK": "opel", "YAR": "toyota",
    "ZFA": "fiat abarth", "ZAR": "alfa-romeo", "ZLA": "lancia", "ZFF": "ferrari",
    "ZHW": "lamborghini", "ZAM": "maserati", "W0L": "opel", "W0V": "opel", "WF0": "ford",
    "YV1": "volvo", "YV4": "volvo", "LVY": "volvo", "7JR": "volvo", "YS3": "saab",
    "SAL": "land-rover", "SAJ": "jaguar", "SAD": "jaguar land-rover", "TMA": "hyundai",
    "KMH": "hyundai", "NLH": "hyundai", "U5Y": "kia hyundai", "U6Y": "kia", "KNA": "kia",
    "KNE": "kia", "KND": "kia", "3KP": "kia", "TSM": "suzuki", "JSA": "suzuki",
    "JS2": "suzuki", "JMZ": "mazda", "JM1": "mazda", "SJN": "nissan", "VSK": "nissan",
    "JN1": "nissan", "SHH": "honda", "SHS": "honda", "JHM": "honda", "JMB": "mitsubishi",
    "MMC": "mitsubishi", "JF1": "subaru", "SB1": "toyota suzuki", "VNK": "toyota mazda",
    "NMT": "toyota", "JTD": "toyota", "JTE": "toyota", "JTN": "toyota", "JTM": "toyota",
    "JTH": "lexus", "XP7": "tesla", "5YJ": "tesla", "LRW": "tesla", "7SA": "tesla",
    "LSJ": "mg", "SCC": "lotus", "KPT": "ssangyong", "LGX": "byd",
}  # fmt: skip
WMI: dict[str, frozenset[str]] = {k: frozenset(v.split()) for k, v in _WMI.items()}
VW_GROUP = frozenset({"skoda", "volkswagen", "audi", "seat", "cupra", "porsche"})
# The tenth character's years repeat every 30: A = 1980 or 2010, Y = 2000 or 2030.
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


@dataclass(frozen=True)
class Decoded:
    makes: frozenset[str]  # empty: a maker this table does not know
    model_year: int | None  # VW group only


def clean(vin: str | None) -> str | None:
    """The VIN in capitals without spaces; None when it cannot be one."""
    if not vin:
        return None
    vin = re.sub(r"[\s-]", "", vin).upper()
    return vin if VALID.fullmatch(vin) else None


def model_year(code: str, near: int) -> int | None:
    """The year of a tenth character, of the two it can mean the one nearer `near`."""
    i = _YEAR_CODES.find(code)
    if i < 0:
        return None
    return min((1980 + i, 2010 + i), key=lambda y: abs(y - near))


def decode(vin: str, near: int) -> Decoded:
    """`near`: a year the car is about (the ad's), to pick between 30-year cycles."""
    makes = WMI.get(vin[:3], frozenset())
    year = model_year(vin[9], near) if makes & VW_GROUP else None
    return Decoded(makes, year)


def mismatches(vin: str, make: str | None, year: int | None, title: str = "") -> list[str]:
    """Warning codes where the VIN disagrees with the ad: "vin_make" (another maker than
    the ad's make and its title name) and "vin_year" (a model year that cannot go with the
    ad's year: built over two years before it, or after it; a car of model year 2020 is
    built from mid-2019). A site may keep the make of an ad its dealer rewrote for another
    car: the title then names the right one."""
    decoded = decode(vin, year or 2015)
    found = []
    named = {make, make_code(title)} - {None}
    if make and decoded.makes and not named & decoded.makes:
        found.append("vin_make")
    if year and decoded.model_year and not year - 2 <= decoded.model_year <= year + 1:
        found.append("vin_year")
    return found
