"""automaton.cz: Automaton, a Prague dealer (Praha 5 - Košíře) of ~400 newer used cars, in stock
or offered for import from abroad.

A Next.js site whose pages are rendered on the server. The listing (`/nabidka-vozidel?page=<n>`,
10 cars per page, ~40 pages, newest first) is read whole on every run; cars it no longer lists
expire then. robots.txt disallows only /admin, /api/ and /monitoring.

A card gives make and model, the version, year, mileage, power, fuel, price and whether the VAT
is deductible. The detail page's schema.org Car record and parameters add body, colour, engine
size, doors, seats and the dealer's notes, and its equipment list tells an automatic gearbox.
The site shows no VIN. Its photos are links signed for an hour only, so none are kept.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ParsedLocation, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, has_page_link, node_value

SOURCE_ID = "automaton"
BASE = "https://www.automaton.cz"
LISTING = BASE + "/nabidka-vozidel"
AD_PATH = re.compile(r"^/nabidka-vozidel/[a-z0-9-]+-(\d+)$")
SELLER = "Automaton"
# The showroom, where cars in stock stand and imported ones are handed over.
STREET, POSTAL_CODE, TOWN = "Holečkova 657/29", "150 00", "Praha"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def model_name(name: str) -> str | None:
    """ "Mercedes-Benz Třídy E" -> "Třídy E", "Volkswagen Tiguan Allspace" -> "Tiguan Allspace"."""
    first, _, rest = name.partition(" ")
    return rest.strip() or None if make_code(first) else None


def showroom(places: PlaceResolver) -> ParsedLocation | None:
    return places.resolve(TOWN, street=STREET, postal_code=POSTAL_CODE)


def set_tag(car: ParsedCar, text: str) -> None:
    """One of a card's tags: "2023", "55 200km", "140kW", "2,0 l", "benzin"."""
    folded = fold(text)
    if re.fullmatch(r"(19|20)\d\d", text):
        car.year = int(text)
    elif folded.endswith("km"):
        car.mileage_km = _number(text)
    elif folded.endswith("kw"):
        car.power_kw = _number(text)
    elif fuel := cars.fuel_in_text(text):
        car.fuel = fuel


def vat_deductible(node: LexborNode | None) -> bool | None:
    return True if node is not None and "odpoctu dph" in fold(node.text()) else None


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    m = AD_PATH.match(box.attributes.get("href") or "")
    name = clean_spaces(node_value(box.css_first(".title")) or "")
    if m is None or not name:
        return None
    url = BASE + m[0]
    version = clean_spaces(node_value(box.css_first(".subtitle")) or "")
    car = ParsedCar(
        external_id=m[1],
        title=clean_spaces(f"{name} {version}"),
        url=url,
        make=make_code(name),
        model=model_name(name),
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
        place_text=TOWN,
        location=showroom(places),
    )
    price = cars.parse_price(node_value(box.css_first(".price-col .price")))
    if price and price[1] == "CZK":
        car.price = price[0]
    car.vat_deductible = vat_deductible(box.css_first(".price-col .note"))
    for tag in box.css(".tags .tag .text"):
        if text := clean_spaces(tag.text()):  # "15 100<!-- --> km": not node_value
            set_tag(car, text)
    car.gearbox = cars.gearbox_in_text(car.title)
    return Lead(m[1], url, url, car)


def car_record(tree: LexborHTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text() or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Car":
            return data
    return None


def _quantity(record: dict, *keys: str) -> int | None:
    """A number nested in the record: ("vehicleEngine", "enginePower") -> 140."""
    value: object = record
    for key in (*keys, "value"):
        value = value.get(key) if isinstance(value, dict) else None
    return _number(str(value)) if value is not None else None


class Automaton(CrawlSource):
    id = SOURCE_ID
    label = "Automaton"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 100

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("a.product-item") if (lead := parse_card(box, places))]
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        record = car_record(tree)
        if record is None or not record.get("name"):
            return None
        info = {
            label: value
            for node in tree.css(".info")
            if (label := node_value(node.css_first(".label")))
            and (value := node_value(node.css_first(".text")))
        }
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
            vat_deductible=vat_deductible(tree.css_first(".price-col .note")),
            fuel=cars.fuel_in_text(record.get("fuelType")),
            power_kw=_quantity(record, "vehicleEngine", "enginePower"),
            engine_cc=_quantity(record, "vehicleEngine", "engineDisplacement"),
            color=clean_spaces(record.get("color") or "") or None,
            doors=_number(str(record.get("numberOfDoors") or "")),
            seats=_number(str(record.get("seatingCapacity") or "")),
            condition="used",
            seller_kind="dealer",
            seller_name=SELLER,
            place_text=TOWN,
            location=showroom(places),
        )
        if body := record.get("bodyType") or info.get("Karoserie"):
            car.body = cars.body_in_text(body) or "other"
        car.equipment = list(
            dict.fromkeys(t for n in tree.css(".equipment-item .label") if (t := node_value(n)))
        )
        automatic = any(fold(e) == "aut. prevodovka" for e in car.equipment)
        car.gearbox = "automatic" if automatic else cars.gearbox_in_text(title)
        if "4x4" in fold(title) or "pohon 4x4" in {fold(e) for e in car.equipment}:
            car.drive = "4x4"
        notes = (node_value(n) for n in tree.css(".note-wrap .text"))
        car.description = "\n".join(n for n in notes if n)
        return car


SOURCE = Automaton()
