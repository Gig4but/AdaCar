"""ar-cars.cz: AR CARS, a dealer of luxury and sports cars in Ostrava-Hrabová (~145 used and
~20 new cars there; its Bratislava showroom's cars, priced in euros, are left out).

The site's car list comes from its own JSON API (`/api/arcars/list`, GET, 60 cars at most per
call, `offset` for the next), which robots.txt allows: used cars by mileage (few ties, so no
car falls between two pages) and new cars, the whole of both on every run (~4 calls); cars
it no longer lists expire then.

An API item gives make, model, version, year, mileage, price, VAT deduction, fuel, gearbox,
drive, body, power, engine size, colour, VIN, the branch and five photos. The car's page
adds the dealer's text, number of seats and owners, and all photos.
"""

import json
from functools import partial

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ParsedLocation, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "arcars"
BASE = "https://www.ar-cars.cz"
API = BASE + "/api/arcars/list"
PAGE_SIZE = 60  # the API's largest page
SELLER = "AR CARS"
# The Ostrava showroom, from the contact page.
TOWN, STREET, PSC = "Ostrava", "Krmelínská 736/7", "72000"
LISTINGS = {"used": "CONDITION_USED", "new": "CONDITION_NEW"}
DRIVES = {"4x4": "4x4", "predni": "front", "zadni": "rear"}
GEARBOXES = {"TRANSMISSION_AUTOMATIC": "automatic", "TRANSMISSION_MANUAL": "manual"}


def _int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and value > 0 else None


def branch_location(places: PlaceResolver) -> ParsedLocation | None:
    return places.resolve(TOWN, street=STREET, postal_code=PSC)


def parse_item(item: dict, places: PlaceResolver) -> ParsedCar | None:
    """One car of the API's list; None for a car at the Bratislava showroom or a motorcycle."""
    if item.get("branchCountry") != "cz" or not item.get("id") or not item.get("url"):
        return None
    body = item.get("bodyStyle")
    if fold(body or "").startswith("motocykl"):
        return None
    return ParsedCar(
        external_id=str(item["id"]),
        title=clean_spaces(item.get("title") or "") or "Auto",
        url=BASE + item["url"],
        make=make_code(item.get("make") or item.get("makeRaw")),
        model=clean_spaces(item.get("model") or "") or None,
        year=_int(item.get("year")),
        mileage_km=item.get("mileage") if isinstance(item.get("mileage"), int) else None,
        price=_int(item.get("priceCZK")),
        vat_deductible=True if item.get("vatDeductible") else None,
        fuel=cars.fuel_in_text(item.get("fuel")),
        gearbox=GEARBOXES.get(item.get("transmissionKey") or ""),
        drive=DRIVES.get(fold(item.get("drive") or "")),
        body=cars.body_in_text(body) or ("other" if body else None),
        condition="new" if item.get("conditionKey") == "CONDITION_NEW" else "used",
        power_kw=_int(item.get("power")),
        engine_cc=_int(item.get("engineVolume")),
        color=item.get("color") or None,
        vin=(item.get("vin") or "").upper() or None,
        seller_kind="dealer",
        seller_name=SELLER,
        place_text=item.get("branch") or TOWN,
        location=branch_location(places),
        images=[i for i in item.get("images") or [] if isinstance(i, str)],
    )


class ArCars(CrawlSource):
    id = SOURCE_ID
    label = "AR CARS"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 10

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, partial(self.api_url, condition)) for condition in LISTINGS.values()]

    @staticmethod
    def api_url(condition: str, page: int) -> str:
        offset = (page - 1) * PAGE_SIZE
        return (
            f"{API}?sort=mileage-asc&condition%5B%5D={condition}&dostupnost=active"
            f"&offset={offset}&limit={PAGE_SIZE}"
        )

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        try:
            data = json.loads(html)
        except ValueError:
            return ListingPage([], has_next=False)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return ListingPage([], has_next=False)
        leads = []
        for item in items:
            car = parse_item(item, places) if isinstance(item, dict) else None
            if car is not None and car.url:
                leads.append(Lead(car.external_id, car.url, car.url, car))
        total = data.get("total")
        if not isinstance(total, int):
            total = 0
        return ListingPage(
            leads,
            has_next=(page - 1) * PAGE_SIZE + len(items) < total and bool(items),
            none_listed=total == 0,
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = node_value(tree.css_first("h1"))
        if not title or tree.css_first("#mainb") is None:
            return None
        text = tree.css_first(".obsahauta")
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text) if text is not None else "",
            seats=_int(_number(node_value(tree.css_first(".pocetmist p")))),
            seller_kind="dealer",
            seller_name=SELLER,
        )
        owners = _number(node_value(tree.css_first(".pocetvlastniku p")))
        car.first_owner = owners == 1 if owners else None
        photos = (img.attributes.get("src") or "" for img in tree.css("#mainb .carimg img"))
        car.images = list(dict.fromkeys(BASE + p for p in photos if p.startswith("/")))
        return car


def _number(text: str | None) -> int | None:
    digits = "".join(c for c in text or "" if c.isdigit())
    return int(digits) if digits else None


SOURCE = ArCars()
