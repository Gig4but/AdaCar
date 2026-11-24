"""prodej-vozu.cz: HAVEX-auto, a Škoda and Volkswagen group dealer of Mladá Boleslav region
(~640 used cars, besides new, commercial and leased cars).

Server-rendered HTML; robots.txt disallows a few internal paths (`/car`, `/import`, ...),
not the listing or the detail pages. The used-car listing (`/ojete-vozy?page=<n>`, 6 cars
per page, ~110 pages; a page number past the end shows the last page) is read whole on
every run; cars it no longer lists expire then. The site says nowhere at which branch a car
stands, so cars have no location.

A card gives the make and model, the version, the cash price, fuel, gearbox, year of first
registration, mileage and a photo. The detail page (`/detail/<id>-<slug>`) adds engine size,
power, colour, whether the VAT is deductible, equipment and all photos; it has no VIN, body
or text.
"""

import json
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.toyota import model_name

SOURCE_ID = "havex"
BASE = "https://prodej-vozu.cz"
LISTING = BASE + "/ojete-vozy"
AD_PATH = re.compile(r"^/detail/(\d+)-[a-z0-9-]+")
PHOTOS = re.compile(r"var photoArray = (\[.*?\]);")  # the gallery's large photos
SELLER = "HAVEX-auto"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def gearbox(text: str | None) -> str | None:
    """ "Manuální", "Automatická"."""
    name = fold(text or "")
    if name.startswith("autom"):
        return "automatic"
    return "manual" if name.startswith("manu") else None


def set_names(car: ParsedCar, name: str, version: str) -> None:
    """ "Škoda Octavia Combi" and " 1.6 TDI Ambition"."""
    car.title = clean_spaces(f"{name} {version}") or "Auto"
    car.make = make_code(name)
    car.model = model_name(name, car.make)
    car.body = cars.body_in_text(name)
    car.fuel = cars.fuel_in_text(version)


def card_values(box: LexborNode) -> dict[str, str]:
    """ "Palivo" -> "Nafta", "Nájezd" -> "162 200 km", ..."""
    found: dict[str, str] = {}
    for row in box.css("tr"):
        key, value = node_value(row.css_first("td.key")), node_value(row.css_first("td.value"))
        if key and value:
            found[key.rstrip(":")] = value
    return found


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first("a.carLink[href]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if m is None:
        return None
    url = BASE + m[0]
    car = ParsedCar(
        external_id=m[1],
        title="",
        url=url,
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    name = node_value(box.css_first(".carHeader")) or ""
    set_names(car, name, node_value(box.css_first(".carTitle")) or "")
    values = card_values(box)
    car.fuel = cars.fuel_in_text(values.get("Palivo")) or car.fuel
    car.gearbox = gearbox(values.get("Převodovka"))
    car.year = _number(values.get("V provozu od"))
    car.mileage_km = _number(values.get("Nájezd"))
    price = cars.parse_price(node_value(box.css_first(".carPrice")))
    car.price = price[0] if price and price[1] == "CZK" else None
    image = box.css_first(".carImage img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [urljoin(BASE, src)]
    return Lead(m[1], url, url, car)


def params(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Objem motoru" -> "1598 cm3", "Výkon motoru" -> "77 kW", ..."""
    found: dict[str, str] = {}
    for item in tree.css("#carSpecs .key"):
        value = item.next
        while value is not None and value.tag != "div":
            value = value.next
        key, text = node_value(item), node_value(value)
        if key and text:
            found.setdefault(key, text)
    return found


class Havex(CrawlSource):
    id = SOURCE_ID
    label = "HAVEX-auto"
    kind: SourceKind = "dealer"
    default_enabled = False  # its cars have no place, so distance and region filters miss them
    nationwide = True
    max_pages = 200  # ~110 pages

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("div.carItem") if (lead := parse_card(box))]
        next_href = f"/ojete-vozy?page={page + 1}"
        has_next = any(
            a.attributes.get("href") == next_href for a in tree.css("#pagination a.next")
        )
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        header = tree.css_first("#carHeader")
        name = node_value(header.css_first(".carTitle")) if header is not None else None
        if header is None or not name:
            return None
        info = params(tree)
        car = ParsedCar(
            external_id=lead.external_id,
            title="",
            url=lead.url,
            condition="used",
            seller_kind="dealer",
            seller_name=SELLER,
        )
        set_names(car, name, node_value(header.css_first(".carSubtitle")) or "")
        car.fuel = cars.fuel_in_text(info.get("Palivo")) or car.fuel
        car.gearbox = gearbox(info.get("Převodovka"))
        car.year = _number(info.get("V provozu od"))
        car.mileage_km = _number(info.get("Stav tachometru"))
        car.engine_cc = _number((info.get("Objem motoru") or "").removesuffix("cm3"))
        car.power_kw = cars.power_in_text(info.get("Výkon motoru"))
        car.color = info.get("Barva")
        price_box = header.css_first(".priceArea .carPrice")
        if price_box is not None:
            price = cars.parse_price(node_value(price_box.css_first(".highlight")))
            car.price = price[0] if price and price[1] == "CZK" else None
            vat = fold(clean_spaces(price_box.text()))
            car.vat_deductible = True if "lze odecist dph" in vat and "nelze" not in vat else None
        car.equipment = list(
            dict.fromkeys(
                t for li in tree.css("#carEquipment .tab-pane li") if (t := node_value(li))
            )
        )
        if m := PHOTOS.search(html):
            photos = json.loads(m[1])
            car.images = list(dict.fromkeys(urljoin(BASE, p) for p in photos if isinstance(p, str)))
        return car


SOURCE = Havex()
