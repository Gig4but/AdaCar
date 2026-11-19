"""The listed cars as a CSV or Excel file. Private sellers' names and the contacts are left
out: they are personal data (PLAN §12)."""

import csv
import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from html import escape

from sqlalchemy.engine import Row

from adacar.db.models import Car, Location
from adacar.i18n import translate
from adacar.normalize.vocab import label
from adacar.scraper.sources import LABELS as SOURCE_LABELS

COLUMNS = (
    "title",
    "make",
    "model",
    "year",
    "km",
    "price",
    "price_was",
    "currency",
    "fuel",
    "gearbox",
    "power",
    "body",
    "place",
    "kraj",
    "seller",
    "vin",
    "posted",
    "sources",
    "link",
)


# What `table` reads.
CAR_COLUMNS = (
    Car.id,
    Car.title,
    Car.make,
    Car.model,
    Car.year,
    Car.mileage_km,
    Car.price,
    Car.price_currency,
    Car.price_was,
    Car.fuel,
    Car.gearbox,
    Car.power_kw,
    Car.body,
    Car.place_text,
    Location.municipality,
    Location.kraj,
    Car.seller_kind,
    Car.seller_name,
    Car.vin,
    Car.posted_at,
)


def table(cars: Iterable[Row], ads: dict[int, list[Row]], lang: str) -> Iterator[list[str]]:
    """The header, then one row per car (rows of CAR_COLUMNS, ads by car id). Numbers are
    plain digits, so a spreadsheet can sort and add them."""
    yield [translate(lang, f"export.{c}") for c in COLUMNS]
    for car in cars:
        listed = ads.get(car.id, [])
        offered = [a for a in listed if a.status == "active"] or listed
        names = dict.fromkeys(SOURCE_LABELS.get(a.source_id) or a.source_id for a in offered)
        primary = next((a for a in offered if a.is_primary), offered[0] if offered else None)
        dealer = car.seller_kind == "dealer"
        yield [
            car.title,
            label("makes", car.make, lang),
            car.model or "",
            str(car.year or ""),
            str(car.mileage_km) if car.mileage_km is not None else "",
            str(car.price) if car.price is not None else "",
            str(car.price_was or ""),
            car.price_currency if car.price is not None else "",
            label("fuels", car.fuel, lang),
            label("gearboxes", car.gearbox, lang),
            str(car.power_kw or ""),
            label("bodies", car.body, lang),
            car.place_text or car.municipality or "",
            car.kraj or "",
            " ".join(
                filter(
                    None,
                    [
                        label("seller_kinds", car.seller_kind, lang),
                        car.seller_name if dealer else None,
                    ],
                )
            ),  # fmt: skip
            car.vin if car.vin and len(car.vin) == 17 else "",
            f"{car.posted_at:%Y-%m-%d}" if car.posted_at else "",
            ", ".join(names),
            (primary.url if primary else None) or "",
        ]


def to_csv(rows: Iterable[list[str]]) -> bytes:
    """Semicolons and a BOM, as Excel with Czech settings opens it. Text that a spreadsheet
    would run as a formula gets a leading apostrophe."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    for row in rows:
        writer.writerow(["'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v for v in row])
    return out.getvalue().encode("utf-8-sig")


def to_xlsx(rows: Iterable[list[str]]) -> bytes:
    """A one-sheet workbook. Whole numbers are numbers, every other cell is inline text, so
    nothing in an ad runs as a formula."""
    lines = []
    for r, row in enumerate(rows, 1):
        cells = "".join(
            f'<c r="{_column(c)}{r}"><v>{v}</v></c>'
            if _NUMBER.fullmatch(v)
            else f'<c r="{_column(c)}{r}" t="inlineStr"><is><t{_KEEP}>{_xml(v)}</t></is></c>'
            for c, v in enumerate(row)
            if v
        )
        lines.append(f'<row r="{r}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{_MAIN}"><sheetData>{"".join(lines)}</sheetData></worksheet>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in _XLSX_PARTS.items():
            z.writestr(name, text)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()


def _column(index: int) -> str:
    """0 -> "A", 25 -> "Z", 26 -> "AA"."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _xml(text: str) -> str:
    """Escaped, without the control characters XML does not allow."""
    return escape(_CONTROL.sub("", text), quote=False)


_KEEP = ' xml:space="preserve"'  # leading and trailing spaces stay
_NUMBER = re.compile(r"[1-9][0-9]{0,14}")  # no leading zero: such text stays text
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_XLSX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    ),
    "_rels/.rels": (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{_PKG}">'
        f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    ),
    "xl/workbook.xml": (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{_MAIN}" '
        f'xmlns:r="{_REL}"><sheets><sheet name="AdaCar" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    ),
    "xl/_rels/workbook.xml.rels": (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{_PKG}">'
        f'<Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    ),
}
