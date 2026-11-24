"""future.drivalia.cz: Future Drivalia, the used-car shop of the car-rental and leasing
company Drivalia (~290 cars, mostly Škoda, 1–4 years old; consumers may return a car within
14 days). The cars stand at Future Store Drivalia, U Okruhu 688, 252 50 Vestec.

WordPress with WooCommerce. The site's own car list is drawn in the browser from POST
requests, but WooCommerce's Store API answers GET requests
(`/wp-json/wc/store/v1/products?lang=cs&orderby=date&order=desc&per_page=100&page=<n>`, newest
first); robots.txt forbids only the admin and cart pages. The shop keeps its sold cars (~2 100
Czech products in all, ~22 pages): the "Status" attribute tells the ones on sale (available
or reserved) from the sold and invoiced ones. A page can hold no car on sale at all, which
would end a walk, so every API page is a listing of its own (`PAGES` of them, the ones past
the end answer an empty list); the whole set is read on every run and a car no longer on sale
expires.

A record gives the make and model, version, price incl. VAT, fuel, gearbox, body, drive,
colour, doors, seats, first owner, service book, equipment and where the car stands. The
detail page (`/vozidla/<id>-cs-<make>-<model>/`) adds the first registration, mileage, power,
engine size, VIN and photos; a sold car's page says only "Toto vozidlo již není dostupné".
"""

import json
import re
from functools import partial
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, html_to_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.arval import fuel
from adacar.scraper.sources.toyota import gearbox, model_name

SOURCE_ID = "drivalia"
BASE = "https://future.drivalia.cz"
API = BASE + "/wp-json/wc/store/v1/products"
FIELDS = "id,name,permalink,short_description,prices,attributes"
PAGE_SIZE = 100
PAGES = 40  # ~22 today; the shop grows by its sold cars
ON_SALE = {"available", "reserved"}
AD_URL = re.compile(r"^https://future\.drivalia\.cz/vozidla/(\d+)-cs-[^/]+/$")
SELLER = "Future Drivalia"
STORE = ("Vestec", "U Okruhu 688", "25250")
DRIVES = {"4x4": "4x4", "predni": "front", "zadni": "rear"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _yes(names: list[str]) -> bool | None:
    return {"ano": True, "ne": False}.get(fold(names[0])) if names else None


def _place(car: ParsedCar, town: str | None, places: PlaceResolver) -> None:
    car.place_text = town
    if town == STORE[0]:
        car.location = places.resolve(town, street=STORE[1], postal_code=STORE[2])
    elif town:
        car.location = places.resolve(town)


def parse_item(item: dict, places: PlaceResolver) -> ParsedCar | None:
    """A Store API product; None for a car no longer on sale."""
    attrs = {
        a.get("taxonomy"): [t["name"] for t in a.get("terms") or [] if t.get("name")]
        for a in item.get("attributes") or []
    }
    status = {fold(s) for s in attrs.get("pa_status") or []}
    m = AD_URL.match(item.get("permalink") or "")
    if m is None or not status & ON_SALE:
        return None
    name = clean_spaces(item.get("name") or "")
    version = html_to_text(item.get("short_description") or "")
    prices = item.get("prices") or {}
    first = {key: (values or [None])[0] for key, values in attrs.items()}
    body = first.get("pa_body")
    make = make_code(name)
    car = ParsedCar(
        external_id=m[1],
        title=clean_spaces(f"{name} {version}") or "Auto",
        url=m[0],
        make=make,
        model=model_name(name, make),
        price=_number(prices.get("price")) if prices.get("currency_code") == "CZK" else None,
        fuel=fuel(first.get("pa_fuel"), version),
        gearbox=gearbox(first.get("pa_gearbox")),
        body=cars.body_in_text(body) or ("other" if body else None),
        drive=DRIVES.get(fold(first.get("pa_drive") or "")),
        condition="used",
        color=first.get("pa_color"),
        doors=_number(first.get("pa_doors")),
        seats=_number(first.get("pa_seats")),
        first_owner=_yes(attrs.get("pa_first_owner") or []),
        service_book=_yes(attrs.get("pa_service_book") or []),
        seller_kind="dealer",
        seller_name=SELLER,
        equipment=attrs.get("pa_equipment") or [],
    )
    _place(car, first.get("pa_location"), places)
    return car


def params(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Nájezd" -> "31 234 km", "VIN" -> ...: each label's value is the next element."""
    found: dict[str, str] = {}
    for label in tree.css("div.uppercase"):
        value = label.next
        while value is not None and value.tag == "-text":
            value = value.next
        if (key := node_value(label)) and (text := node_value(value)):
            found.setdefault(key, text)
    return found


def _meta(tree: LexborHTMLParser, prop: str) -> str:
    node = tree.css_first(f'meta[property="{prop}"]')
    return clean_spaces(node.attributes.get("content") or "") if node is not None else ""


class Drivalia(CrawlSource):
    id = SOURCE_ID
    label = "Future Drivalia"
    kind: SourceKind = "leasing"
    nationwide = True
    max_pages = 1

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, partial(self.api_page, n)) for n in range(1, PAGES + 1)]

    @staticmethod
    def api_page(number: int, page: int = 1) -> str:
        params = {"lang": "cs", "orderby": "date", "order": "desc", "per_page": PAGE_SIZE}
        return f"{API}?{urlencode({**params, 'page': number, '_fields': FIELDS})}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        items = json.loads(html)
        leads = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and (car := parse_item(item, places)):
                leads.append(Lead(car.external_id, car.url or "", car.url, car))
        # A page of sold cars, or one past the end, is an answer too.
        return ListingPage(leads, has_next=False, none_listed=isinstance(items, list))

    def gone(self, html: str) -> bool:
        heading = LexborHTMLParser(html).css_first("h1")
        return heading is not None and "již není dostupné" in (node_value(heading) or "")

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        info = params(tree)
        if not info.get("VIN") and not info.get("Nájezd"):
            return None
        name = _meta(tree, "og:title").removesuffix(" - Future Drivalia")
        version = _meta(tree, "og:description")
        registered = re.search(r"(19|20)\d\d", info.get("Datum první registrace") or "")
        body = info.get("Karoserie")
        photos = [a.attributes.get("href") for a in tree.css("a[data-fancybox]")]
        make = make_code(name)
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {version}") or "Auto",
            url=lead.url,
            make=make,
            model=model_name(name, make),
            year=int(registered[0]) if registered else None,
            mileage_km=_number(info.get("Nájezd")),
            price=_number(_meta(tree, "product:price:amount"))
            if _meta(tree, "product:price:currency") == "CZK"
            else None,
            fuel=fuel(info.get("Typ paliva"), version),
            gearbox=gearbox(info.get("Převodovka")),
            body=cars.body_in_text(body) or ("other" if body else None),
            drive=DRIVES.get(fold(info.get("Pohon kol") or "")),
            condition="used",
            power_kw=_number(info.get("Výkon v kW")),
            engine_cc=_number(info.get("Objem motoru")),
            color=info.get("Barva"),
            doors=_number(info.get("Počet dveří")),
            seats=_number(info.get("Počet míst")),
            vin=(info.get("VIN") or "").upper() or None,
            seller_kind="dealer",
            seller_name=SELLER,
            equipment=[t for li in tree.css("#equip-list li") if (t := node_value(li))],
            images=list(dict.fromkeys(p for p in photos if p)),
        )
        _place(car, info.get("Lokalita"), places)
        return car


SOURCE = Drivalia()
