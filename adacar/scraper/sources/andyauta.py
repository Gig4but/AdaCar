"""andy-auta.cz: Andy-auta, a dealer of ~340 used and nearly new cars at Tábor and České
Budějovice.

Server-rendered HTML. The listing (`/auta?page=<n>`, 24 cars per page, ~15 pages, by price,
highest first) is read whole on every run; cars it no longer lists expire then. robots.txt
disallows internal paths (/_auta/, /_xml-feed/, /fotodokumentace/ and the like), none needed.

A card gives make, model, version, first registration, fuel, mileage, price (and the price
without VAT when it is deductible), photos and the branch the car stands at (its stock number
ends "TA" or "CB"). The detail page's schema.org Car record adds the VIN, body, gearbox, power,
engine size, colour, doors, seats and text; the page adds the seller's note, equipment and all
photos.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, has_page_link, node_value

SOURCE_ID = "andyauta"
BASE = "https://www.andy-auta.cz"
LISTING = BASE + "/auta"
AD_PATH = re.compile(r"^/auto/[a-z]\d+-[a-z0-9-]+-(\d+)$")
REGISTERED = re.compile(r"^(?:\d{1,2}/)?((?:19|20)\d{2})$")
SELLER = "Andy-auta"
# Branch code at the end of a card's stock number -> town, street, postal code.
BRANCHES = {
    "TA": ("Tábor", "Vožická 3150", "390 02"),
    "CB": ("České Budějovice", "Slévárenská 758/2", "370 01"),
}
TOWNS = {town: code for code, (town, _, _) in BRANCHES.items()}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def branch(car: ParsedCar, code: str | None, places: PlaceResolver) -> None:
    if code not in BRANCHES:
        return
    town, street, postal_code = BRANCHES[code]
    car.place_text = town
    car.location = places.resolve(town, street=street, postal_code=postal_code)


def photo(path: str | None) -> str | None:
    return BASE + path if path and path.startswith("/photos/") else None


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    link = box.css_first('a[href^="/auto/"]')
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    heading = box.css_first("h3.card-title")
    if link is None or m is None or heading is None or box.css_first(".prodano-text"):
        return None
    url = BASE + m[0]
    make = node_value(heading.css_first("span"))
    name = clean_spaces(heading.text())
    version = clean_spaces(node_value(box.css_first(".card-subtitle")) or "")
    car = ParsedCar(
        external_id=m[1],
        title=clean_spaces(f"{name} {version}"),
        url=url,
        make=make_code(make),
        model=clean_spaces(name[len(make or "") :]) or None,
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    stats = {
        node_value(n.css_first(".stat-label")): node_value(n.css_first(".stat-value"))
        for n in box.css(".card-body .text-center")
    }
    if m_year := REGISTERED.match(stats.get("Do provozu") or ""):
        car.year = int(m_year[1])
    car.fuel = cars.fuel_in_text(stats.get("Palivo"))
    car.mileage_km = _number(stats.get("Nájezd"))
    price = cars.parse_price(node_value(box.css_first(".price-main")))
    if price and price[1] == "CZK":
        car.price = price[0]
    net = node_value(box.css_first(".price-sub")) or ""
    car.vat_deductible = True if "bez DPH" in net else None
    code = (node_value(box.css_first(".ev-value")) or "").split()[-1:]
    branch(car, code[0] if code else None, places)
    gallery = box.css_first("[data-photos]")
    try:
        shots = json.loads(gallery.attributes.get("data-photos") or "[]") if gallery else []
    except ValueError:
        shots = []
    car.images = [p for s in shots if isinstance(s, dict) and (p := photo(s.get("jpg")))]
    return Lead(m[1], url, url, car)


def car_record(tree: LexborHTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text() or "")
        except ValueError:
            continue
        if isinstance(data, dict) and "Car" in (data.get("@type") or []):
            return data
    return None


def _quantity(record: dict, *keys: str) -> int | None:
    """A number nested in the record: ("vehicleEngine", "enginePower") -> 210."""
    value: object = record
    for key in (*keys, "value"):
        value = value.get(key) if isinstance(value, dict) else None
    return _number(str(value)) if value is not None else None


class AndyAuta(CrawlSource):
    id = SOURCE_ID
    label = "Andy-auta"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 50

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        container = tree.css_first("#cars-list-container")
        boxes = container.css(".car-card-item") if container is not None else []
        leads = [lead for box in boxes if (lead := parse_card(box, places))]
        sold = any(box.css_first(".prodano-text") for box in boxes)
        return ListingPage(leads, has_page_link(tree, "page", page + 1), not leads and sold)

    def gone(self, html: str) -> bool:
        return LexborHTMLParser(html).css_first("#gallery-wrapper .prodano-text") is not None

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        record = car_record(tree)
        if record is None or not record.get("name"):
            return None
        offer = record.get("offers") or {}
        title = clean_spaces(record["name"])
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            make=make_code((record.get("brand") or {}).get("name")) or make_code(title),
            model=clean_spaces(record.get("model") or "") or None,
            year=_number(str(record.get("productionDate") or "")),
            mileage_km=_quantity(record, "mileageFromOdometer"),
            price=_number(str(offer.get("price") or ""))
            if offer.get("priceCurrency") == "CZK"
            else None,
            fuel=cars.fuel_in_text(record.get("fuelType")),
            gearbox=cars.gearbox_in_text(f"{record.get('vehicleTransmission') or ''} převodovka"),
            power_kw=_quantity(record, "vehicleEngine", "enginePower"),
            engine_cc=_quantity(record, "vehicleEngine", "engineDisplacement"),
            color=clean_spaces(record.get("color") or "") or None,
            doors=_number(str(record.get("numberOfDoors") or "")),
            seats=_number(str(record.get("vehicleSeatingCapacity") or "")),
            vin=clean_spaces(record.get("vehicleIdentificationNumber") or "").upper() or None,
            condition="used",
            seller_kind="dealer",
            seller_name=SELLER,
        )
        if body := record.get("bodyType"):
            car.body = cars.body_in_text(body) or "other"
        net = node_value(tree.css_first(".price-box.cash .sub-price span")) or ""
        car.vat_deductible = True if "bez DPH" in net else None
        town = ((offer.get("seller") or {}).get("address") or {}).get("addressLocality")
        branch(car, TOWNS.get(town or ""), places)
        texts = [(record.get("description") or "").strip()]
        for section in tree.css(".car-detail-section"):
            if node_value(section.css_first("h2")) == "Poznámka prodejce":
                texts += [node_text(p) for p in section.css("p")]
        car.description = "\n\n".join(t for t in texts if t)
        car.equipment = list(
            dict.fromkeys(
                t for li in tree.css("#car-equipment-section li") if (t := node_value(li))
            )
        )
        if "4x4" in fold(title) or "pohon 4x4" in {fold(e) for e in car.equipment}:
            car.drive = "4x4"
        thumbs = (a.attributes.get("data-thumb") for a in tree.css(".gallery-main a[data-thumb]"))
        photos = [p for t in thumbs if (p := photo(t))]
        car.images = list(dict.fromkeys(photos)) or [
            i for i in record.get("image") or [] if isinstance(i, str)
        ]
        return car


SOURCE = AndyAuta()
