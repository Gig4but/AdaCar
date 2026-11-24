"""renew.renault.cz: Renault and Dacia RENEW, the used cars of Renault's Czech dealers (~1 000
cars, mostly Renault and Dacia, also trade-ins of other makes).

Server-rendered HTML. robots.txt allows the listing on the home page but disallows every car's
page (/vyhledani-ojeteho-vozidla/) and the search, so the cards are all that is read: one
nationwide listing (`/?cars_per_page=48&carlist_page=<n>`, ~22 pages) read whole on every
run; cars it no longer lists expire then. The link of a car is kept for the user.

A card gives the make and model, the version (engine, often power and gearbox), VIN, colour,
year, mileage, the price with VAT, the start of the dealer's text and a photo. It does not
name the dealer or where the car stands.
"""

import re
from urllib.parse import urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import (
    CrawlSource,
    Lead,
    Listing,
    ListingPage,
    has_page_link,
    node_value,
)
from adacar.scraper.sources.toyota import HP_TO_KW, model_name

SOURCE_ID = "renault"
BASE = "https://renew.renault.cz"
PAGE_SIZE = 48
AD_PATH = re.compile(r"^/vyhledani-ojeteho-vozidla/([^/]+)/?$")
HP = re.compile(r"(?<![\d.,])(\d{2,3})\s?(?:k|hp|ps)(?![a-z])")
NO_PHOTO = "nopic"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def fuel(version: str) -> str | None:
    """From the version: "E-Tech full hybrid 145", "fullhybrid E-Tech200", "1,5 Blue dCi",
    "ECO-G 100" (LPG), "1,6SCe" (petrol), "E-Tech 52 kWh" (electric)."""
    named = cars.fuel_in_text(version)
    if named:
        return named
    folded = fold(version)
    for pattern, code in (
        (r"hybrid", "hybrid"),
        (r"eco-g", "lpg"),
        (r"\d\s*kwh", "electric"),
        (r"\b(?:comfort|urban|long) range\b", "electric"),
        (r"sce(?![a-z])", "petrol"),
    ):
        if re.search(pattern, folded):
            return code
    return None


def power(version: str) -> int | None:
    """ "1,3 TCe 103 kW" -> 103; "1,7dCi 150k" (horsepower) -> 110."""
    if kw := cars.power_in_text(version):
        return kw
    m = HP.search(fold(version))
    return round(int(m[1]) * HP_TO_KW) if m else None


def parse_card(box: LexborNode) -> Lead | None:
    parts = urlsplit(box.attributes.get("href") or "")
    m = AD_PATH.match(parts.path)
    if m is None:
        return None
    url = urlunsplit(("https", parts.netloc or urlsplit(BASE).netloc, parts.path, "", ""))
    vin = clean_spaces(box.attributes.get("data-vin") or "").upper() or None
    external_id = vin or m[1]
    name = node_value(box.css_first(".selection-name")) or ""
    version = clean_spaces(node_value(box.css_first(".selection-version")) or "")
    version = version.removesuffix("&hellip").removesuffix("…").strip()
    text = box.css_first(".selection-descr")
    description = clean_spaces(text.text()) if text is not None else ""
    car = ParsedCar(
        external_id=external_id,
        title=clean_spaces(f"{name} {version}") or "Auto",
        url=url,
        description=description,
        make=make_code(name),
        year=_number(node_value(box.css_first(".selection-year .value-value"))),
        mileage_km=_number(box.attributes.get("data-mileage"))
        or _number(node_value(box.css_first(".selection-km .value-value"))),
        price=_number(node_value(box.css_first(".selection-price-final .price-item strong"))),
        fuel=fuel(version),
        gearbox=cars.gearbox_in_text(version, description),
        body=cars.body_in_text(version),
        condition="used",
        power_kw=power(version),
        color=node_value(box.css_first(".farba-text")),
        vin=vin,
        seller_kind="dealer",
    )
    car.model = model_name(name, car.make)
    photo = box.css_first(".lazy-bg[data-bg-image]")
    src = photo.attributes.get("data-bg-image") if photo is not None else None
    if src and NO_PHOTO not in src:
        car.images = [src]
    return Lead(external_id, url, None, car)


class Renault(CrawlSource):
    id = SOURCE_ID
    label = "Renault a Dacia RENEW"
    kind: SourceKind = "dealer"
    default_enabled = False  # its cars have no place, so distance and region filters miss them
    nationwide = True
    max_pages = 60

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return f"{BASE}/?cars_per_page={PAGE_SIZE}" + (f"&carlist_page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("a.js-vehicle-item") if (lead := parse_card(box))]
        return ListingPage(leads, has_page_link(tree, "carlist_page", page + 1))


SOURCE = Renault()
