"""mototechna.cz: Mototechna, AAA Auto's premium used cars (~1 000 cars), on the same system
as aaaauto.cz (see aaaauto.py): pages carry their data as JSON in `<script id="ng-state">`.

The listing (`/prodej-vozu?page=<n>`, 20 cars per page) ignores a sort order given in the URL,
so the whole of it is read on every run (~50 pages); cars it no longer lists expire then.

A card has make, model, year, mileage, price, fuel, gearbox, body, engine size, colour, photos
and the branch the car stands at (mostly Mototechna in Praha-Hloubětín, some at AAA Auto
branches). The detail page has no JSON state: its schema.org Car record and the parameter
list add power, drive and equipment.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources import aaaauto

SOURCE_ID = "mototechna"
BASE = "https://www.mototechna.cz"
SELLER = "Mototechna"
DRIVES = {"4x4": "4x4", "predni": "front", "zadni": "rear"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", re.sub(r"[.,]\d+\b", "", text or ""))
    return int(digits) if digits else None


def car_record(tree: LexborHTMLParser) -> dict | None:
    """The page's schema.org Car (in a @graph)."""
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text() or "")
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        for node in data.get("@graph") or [data]:
            types = node.get("@type") if isinstance(node, dict) else None
            if types == "Car" or (isinstance(types, list) and "Car" in types):
                return node
    return None


def params(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Výkon" -> "360 kW", "Palivo" -> "Benzín + Plug-in Hybrid", ..."""
    found: dict[str, str] = {}
    for item in tree.css("app-inline-params-item"):
        label = node_value(item.css_first(".inline-params__item-label"))
        value = node_value(item.css_first(".inline-params__item-value"))
        if label and value:
            found.setdefault(label, value)
    return found


class Mototechna(CrawlSource):
    id = SOURCE_ID
    label = "Mototechna"
    nationwide = True
    max_pages = 100
    max_details = 100

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return f"{BASE}/prodej-vozu" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        message = aaaauto.car_list(aaaauto.ng_state(html))
        if message is None:
            return ListingPage([], has_next=False)
        leads = []
        for item in message.get("items") or []:
            if not isinstance(item, dict) or not item.get("id") or item.get("isSold"):
                continue
            car = aaaauto.parse_item(item, places, base=BASE, seller=SELLER)
            leads.append(Lead(car.external_id, car.url or "", car.url, car))
        pagination = message.get("pagination") or {}
        return ListingPage(
            leads,
            has_next=bool(pagination.get("hasMore")),
            none_listed=pagination.get("totalItems") == 0,
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        record = car_record(tree)
        if record is None:
            return None
        info = params(tree)
        offer = record.get("offers") or {}
        brand = (record.get("brand") or {}).get("name")
        engine = ((record.get("vehicleEngine") or {}).get("engineDisplacement") or {}).get("value")
        fuel_text = info.get("Palivo") or record.get("fuelType")
        drive_text = fold(info.get("Pohon") or record.get("driveWheelConfiguration") or "")
        gearbox_text = info.get("Převodovka") or record.get("vehicleTransmission")
        body_text = fold(info.get("Karoserie") or record.get("bodyType") or "")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(record.get("name") or ""),
            url=lead.url,
            make=make_code(brand),
            model=clean_spaces(record.get("model") or "") or None,
            year=_number(str(record.get("vehicleModelDate") or "")),
            mileage_km=_number(str((record.get("mileageFromOdometer") or {}).get("value") or "")),
            price=_number(str(offer.get("price") or ""))
            if offer.get("priceCurrency") == "CZK"
            else None,
            fuel=cars.fuel_in_text(fuel_text),
            gearbox=cars.gearbox_in_text(gearbox_text),
            body=aaaauto.BODIES.get(body_text.replace(" ", "-"), "other") if body_text else None,
            drive=next((code for word, code in DRIVES.items() if word in drive_text), None),
            condition="used",
            power_kw=cars.power_in_text(info.get("Výkon")),
            engine_cc=_number(str(engine or "")),
            color=clean_spaces(record.get("color") or "") or None,
            seats=_number(str(record.get("seatingCapacity") or "")),
            seller_kind="dealer",
            seller_name=SELLER,
            equipment=list(
                dict.fromkeys(
                    text
                    for node in tree.css("app-car-detail-equipment-list .check-list__item")
                    if (text := clean_spaces(node.text()))
                )
            ),
            images=[i for i in record.get("image") or [] if isinstance(i, str)],
        )
        if line := info.get("Stupeň výbavy"):
            car.description = f"Stupeň výbavy: {line}"
        return car if car.title else None


SOURCE = Mototechna()
