"""sportovnivozy.cz: SportovníVozy, ads of sports and premium cars from dealers and private
sellers (~7 400 cars), HTML only.

robots.txt disallows the sort orders (`?razeni=`), photo pages and the seller listings; the
"novinky" listing (`/index.php?akce=novinky&strana=<n>`, 30 cars per page, ~250 pages,
~75 kB each) is newest first by itself. It is read whole on every run: a card names no
place, so every car needs its detail page, and the detail pages of older cars are read as
the per-run limit allows. New and sold ads shift the pages during a pass, so a car expires
after 3 days unseen. A sold car's page stays up with a "Datum archivace" (archived on) row,
which `gone()` reads.

A card gives the title, year, mileage, engine size, power, price and a photo. The detail
page adds make, model, version, body, fuel, gearbox, driven wheels, colour, the date of the
ad, whether a dealer's VAT is deductible, equipment, the seller's text, the
VIN (in the link to a history check), the seller (a dealer links to its other cars) with
region and town or address, and the photos.
"""

import json
import re
from datetime import datetime, time
from typing import Any
from urllib.parse import unquote, urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, clean_title, czech_date, fold, node_text
from adacar.normalize.vocab import make_code, make_end
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.dedup import SERIES
from adacar.scraper.sources.bazar_cz import fuel as form_fuel
from adacar.scraper.sources.bazos import MAKE_WORDS, set_price

SOURCE_ID = "sportovnivozy"
BASE = "https://www.sportovnivozy.cz/"
AD_PATH = re.compile(r"^(\d+)-[a-z0-9-]+$")
CARD_NUMBERS = re.compile(r"(?:(\d{4})\s*/\s*)?(?:([\d ]+)\s*Km)?", re.IGNORECASE)
VIN = re.compile(r"vin=([A-HJ-NPR-Z0-9]{17})(?![A-Z0-9])")
PSC = re.compile(r"^\d{3}\s?\d{2}$")
BODIES = {
    "kupe": "coupe", "kabriolet": "cabrio", "cc": "cabrio", "roadster": "cabrio",
    "sportovni hatchback": "hatchback", "sportovni limuzina": "sedan",
    "sportovni kombi": "combi", "suv": "suv", "jina karoserie": "other",
}  # fmt: skip
DRIVES = {"predni": "front", "zadni": "rear", "4x4": "4x4"}
SOLD = re.compile(r"(?<![a-z])(prodano|sold)(?![a-z])(?!\s+v\b)")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) or None if digits else None


def fuel(text: str | None) -> str | None:
    """ "Benzín", "Nafta", "Hybridní pohon", "Elektrický pohon", "Vodíkový pohon", "Ethanol"."""
    folded = fold(text or "")
    if folded.startswith("vodik"):
        return "hydrogen"
    if folded.startswith(("ethanol", "jine")):
        return "other"
    return form_fuel(text)


def gearbox(text: str | None) -> str | None:
    folded = fold(text or "")
    if folded.startswith("autom"):
        return "automatic"
    return "manual" if folded.startswith("manu") else None


def model_name(title: str, make: str | None) -> str | None:
    """The word after the make: "BMW 8 Gran Coupe" -> "8", "Mercedes-Benz S S55L" -> "S"."""
    end = make_end(title)
    words = title[end:].strip(" -,").split() if end is not None else []
    while words and fold(words[0]) in MAKE_WORDS:
        words.pop(0)
    if len(words) > 1 and fold(words[0]) in SERIES:
        return " ".join(words[:2])
    return words[0] if words else None


def labelled(rows: list[LexborNode]) -> dict[str, LexborNode]:
    """Table rows "label: | spacer | value", keyed by the label without its colon."""
    found = {}
    for row in rows:
        cells = row.css("td")
        if len(cells) >= 3 and (label := node_value(cells[0])):
            found.setdefault(label.rstrip(":").strip(), cells[2])
    return found


def product_ld(tree: LexborHTMLParser) -> dict[str, Any]:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text(), strict=False)  # the text keeps its line breaks
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return {}


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first("td.h128 a[href]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    url = urljoin(BASE, m[0])
    title = clean_title(node_value(box.css_first("td.h40 a strong")))
    cells = box.css("td.vt")
    numbers = clean_spaces(cells[1].text(separator=" ")) if len(cells) > 1 else ""
    year_km = CARD_NUMBERS.match(numbers)
    make = make_code(title)
    car = ParsedCar(
        external_id=m[1],
        title=title or "Auto",
        url=url,
        make=make,
        model=model_name(title, make),
        year=int(year_km[1]) if year_km and year_km[1] else None,
        mileage_km=_number(year_km[2]) if year_km else None,
        engine_cc=_number(m2[1]) if (m2 := re.search(r"([\d ]+)\s*ccm", numbers)) else None,
        power_kw=cars.power_in_text(numbers),
    )
    set_price(car, node_value(box.css_first("td.vb strong")))
    image = link.css_first("img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [urljoin(BASE, src)]
    mark_sold(car)
    return Lead(m[1], url, url, car)


def mark_sold(car: ParsedCar) -> None:
    if SOLD.search(fold(car.title)):
        car.extra["not_offer"] = "sold"


class SportovniVozy(CrawlSource):
    id = SOURCE_ID
    label = "SportovníVozy.cz"
    kind: SourceKind = "portal"
    nationwide = True
    max_pages = 300  # every ad, ~250 pages
    expire_after_days = 3

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return BASE + ("novinky" if page == 1 else f"index.php?akce=novinky&strana={page}")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("table.vypisDilo") if (lead := parse_card(box))]
        next_href = f"index.php?akce=novinky&strana={page + 1}"
        has_next = any(a.attributes.get("href") == next_href for a in tree.css("a[href]"))
        return ListingPage(leads, has_next)

    def gone(self, html: str) -> bool:
        return "Datum archivace" in facts_of(LexborHTMLParser(html))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first("h1")))
        facts = facts_of(tree)
        if not title or not facts:
            return None
        value = {k: clean_spaces(v.text(separator=" ")) for k, v in facts.items()}
        notes = labelled(tree.css("table.zalamovaniTextu tr"))
        ld = product_ld(tree)
        make = make_code(value.get("Značka", "")) or make_code(title)
        text = notes.get("Doplňující popis")
        added = czech_date(value.get("Datum vložení"))
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text) if text is not None else "",
            make=make,
            model=value.get("Model") or model_name(title, make),
            year=_number(value.get("Rok výroby")),
            mileage_km=_number((value.get("Najeto") or "").partition("Km")[0]),
            fuel=fuel(value.get("Palivo")),
            gearbox=gearbox(value.get("Převodovka")),
            body=BODIES.get(fold(value.get("Karoserie", ""))),
            drive=DRIVES.get(fold(value.get("Poháněná kola", ""))),
            power_kw=cars.power_in_text(value.get("Výkon")),
            engine_cc=_number(value.get("Zdvihový objem")),
            color=value.get("Barva") or None,
            posted_at=datetime.combine(added, time()) if added else None,
        )
        if (version := value.get("Typ")) and version not in title:
            car.title = f"{title} {version}"
        offer = ld.get("offers") or {}
        if offer.get("priceCurrency") == "CZK":
            car.price = _number(str(offer.get("price") or "")) or None
        if car.price is None:
            set_price(car, node_value(tree.css_first("span.T135pc strong")))
        price_box = tree.css_first("span.T135pc")
        shown = fold(price_box.parent.text()) if price_box and price_box.parent else ""
        car.vat_deductible = True if "odpoctu" in shown or "bez dph" in shown else None
        equipment = notes.get("Nadstandardní výbava")
        if equipment is not None:
            items = re.split(r",|\s\*\s", equipment.text())
            car.equipment = [e for x in items if (e := clean_spaces(x))]
        vin = VIN.search(unquote(html))
        car.vin = vin[1] if vin else None
        images = ld.get("image") or []
        car.images = [i for i in images if isinstance(i, str)]
        self.add_seller(car, tree, places)
        mark_sold(car)
        if self.gone(html):
            car.extra["not_offer"] = "sold"
        return car

    @staticmethod
    def add_seller(car: ParsedCar, tree: LexborHTMLParser, places: PlaceResolver) -> None:
        """ "Prodávající: <h3>name</h3> Kraj: Moravskoslezský <strong>town</strong>"; a dealer's
        address is "Kolbenova 809/31, Praha 1, 19800"."""
        cell = next((td for td in tree.css("td.vt") if "Prodávající:" in (td.text() or "")), None)
        name = cell.css_first("h3") if cell is not None else None
        if cell is None or name is None:
            return
        car.seller_name = node_value(name)
        # Every page recommends a few dealers; only a dealer's own ad links to "its" offer.
        dealer = any("tohoto prodejce" in a.text() for a in tree.css('a[href^="autobazar-"]'))
        car.seller_kind = "dealer" if dealer else "private"
        region = re.search(r"Kraj:\s*([^\n<]+)", cell.text())
        kraj = clean_spaces(region[1]) if region else None
        address = cell.css_first("strong")
        text = clean_spaces(address.text(separator=" ")) if address is not None else ""
        parts = [p.strip() for p in text.split(",")]
        if len(parts) > 3 and not re.search(r"\d", parts[-1]):
            parts.pop()
        psc = parts.pop() if len(parts) > 1 and PSC.match(parts[-1]) else None
        town = re.sub(r"\s+(\d{3,}|\d+/\d+)\w*$", "", parts[-1]) if parts and parts[-1] else None
        street = parts[0] if len(parts) > 1 else None
        car.place_text = town or kraj
        if town or psc:
            car.location = places.resolve(town, street=street, postal_code=psc, region=kraj)
        if car.location is None and kraj:
            car.location = places.resolve(kraj)


def facts_of(tree: LexborHTMLParser) -> dict[str, LexborNode]:
    """The detail's table of facts: rows with an italic label ("Značka:", "Najeto:")."""
    rows = [td.parent for td in tree.css("td.ital") if td.parent is not None]
    return labelled(rows)


SOURCE = SportovniVozy()
