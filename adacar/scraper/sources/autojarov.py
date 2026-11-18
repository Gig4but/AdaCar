"""autojarov.cz: Auto Jarov, a Prague dealer group (~750 used cars at two branches), HTML.

robots.txt disallows every URL with a query and filter paths with many underscores, so the
used-car listing is read by its plain paths (`/nabidka-vozu/typy_ojete/<n>/`, 12 cars per
page), the whole of it on every run (~60 pages); cars it no longer lists expire then.

A card gives the title, price, gearbox, fuel, power, mileage, first registration, a photo and
the branch the car stands at ("Skladem: Praha 4 ‑ Kunratice"). The detail page adds engine
size, colour, doors, equipment, all photos and the branch's address.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ParsedLocation, ScrapeQuery
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "autojarov"
BASE = "https://www.autojarov.cz"
LISTING = BASE + "/nabidka-vozu/typy_ojete/"
AD_PATH = re.compile(r"^(?:https://www\.autojarov\.cz)?(/nabidka-vozu/[a-z0-9-]+-(\d+)\.html)$")
REGISTERED = re.compile(r"^(?:\d{1,2}/)?((?:19|20)\d{2})$")
ADDRESS = re.compile(r"^(?P<street>.+?),\s*(?P<psc>\d{3}\s?\d{2})\s+(?P<town>.+)$")
SINGLE_QUOTED = re.compile(r":\s*'([^'\"]*)'")
SELLER = "Auto Jarov"
PHOTOS = "/uploads/images/vehicles/"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def place_name(text: str | None) -> str | None:
    """ "Praha 4 ‑ Kunratice" (with a non-breaking hyphen) -> "Praha 4 - Kunratice"."""
    name = clean_spaces(re.sub(r"\s*[‐‑–-]\s*", " - ", text or ""))
    return name or None


def stock_place(node: LexborNode | None) -> str | None:
    """The branch named after "Skladem:"."""
    span = node.css_first("span") if node is not None else None
    return place_name(node_value(span))


def set_param(car: ParsedCar, text: str) -> None:
    """One of a card's unlabelled parameters: "96 kW", "22 914 km", "11/2025", "benzín"."""
    folded = text.lower()
    if m := REGISTERED.match(text):
        car.year = int(m[1])
    elif folded.endswith(" km"):
        car.mileage_km = _number(text)
    elif folded.endswith(" kw"):
        car.power_kw = _number(text)
    elif gearbox := cars.gearbox_in_text(text):
        car.gearbox = gearbox
    elif fuel := cars.fuel_in_text(text):
        car.fuel = fuel


def model_name(text: str | None) -> str | None:
    """ "Octavia Combi Ambition 2,0 TDI", "OCTAVIA COMBI" -> "Octavia"."""
    words = (text or "").split()
    if not words:
        return None
    return words[0].capitalize() if words[0].isupper() and len(words[0]) > 3 else words[0]


class AutoJarov(CrawlSource):
    id = SOURCE_ID
    label = "Auto Jarov"
    nationwide = True
    max_pages = 100

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"{page}/" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for box in tree.css("a.vehicle-smallCard"):
            m = AD_PATH.match(box.attributes.get("href") or "")
            heading = box.css_first("h4")
            if m is None or heading is None:
                continue
            url = BASE + m[1]
            make_text = next(
                (t for t in heading.text(deep=False, separator="\n").split("\n") if t.strip()), ""
            )
            car = ParsedCar(
                external_id=m[2],
                title=clean_spaces(box.attributes.get("title") or "") or clean_spaces(make_text),
                url=url,
                make=make_code(make_text) or make_code(box.attributes.get("title")),
                model=model_name(node_value(heading.css_first("b"))),
                condition="used",
                seller_kind="dealer",
                seller_name=SELLER,
            )
            set_price(car, node_value(box.css_first(".prices b")))
            car.body = cars.body_in_text(car.title)
            for item in box.css("ul.parameters li"):
                if text := node_value(item):
                    set_param(car, text)
            car.place_text = stock_place(box.css_first(".stockStatus"))
            car.location = places.resolve(car.place_text) if car.place_text else None
            image = box.css_first("img")
            src = (image.attributes.get("src") or "") if image is not None else ""
            if src.startswith(PHOTOS):  # not the "no photo" placeholder
                car.images = [BASE + src]
            leads.append(Lead(m[2], url, url, car))
        next_path = f"/nabidka-vozu/typy_ojete/{page + 1}/"
        has_next = any(
            (a.attributes.get("href") or "").removeprefix(BASE) == next_path
            for a in tree.css("a[href]")
        )
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        product = product_record(tree)
        heading = tree.css_first(".vehicle-top-info")
        if product is None or heading is None:
            return None
        offer = product.get("offers") or {}
        brand = (product.get("brand") or {}).get("name")
        h1 = tree.css_first("h1 span")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(product.get("name") or ""),
            url=lead.url,
            make=make_code(brand),
            model=model_name(node_value(h1)),
            condition="used",
            seller_kind="dealer",
            seller_name=SELLER,
        )
        if offer.get("priceCurrency") == "CZK":
            car.price = _number(str(offer.get("price") or "")) or None
        info = main_parameters(tree)
        car.fuel = cars.fuel_in_text(info.get("Palivo"))
        # "automatická" alone: the rules want "automat" or "automatická převodovka".
        car.gearbox = cars.gearbox_in_text(f"{info.get('Převodovka') or ''} převodovka")
        car.engine_cc = _number(info.get("Objem"))
        car.power_kw = cars.power_in_text(info.get("Výkon"))
        car.color = info.get("Barva")
        car.doors = _number(info.get("Počet dveří"))
        car.mileage_km = _number(info.get("Stav tachometru"))
        if m := REGISTERED.match(info.get("Do provozu") or ""):
            car.year = int(m[1])
        car.body = cars.body_in_text(info.get("Karoserie"), car.title)
        car.equipment = list(
            dict.fromkeys(t for li in tree.css("ul.equipments li") if (t := node_value(li)))
        )
        if seats := next((e for e in car.equipment if e.startswith("Počet míst")), None):
            car.seats = _number(seats)
        car.place_text = next(
            (stock_place(li) for li in heading.css("li") if "Skladem" in li.text()), None
        )
        car.location = branch_location(tree, car.place_text, places)
        photos = (a.attributes.get("href") or "" for a in tree.css(".gallery a[href]"))
        car.images = list(dict.fromkeys(BASE + p for p in photos if p.startswith(PHOTOS)))
        return car if car.title else None


def set_price(car: ParsedCar, text: str | None) -> None:
    price = cars.parse_price(text)
    if price and price[1] == "CZK":
        car.price = price[0]


def product_record(tree: LexborHTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        # The site writes some values in single quotes ("priceCurrency": 'CZK').
        text = SINGLE_QUOTED.sub(r': "\1"', script.text() or "")
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return None


def main_parameters(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Objem" -> "1 498 ccm", "Barva" -> "černá", ..."""
    found: dict[str, str] = {}
    for item in tree.css("ul.mainParameters li span"):
        value = node_value(item.css_first("b"))
        label = node_value(item)
        if label and value:
            found.setdefault(label, value)
    return found


def branch_location(
    tree: LexborHTMLParser, place: str | None, places: PlaceResolver
) -> ParsedLocation | None:
    """The branch's address in the contacts ("Vídeňská 126a, 14800 Praha 4 ‑ Kunratice"),
    when it is the branch the car stands at; else the branch's name alone."""
    for node in tree.css(".branch-section-smallCard address"):
        m = ADDRESS.match(node_value(node) or "")
        if m and place and place_name(m["town"]) == place:
            return places.resolve(place, street=m["street"], postal_code=m["psc"])
    return places.resolve(place) if place else None


SOURCE = AutoJarov()
