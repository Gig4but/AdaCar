"""auto.bazos.cz: classified car ads, one category per make, HTML only.

robots.txt disallows the search and locality filters, so each make's category is walked
(`/<category>/<offset>/`, 40 per page) newest first, and stops at the first page with no
new ad. Every place is kept (filters choose the region), so "nothing new" is reliable.
A first run reads every make to its end (Škoda alone has ~31 000 ads, ~800 pages): later runs
stop at the first known page, so what a first run leaves out would never be read.

Ads last 30 days unless the seller renews them, which brings them back to the top: an ad
unseen for 30 days is gone.

Ads are free text written by anyone: the card gives the title, price, place (with PSČ)
and date; the detail page the whole text, from which the rules in normalize/cars.py read
year, mileage, power, fuel, gearbox and body. Buyers, scrap dealers and parts sellers are
kept but not listed.
"""

import re
from datetime import datetime
from functools import partial
from urllib.parse import unquote, urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ParsedLocation, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, clean_title, fold, node_text
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "bazos"
BASE = "https://auto.bazos.cz"
PAGE_SIZE = 40
# Category -> make code (None: "other makes", the make is read from the title).
CATEGORIES: dict[str, str | None] = {
    "alfa": "alfa-romeo", "audi": "audi", "bmw": "bmw", "citroen": "citroen", "dacia": "dacia",
    "fiat": "fiat", "ford": "ford", "honda": "honda", "hyundai": "hyundai",
    "chevrolet": "chevrolet", "kia": "kia", "mazda": "mazda", "mercedes": "mercedes-benz",
    "mitsubishi": "mitsubishi", "nissan": "nissan", "opel": "opel", "peugeot": "peugeot",
    "renault": "renault", "seat": "seat", "suzuki": "suzuki", "skoda": "skoda",
    "toyota": "toyota", "volkswagen": "volkswagen", "volvo": "volvo", "ostatni": None,
}  # fmt: skip
AD_PATH = re.compile(r"^(?:https://auto\.bazos\.cz)?/inzerat/(\d+)/[^/]+\.php$")
DATE = re.compile(r"\[(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\]")
PSC = re.compile(r"^\d{3}\s?\d{2}$")
ABROAD = frozenset({"zahranici", "slovensko"})  # places of ads from abroad
SHOWN = re.compile(r"Zobrazeno\s+\d+-(\d+)\s+inzerát\w*\s+z\s+(\d{1,3}(?:\s\d{3})*)")  # "z 31 348"
CATEGORY_OF = re.compile(r"^/([a-z]+)/")


def _int(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def posted(node: LexborNode | None) -> datetime | None:
    """ "- TOP - [6.10. 2026]"."""
    m = DATE.search(node.text() if node is not None else "")
    if not m:
        return None
    try:
        return datetime(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def location(
    places: PlaceResolver, area: str | None, psc: str | None, town: str | None = None
) -> ParsedLocation | None:
    """`area` is a town or city part ("Kolín", "Praha 8"), `psc` picks the municipality,
    `town` is the municipality named on the detail page."""
    psc = psc if psc and PSC.match(psc) else None
    by_psc = places.resolve(None, postal_code=psc) if psc else None
    if town:
        by_name = places.resolve(town, postal_code=psc)
        if by_name is not None and (by_psc is None or by_name.kraj == by_psc.kraj):
            return by_name
    if by_psc is not None:
        return by_psc
    return places.resolve(re.sub(r"\s+-\s+", "-", area)) if area else None


def _lines(node: LexborNode | None) -> list[str]:
    if node is None:
        return []
    return [line for p in node.text(separator="\n").split("\n") if (line := clean_spaces(p))]


def add_facts(car: ParsedCar, make: str | None) -> None:
    """Make, model and the car facts the rules find in the title and text."""
    title, text = car.title, car.description
    car.make = make or make_code(title)
    car.model = model_name(title, car.make)
    car.year = cars.year_in_text(title, text, car.make)
    car.mileage_km = cars.km_in_text(title, text)
    car.power_kw = cars.power_in_text(title, text)
    car.fuel = cars.fuel_in_text(title, text)
    car.gearbox = cars.gearbox_in_text(title, text)
    car.body = cars.body_in_text(title, text)
    if reason := cars.not_offer_reason(title):
        car.extra["not_offer"] = reason


# Words before the model that are not the model: "Prodám Škoda Octavia", "Alfa Romeo Giulia".
FILLER = frozenset({"prodam", "predam", "prodej", "nabizim", "vymenim", "super", "top"})
MAKE_WORDS = frozenset(
    word for names in MAKES.values() for word in re.findall(r"[a-z]+", fold(names["cs"]))
) | {"vw", "merc", "mercedes", "alfa", "landrover", "great", "wall", "motors"}


def model_name(title: str, make: str | None) -> str | None:
    """The first word of the title that is neither a make's name nor a word like "Prodám":
    "Škoda Octavia 2.0" and "Octavia 2.0" -> "Octavia", "Alfa Romeo Giulia" -> "Giulia"."""
    for word in re.findall(r"[^\W\d_][\w-]*", title):
        folded = fold(word)
        if folded in FILLER or folded in MAKE_WORDS or len(word) < 2:
            continue
        return word.capitalize() if word.islower() else word
    return None


class Bazos(CrawlSource):
    id = SOURCE_ID
    label = "Bazoš – auta"
    kind: SourceKind = "classifieds"
    newest_first = True
    nationwide = True
    max_pages = 1500  # per make: more than the largest has
    expire_after_days = 30
    refresh_after_days = None  # private ads rarely change; renewed ones come back as new

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        wanted = set(query.makes)
        return [
            (None, partial(self.category_url, category))
            for category, make in CATEGORIES.items()
            if not wanted or make is None or make in wanted
        ]

    @staticmethod
    def category_url(category: str, page: int) -> str:
        offset = (page - 1) * PAGE_SIZE
        return f"{BASE}/{category}/" + (f"{offset}/" if offset else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        canonical = tree.css_first('link[rel="canonical"]')
        href = (canonical.attributes.get("href") if canonical is not None else None) or ""
        category = CATEGORY_OF.match(href.removeprefix(BASE))
        make = CATEGORIES.get(category[1]) if category else None
        leads = []
        for row in tree.css("div.inzeraty"):
            link = row.css_first("h2.nadpis a")
            m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
            if m is None:
                continue
            url = urljoin(BASE, m[0])
            area, psc = (_lines(row.css_first(".inzeratylok")) + [None, None])[:2]
            snippet = row.css_first(".popis")
            car = ParsedCar(
                external_id=m[1],
                title=clean_title(node_value(link)),
                url=url,
                description=node_text(snippet) if snippet is not None else "",
                place_text=area,
                location=location(places, area, psc),
                posted_at=posted(row.css_first(".velikost10")),
            )
            set_price(car, node_value(row.css_first(".inzeratycena")))
            add_facts(car, make)
            image = row.css_first("img.obrazek")
            if image is not None and (src := image.attributes.get("src")):
                car.images = [src]
            leads.append(Lead(m[1], url, url, car))
        next_page = re.compile(rf"^(?:{re.escape(BASE)})?/[a-z]+/{page * PAGE_SIZE}/$")
        has_next = any(next_page.match(a.attributes.get("href") or "") for a in tree.css("a"))
        # No link: the last page, or a changed layout. The ad count tells which; without it,
        # reading on until an empty page beats taking every page after this as gone.
        shown = SHOWN.search(html)
        if not has_next:
            has_next = _int(shown[1]) < _int(shown[2]) if shown else bool(leads)
        return ListingPage(leads, has_next, none_listed=shown is not None and _int(shown[2]) == 0)

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        card = lead.card
        return card is None or fold(card.place_text or "") not in ABROAD

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first("h1.nadpisdetail")))
        text = tree.css_first("div.popisdetail")
        if not title or text is None:
            return None
        info: dict[str, LexborNode] = {}
        for row in tree.css("td.listadvlevo tr"):
            cells = row.css("td")
            if len(cells) >= 2:
                label = clean_spaces(cells[0].text()).rstrip(":")
                info.setdefault(label, cells[-1])
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text),
            posted_at=posted(tree.css_first(".inzeratydetnadpis .velikost10")),
            seller_name=node_value(info.get("Jméno")),
        )
        place = info.get("Lokalita")
        if place is not None:
            # "<a>530 02</a> <a href="/inzeraty/pardubice-2/53002/">Pardubice</a>": PSČ, town.
            links = place.css("a")
            psc = node_value(links[0]) if links else None
            area = node_value(links[-1]) if len(links) > 1 else None
            href = unquote(links[-1].attributes.get("href") or "") if len(links) > 1 else ""
            m = re.search(r"/inzeraty/([^/]+?)(?:-\d+)?/\d{5}/", href)
            town = m[1].replace("-", " ") if m else None
            car.place_text = area
            car.location = location(places, area, psc, town)
        set_price(car, node_value(info.get("Cena")))
        card_make = lead.card.make if lead.card else None
        add_facts(car, card_make)
        photos = (
            img.attributes.get("data-flickity-lazyload") or img.attributes.get("src")
            for img in tree.css("img.carousel-cell-image")
        )
        car.images = list(dict.fromkeys(p for p in photos if p))[:20]
        return car


def set_price(car: ParsedCar, text: str | None) -> None:
    """Only a price in koruna: an amount in euro on a Czech site cannot be compared with the
    others (the text still shows it)."""
    price = cars.parse_price(text)
    if price and price[1] == "CZK":
        car.price = price[0]


SOURCE = Bazos()
