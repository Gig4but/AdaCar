"""autolouda.cz: Auto Louda, a dealer group of Central Bohemia, Prague and other regions
(~2 100 used cars, besides new and demonstrator cars).

The car search pages its results only through a POST API (`/api/cars/filter-cars`), and a
page number in the listing URL is ignored, so the cars are found in the site's sitemap
instead (`/sitemap-cars.xml`, one request): it lists every car's page with its first photo,
and used cars' photos are under `content.louda.cz/cars-used/`. robots.txt allows both (it
disallows only some filter parameters). The sitemap is read on every run; cars it no longer
lists expire then. Detail pages carry the rest, so a car is listed once its page is read.

A detail page (`/koupit/<slug>-<id>`) has a schema.org Car record (make, model, year, fuel,
gearbox, body, power, colour, doors, price, photos) and a parameter list that adds mileage,
condition, the engine and whether the VAT is deductible; tags tell a first owner. Neither
says at which branch a car stands, and there is no VIN.
"""

import html as htmllib
import re

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.mototechna import car_record

SOURCE_ID = "louda"
BASE = "https://autolouda.cz"
SITEMAP = BASE + "/sitemap-cars.xml"
# A sitemap entry (one <url>) of a car's page with a used car's photo.
USED_CAR = re.compile(
    r"<loc>(https://autolouda\.cz/koupit/[a-z0-9-]+-(\d+))</loc>.*"
    r"<image:loc>https://content\.louda\.cz/cars-used/",
    re.S,
)
SELLER = "Auto Louda"
FINANCED = re.compile(r"sleva pri financovani\s*(\d[\d .]*)\s*kc")
CONDITIONS = {"ojete": "used", "predvadeci": "demo", "nove": "new"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def params(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Nájezd" -> "99 527 km", "Odpočet DPH" -> "Bez odpočtu DPH", ..."""
    found: dict[str, str] = {}
    for item in tree.css(".car-profile-content-param > div.col-xs-6"):
        parts = [clean_spaces(node.text()) for node in item.iter()]
        if len(parts) >= 2 and parts[0] and parts[1]:
            found.setdefault(parts[0], parts[1])
    return found


def text_of(record: dict, key: str) -> str:
    """The record's values carry HTML entities ("manu&#225;l")."""
    return clean_spaces(htmllib.unescape(str(record.get(key) or "")))


class Louda(CrawlSource):
    id = SOURCE_ID
    label = "Auto Louda"
    kind: SourceKind = "dealer"
    default_enabled = False  # its cars have no place, so distance and region filters miss them
    nationwide = True
    max_pages = 1

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, lambda page: SITEMAP)]

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        entries = (USED_CAR.search(entry) for entry in html.split("<url>"))
        leads = {m[2]: Lead(m[2], m[1], m[1]) for m in entries if m}
        return ListingPage(list(leads.values()), has_next=False)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        record = car_record(tree)
        title = node_value(tree.css_first("h1"))
        if record is None or not title:
            return None
        info = params(tree)
        offer = record.get("offers") or {}
        engine = info.get("Motor") or text_of(record, "engineDisplacement")
        body = info.get("Karoserie") or text_of(record, "vehicleType")
        vat = fold(info.get("Odpočet DPH") or "")
        badges = {fold(node_value(b) or "") for b in tree.css(".car-badge-tag")}
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            make=make_code(text_of(record.get("brand") or {}, "name")),
            model=text_of(record, "model") or None,
            year=_number(str(record.get("manufacturerYear") or "")),
            mileage_km=_number(info.get("Nájezd")),
            price=_number(str(offer.get("price") or ""))
            if offer.get("priceCurrency") == "CZK"
            else None,
            vat_deductible=True if vat.startswith("s odpoct") else None,
            fuel=cars.fuel_in_text(info.get("Palivo") or text_of(record, "fuelType")),
            # "Automatická" alone: the rules want "automatická převodovka".
            gearbox=cars.gearbox_in_text(f"{info.get('Převodovka') or ''} převodovka"),
            body=cars.body_in_text(body) or ("other" if body else None),
            drive="4x4" if "4x4" in fold(f"{engine} {title}") else None,
            condition=CONDITIONS.get(fold(info.get("Stav") or "")),
            power_kw=_number(str((record.get("powerOutput") or {}).get("value") or "")),
            color=text_of(record, "color") or None,
            doors=_number(text_of(record, "numberOfDoors")),
            first_owner=True if "prvni majitel" in badges else None,
            seller_kind="dealer",
            seller_name=SELLER,
            equipment=list(
                dict.fromkeys(
                    t
                    for li in tree.css(".car-profile-content-param-accordion-content li")
                    if (t := node_value(li))
                )
            ),
            images=[i for i in record.get("image") or [] if isinstance(i, str)],
        )
        car.service_book = True if "Servisní knížka" in car.equipment else None
        note = node_value(tree.css_first('[data-id="final-price-note"] .content-text')) or ""
        if car.price and (m := FINANCED.search(fold(note))):
            car.price += _number(m[1]) or 0
        return car


SOURCE = Louda()
