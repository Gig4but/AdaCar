"""lennermotors.cz: Lenner Motors, a Hyundai, Peugeot and MG dealer in Praha 4 - Braník with
~330 used cars of many makes.

Server-rendered HTML (Drupal). The listing (`/ojete-vozy`) loads 15 more cars at a time, and
asked for `?page=<n>` without JavaScript it shows every car up to that page; so one request for
page 24 (the first 375 cars) reads the whole stock, and a "load more" link on it means there are
more (the next request repeats the first cars, which the crawl skips). Cars the listing no
longer shows expire after such a full read. robots.txt disallows only Drupal's own paths.

A card gives the title, mileage, year, power, fuel, price and the price without VAT and a
photo. The detail page's schema.org Car record and parameters add the VIN, model, gearbox,
body, colour, engine size, first registration, the seller's note, equipment, all photos and the
address of the branch.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "lenner"
BASE = "https://www.lennermotors.cz"
LISTING = BASE + "/ojete-vozy"
AD_PATH = re.compile(r"^/ojete-vozy/([a-z0-9-]+)/([^/]+)/(\d+)$")
PAGES_PER_REQUEST = 25  # of 15 cars
REGISTERED = re.compile(r"(?:\d{1,2}\s*/\s*)?((?:19|20)\d{2})$")
PSC_TOWN = re.compile(r"^(\d{3}\s?\d{2})\s+(.+)$")
SELLER = "Lenner Motors"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def model_name(title: str, make_slug: str, slug: str) -> str | None:
    """ "Hyundai Santa Fe 1.6 T-GDI" with the URL's "santa-fe" -> "Santa Fe"."""
    words = title.split()
    start = _words_of(words, make_slug, 0)
    if start is None or start == len(words) or not make_code(title):
        return None
    end = _words_of(words, slug, start) or start + 1
    return " ".join(words[start:end])


def _words_of(words: list[str], slug: str, start: int) -> int | None:
    """Where the words from `start` that spell `slug` end; None when none do."""
    for end in range(start + 1, len(words) + 1):
        if re.sub(r"[^a-z0-9]+", "-", fold(" ".join(words[start:end]))).strip("-") == slug:
            return end
    return None


def fuel(text: str | None, title: str) -> str | None:
    """ "hybrid" of a car whose title says "PHEV" -> "plugin_hybrid"; "benzín" or "nafta" of
    one whose title says "HEV" or "PHEV" -> "hybrid" or "plugin_hybrid" (not "MHEV" or a bare
    "Hybrid", which mild hybrids are called too)."""
    named = cars.fuel_in_text(text)
    in_title = cars.fuel_in_text(title)
    if named == "hybrid" and in_title == "plugin_hybrid":
        return in_title
    plugged = re.search(r"(?<![a-z])p?hev(?![a-z])|plug-?in", fold(title))
    if named in ("petrol", "diesel") and plugged:
        return in_title
    return named


def gearbox(text: str | None) -> str | None:
    """ "aut. převodovka", "man. převodovka"."""
    folded = fold(text or "")
    if folded.startswith("aut"):
        return "automatic"
    return "manual" if folded.startswith("man") else cars.gearbox_in_text(text)


def town_name(text: str) -> str:
    """ "Praha 4 Braník" -> "Praha 4-Braník", as the resolver reads city parts."""
    return re.sub(r"^(Praha \d+) (\D+)$", r"\1-\2", text)


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first(".teaser__label a[href]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    url = BASE + m[0]
    title = clean_spaces(link.text())
    car = ParsedCar(
        external_id=m[3],
        title=title,
        url=url,
        make=make_code(title),
        model=model_name(title, m[1], m[2]),
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    for item in box.css(".teaser__car-info span"):
        text = node_value(item) or ""
        if text.endswith(" km"):
            car.mileage_km = _number(text)
        elif text.endswith(" kW"):
            car.power_kw = _number(text)
        elif re.fullmatch(r"(19|20)\d\d", text):
            car.year = int(text)
        elif named := fuel(text, title):
            car.fuel = named
    price = cars.parse_price(node_value(box.css_first(".teaser__price-big")))
    if price and price[1] == "CZK":
        car.price = price[0]
    net = node_value(box.css_first(".teaser__price-small")) or ""
    car.vat_deductible = True if "bez DPH" in net else None
    image = box.css_first(".teaser__img img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [BASE + src if src.startswith("/") else src]
    return Lead(m[3], url, url, car)


def car_record(tree: LexborHTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text() or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Car":
            return data
    return None


def parameters(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Karoserie" -> "kombi", "Výkon motoru" -> "117 kW", ..."""
    found: dict[str, str] = {}
    for grid in tree.css(".car-template-1__info-list-grid, .car-template-1__benefits__info-list"):
        for row in grid.css("div > div"):
            kids = [c for c in row.iter() if c.tag == "div"]
            if any(g.tag == "div" for k in kids for g in k.iter()):
                continue
            cells = [node_value(c) for c in kids]
            if len(cells) == 2 and cells[0] and cells[1]:
                found.setdefault(cells[0], cells[1])
    return found


class Lenner(CrawlSource):
    id = SOURCE_ID
    label = "Lenner Motors"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 10

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return f"{LISTING}?page={page * PAGES_PER_REQUEST - 1}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css(".teaser") if (lead := parse_card(box))]
        has_next = tree.css_first('.pager a[rel="next"]') is not None
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        record = car_record(tree)
        if record is None or not record.get("name"):
            return None
        info = parameters(tree)
        offer = record.get("offers") or {}
        title = clean_spaces(record["name"])
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            make=make_code((record.get("brand") or {}).get("name")) or make_code(title),
            model=clean_spaces(record.get("model") or "") or None,
            year=_number(record.get("modelDate")),
            mileage_km=_number(str((record.get("mileageFromOdometer") or {}).get("value") or "")),
            price=_number(str(offer.get("price") or ""))
            if offer.get("priceCurrency") == "CZK"
            else None,
            vat_deductible=True if fold(info.get("Možnost odpočtu DPH") or "") == "ano" else None,
            fuel=fuel(record.get("fuelType") or info.get("Palivo"), title),
            gearbox=gearbox(record.get("vehicleTransmission") or info.get("Převodovka")),
            power_kw=cars.power_in_text(info.get("Výkon motoru")),
            engine_cc=_number(info.get("Objem motoru")),
            color=info.get("Barva exteriér"),
            vin=clean_spaces(record.get("vehicleIdentificationNumber") or "").upper() or None,
            condition="used",
            seller_kind="dealer",
            seller_name=SELLER,
            images=[i for i in record.get("image") or [] if isinstance(i, str)],
        )
        if car.year is None and (m := REGISTERED.search(info.get("Uvedení do provozu") or "")):
            car.year = int(m[1])
        if body := info.get("Karoserie"):
            car.body = cars.body_in_text(body) or "other"
        note = tree.css_first(".car-template-1__short-text")
        if note is not None:
            texts = [t for p in note.css("p") if (t := node_text(p)) != "Poznámka prodejce"]
            car.description = "\n".join(t for t in texts if t)
        car.equipment = list(
            dict.fromkeys(t for n in tree.css(".extras-items__item") if (t := node_value(n)))
        )
        lines = [node_value(s) or "" for s in tree.css(".branch-info .adress strong")]
        if len(lines) == 2 and (m := PSC_TOWN.match(lines[1])):
            car.place_text = m[2]
            car.location = places.resolve(town_name(m[2]), street=lines[0], postal_code=m[1])
        return car


SOURCE = Lenner()
