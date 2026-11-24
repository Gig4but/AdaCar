"""autonord.cz: Auto Nord Group, a VW group dealer (Škoda, VW, SEAT, CUPRA) with ~550 used and
demonstration cars at six branches in northern Bohemia (Terezín, Roudnice nad Labem, Děčín,
Česká Lípa, Jablonec nad Nisou).

A Next.js site: pages carry their data in the React Server Components payload
(`self.__next_f.push(...)` scripts). The used-car listing (`/vozy?stav=used&page=<n>`, 24 cars
per page, ~23 pages, ordered by the day a car came in) is read whole on every run; cars it no
longer lists expire then. robots.txt disallows only /admin, /api and preview URLs.

A card gives make, model, version, year, mileage, fuel (often empty), gearbox, power, body,
colour, price, a photo, whether it is a demonstrator and the branch. The detail page adds the
VIN, engine size, drive, price without VAT, first owner, service book, country of origin, text,
equipment, all photos and the branch's address.
"""

import json
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, has_page_link

SOURCE_ID = "autonord"
BASE = "https://www.autonord.cz"
LISTING = BASE + "/vozy?stav=used"
FLIGHT = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)
SELLER = "Auto Nord"
FUELS = {
    "petrol": "petrol",
    "diesel": "diesel",
    "bev": "electric",
    "phev": "plugin_hybrid",
    "hybrid": "hybrid",
    "lpg": "lpg",
    "cng": "cng",
}
GEARBOXES = {"automatic": "automatic", "manual": "manual"}
DRIVES = {"awd": "4x4", "4x4": "4x4", "fwd": "front", "rwd": "rear"}
COUNTRIES = {"CZ": "Česká republika"}


def flight_data(html: str) -> str:
    """The text of the page's React Server Components payload."""
    chunks = []
    for m in FLIGHT.finditer(html):
        try:
            data = json.loads(m[1])
        except ValueError:
            continue
        if len(data) > 1 and isinstance(data[1], str):
            chunks.append(data[1])
    return "".join(chunks)


def value_after(flight: str, key: str) -> object:
    """The JSON value written after the first `"key":` in the payload."""
    at = flight.find(f'"{key}":')
    if at < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(flight, at + len(key) + 3)[0]
    except ValueError:
        return None


def vehicle(flight: str, vehicle_id: str) -> tuple[dict, dict] | None:
    """The detail page's record of one car and of the branch it stands at."""
    decoder = json.JSONDecoder()
    for m in re.finditer(re.escape(f'{{"v":{{"id":"{vehicle_id}"'), flight):
        try:
            data = decoder.raw_decode(flight, m.start())[0]
        except ValueError:
            continue
        if isinstance(data.get("v"), dict) and isinstance(data.get("branch"), dict):
            return data["v"], data["branch"]
    return None


def town(branch_label: str) -> str:
    """ "Česká Lípa – Sluneční" (the branch in a street of the town) -> "Česká Lípa"."""
    return re.split(r"\s+[–-]\s+", branch_label)[0]


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def variant_fuel(variant: str) -> str | None:
    """ "1.4 TSI iV DSG", "1.4 TSI GTE", "1.4 TSI e-Hybrid" -> "plugin_hybrid"; else as named."""
    if re.search(r"tsi\s*(?:iv|gte)(?![a-z])|e-?hybrid|(?<![a-z])gte(?![a-z])", fold(variant)):
        return "plugin_hybrid"
    return cars.fuel_in_text(variant)


def parse_item(item: dict, url: str) -> ParsedCar:
    """A car from a listing item; the detail page's record has the same fields and more."""
    model = clean_spaces(item.get("model") or "")
    variant = clean_spaces(item.get("variant") or "")
    brand = item.get("brandLabel") or ""
    car = ParsedCar(
        external_id=item["id"],
        title=clean_spaces(f"{brand} {model} {variant}"),
        url=url,
        make=make_code(brand) or make_code(item.get("brand")),
        model=model or None,
        year=_int(item.get("year")),
        mileage_km=_int(item.get("mileage")),
        price=_int(item.get("price")),
        fuel=FUELS.get(item.get("fuel") or "") or variant_fuel(variant),
        gearbox=GEARBOXES.get(item.get("transmission") or ""),
        power_kw=_int(item.get("powerKw")),
        color=clean_spaces(item.get("color") or "") or None,
        condition="demo" if item.get("isDemoCar") else "used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    if body := item.get("bodyType"):
        car.body = cars.body_in_text(body) or "other"
    if image := item.get("primaryImageUrl"):
        car.images = [image]
    return car


class AutoNord(CrawlSource):
    id = SOURCE_ID
    label = "Auto Nord"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 60

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"&page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        flight = flight_data(html)
        items = value_after(flight, "initialItems")
        options = value_after(flight, "branchOptions")
        branches = {
            b["slug"]: b["label"]
            for b in (options if isinstance(options, list) else [])
            if isinstance(b, dict) and b.get("slug") and b.get("label")
        }
        # The listing data has the car's own slug, the links its make and model in the path too.
        links = {
            href.rsplit("/", 1)[-1]: href
            for a in tree.css('a[href^="/vozy/"]')
            if "-id-" in (href := a.attributes.get("href") or "")
        }
        leads = []
        for item in items if isinstance(items, list) else []:
            href = links.get(item.get("slug") or "") if isinstance(item, dict) else None
            if not href or not item.get("id"):
                continue
            url = urljoin(BASE, href)
            car = parse_item(item, url)
            if label := branches.get(item.get("locationSlug") or ""):
                car.place_text = town(label)
                car.location = places.resolve(car.place_text)
            leads.append(Lead(car.external_id, url, url, car))
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        found = vehicle(flight_data(html), lead.external_id)
        if found is None:
            return None
        data, branch = found
        car = parse_item(data, lead.url)
        car.vin = clean_spaces(data.get("vin") or "").upper() or None
        car.engine_cc = _int(data.get("engineDisplacementCcm"))
        car.drive = DRIVES.get(fold(data.get("driveType") or ""))
        car.vat_deductible = True if data.get("priceWithoutVat") else None
        car.first_owner = data.get("isFirstOwner")
        car.service_book = data.get("hasServiceBook")
        origin = data.get("originCountry")
        car.origin_country = COUNTRIES.get(origin, origin) if origin else None
        car.description = (data.get("description") or "").strip()
        car.equipment = list(dict.fromkeys(e for e in data.get("equipment") or [] if e))
        photos = (i.get("url") for i in data.get("images") or [] if isinstance(i, dict))
        car.images = list(dict.fromkeys(p for p in photos if p)) or car.images
        if city := branch.get("city"):
            car.place_text = city
            car.location = places.resolve(
                city, street=branch.get("address"), postal_code=branch.get("zip")
            )
        return car


SOURCE = AutoNord()
