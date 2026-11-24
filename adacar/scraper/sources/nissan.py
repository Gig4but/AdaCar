"""nissan-exclusive.cz: Nissan Exclusive, the used cars of Nissan's Czech dealers (~110 cars,
all Nissans, mostly demonstrators and near-new cars under a year old).

Server-rendered HTML. The listing pages its cars by POST requests only, so its sitemap
(`/sitemap.xml`, every car's page) is the listing: one nationwide page read on every run, and
cars it no longer lists expire then. robots.txt asks for one request per 5 seconds and
disallows only the PDF exports.

The sitemap gives only each car's page (`/vuz/<id>-<slug>`). The page has the model and
version, year, mileage, fuel, gearbox, body, drive, power, engine size, the price with and
without VAT, the dealer with its address, the dealer's text, extras and photos; no VIN.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.toyota import fuel

SOURCE_ID = "nissan"
BASE = "https://www.nissan-exclusive.cz"
SITEMAP = BASE + "/sitemap.xml"
AD_URL = re.compile(r"<loc>\s*(https://www\.nissan-exclusive\.cz/vuz/(\d+)-[^<\s]*)\s*</loc>")
DRIVES = {"4x4": "4x4", "4wd": "4x4", "vsech": "4x4", "predni": "front", "zadni": "rear"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def gearbox(text: str | None) -> str | None:
    """ "Auto", "Manuál"."""
    name = fold(text or "")
    if name.startswith("auto"):
        return "automatic"
    return "manual" if name.startswith("manu") else None


def dealer(tree: LexborHTMLParser) -> dict:
    """The dealer on the page's map: title, street, city, zip."""
    script = tree.css_first("script[data-map-data]")
    try:
        points = json.loads(script.text() if script is not None else "").get("points") or []
    except (ValueError, AttributeError):
        return {}
    return points[0] if points and isinstance(points[0], dict) else {}


class Nissan(CrawlSource):
    id = SOURCE_ID
    label = "Nissan ojeté vozy"
    kind: SourceKind = "dealer"
    nationwide = True
    delay = 5.0  # robots.txt: "Request-rate: 1/5s"

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, lambda page: SITEMAP)]

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        leads = {m[2]: Lead(m[2], m[1], m[1]) for m in AD_URL.finditer(html)}
        return ListingPage(
            list(leads.values()), has_next=False, none_listed="<urlset" in html and not leads
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        name = node_value(tree.css_first(".info-box h1"))
        if not name:
            return None
        info: dict[str, str] = {}
        for item in tree.css("#popis .attribute-item, .attribute-box .attribute-item"):
            label = node_value(item.css_first(".attribute-item__label"))
            value = node_value(item.css_first(".attribute-item__value"))
            if label and value:
                info.setdefault(label, value)
        # The short text under the name ends with the version: "..., N-Connecta".
        summary = clean_spaces(node_text(p)) if (p := tree.css_first(".info-box p")) else ""
        version = summary.rpartition(", ")[2] if ", " in summary else ""
        tags = {fold(node_value(t) or "") for t in tree.css(".info-box .tag")}
        drive = fold(info.get("Pohon") or "")
        text = tree.css_first("#popis .structure-content")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {version}"),
            url=lead.url,
            description=node_text(text) if text is not None else "",
            make=make_code(info.get("Značka") or name),
            model=info.get("Model"),
            year=_number(info.get("Rok výroby")),
            mileage_km=_number(info.get("Nájezd")),
            price=_number(node_value(tree.css_first('.price-box [title="Zadaná cena"]'))),
            vat_deductible=True if tree.css_first('.price-box [title="Cena bez DPH"]') else None,
            fuel=fuel(cars.fuel_in_text(info.get("Palivo")), f"{version} {summary}"),
            gearbox=gearbox(info.get("Typ převodovky")),
            body=cars.body_in_text(info.get("Karoserie"))
            or ("other" if info.get("Karoserie") else None),
            drive=next((code for word, code in DRIVES.items() if word in drive), None),
            condition="demo" if "predvadeci vuz" in tags else "used",
            power_kw=cars.power_in_text(info.get("Výkon")),
            engine_cc=_number(info.get("Objem motoru")),
            color=info.get("Barva"),
            seller_kind="dealer",
            equipment=[t for li in tree.css(".specification-list li") if (t := node_value(li))],
        )
        photos = (img.attributes.get("src") for img in tree.css(".product-gallery__main img"))
        car.images = list(dict.fromkeys(p for p in photos if p))
        seller = dealer(tree)
        car.seller_name = seller.get("title") or node_value(tree.css_first(".dealer-box div"))
        car.place_text = node_value(tree.css_first(".dealer-box strong")) or seller.get("city")
        if city := seller.get("city"):
            car.location = places.resolve(
                re.sub(r"\s+[-–]\s+", "-", city),
                street=seller.get("street"),
                postal_code=seller.get("zip"),
            )
        return car


SOURCE = Nissan()
