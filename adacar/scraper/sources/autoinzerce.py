"""auto-inzerce.cz: small free classifieds, the "Auto" section (~70 ads, mostly private
sellers, also trailers and lorries), HTML only.

robots.txt only states content signals. The listing (`/vyhledavani.php?load=auto&page=<n>`,
~10 ads per page, ~7 pages, paid TOP ads first) is read whole on every run, so a complete
pass tells which ads are gone; a deleted ad's page says "Inzerát neexistuje".

Sellers fill a form: a card gives a shortened title, price, the start of the text, body,
mileage, gearbox, fuel and year. The detail page adds the whole title and text, make,
model, engine size, power, category, condition, driven wheels, doors, seats, VIN, the
seller's region and photos. Trailers, lorries and buses ("Kategorie" other than cars and
vans) are kept but not listed, like buyers on bazos.
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, clean_title, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import (
    CrawlSource,
    Lead,
    Listing,
    ListingPage,
    has_page_link,
    node_value,
)
from adacar.scraper.sources.bazar_cz import fuel, gearbox, mileage, year
from adacar.scraper.sources.bazos import set_price

SOURCE_ID = "autoinzerce"
BASE = "https://www.auto-inzerce.cz"
LISTING = BASE + "/vyhledavani.php?load=auto"
AD_ID = re.compile(r"^/detail\.php\?id=(\d+)(?:&|$)")
VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
CAR_CATEGORIES = frozenset({"osobni", "uzitkove"})
BODIES = {"terenni": "offroad", "skrin": "van", "sedan/limuzina": "sedan", "ostatni": "other"}
DRIVES = {"4x4": "4x4", "prednich kol": "front", "zadnich kol": "rear"}
CONDITIONS = {
    "nove": "new", "ojete": "used", "predvadeci": "demo", "havarovane": "damaged",
    "veteran": "used",
}  # fmt: skip


def ad_url(external_id: str) -> str:
    """The page without its name part, which may hold spaces ("&name=seat leon cupra 290")."""
    return f"{BASE}/detail.php?id={external_id}"


def body(text: str | None) -> str | None:
    folded = fold(clean_spaces(text or ""))
    return BODIES.get(folded) or cars.body_in_text(folded) if folded else None


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def card_facts(cell: LexborNode) -> dict[str, str]:
    """Two columns: the labels ("Najeto:") and their values, in the same order."""
    labels = [node_value(x) or "" for x in cell.css(".b-items__cars-one-info-title")]
    values = [node_value(x) or "" for x in cell.css(".b-items__cars-one-info-value")]
    return {label.rstrip(":"): value for label, value in zip(labels, values, strict=False)}


def parse_card(cell: LexborNode) -> Lead | None:
    link = cell.css_first('.b-items__cell-info-title a[href^="/detail.php"]')
    m = AD_ID.match(link.attributes.get("href") or "") if link is not None else None
    if m is None:
        return None
    url = ad_url(m[1])
    title = clean_title(re.sub(r"\s*\.\.\.$", "", node_value(link) or ""))
    snippet = cell.css_first(".b-items__cell-info > p")
    facts = card_facts(cell)
    car = ParsedCar(
        external_id=m[1],
        title=title or "Auto",
        url=url,
        description=node_text(snippet) if snippet is not None else "",
    )
    set_price(car, node_value(cell.css_first(".b-items__cell-info-price")))
    add_facts(car, facts, None)
    image = cell.css_first(".b-items__cars-one-img img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [urljoin(BASE, src)]
    return Lead(m[1], url, url, car)


def add_facts(car: ParsedCar, facts: dict[str, str], make: str | None) -> None:
    """The form's values; what the seller left out is read from the title and text."""
    title, text = car.title, car.description
    car.make = make or make_code(title)
    car.model = facts.get("Model") if facts.get("Model") not in (None, "Ostatní") else None
    car.year = year(facts.get("Rok výroby")) or cars.year_in_text(title, text, car.make)
    car.mileage_km = mileage(facts.get("Najeto")) or cars.km_in_text(title, text)
    car.fuel = fuel(facts.get("Palivo")) or cars.fuel_in_text(title, text)
    car.gearbox = gearbox(facts.get("Převodovka")) or cars.gearbox_in_text(title, text)
    car.body = body(facts.get("Karoserie")) or cars.body_in_text(title, text)
    car.power_kw = cars.power_in_text(facts.get("Výkon"), title, text)
    if reason := cars.not_offer_reason(title):
        car.extra["not_offer"] = reason


class AutoInzerce(CrawlSource):
    id = SOURCE_ID
    label = "Auto-inzerce.cz"
    kind: SourceKind = "classifieds"
    nationwide = True
    max_pages = 50  # the whole listing, ~7 pages
    refresh_after_days = None  # private ads rarely change

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"&page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for cell in tree.css(".b-items__cell") if (lead := parse_card(cell))]
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def gone(self, html: str) -> bool:
        headings = LexborHTMLParser(html).css("h1")
        return any(fold(node_value(h) or "") == "inzerat neexistuje" for h in headings)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first(".b-detail__head-title h1")))
        if not title or self.gone(html):
            return None
        facts = {}
        for row in tree.css(".b-detail__main-aside-desc .row"):
            label = node_value(row.css_first(".b-detail__main-aside-desc-title"))
            if label:
                facts[label] = node_value(row.css_first(".b-detail__main-aside-desc-value")) or ""
        text = "\n\n".join(node_text(p) for p in tree.css("#info1 > p"))
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=text,
            engine_cc=_number(facts.get("Obsah motoru")),
            drive=DRIVES.get(fold(facts.get("Pohon kol", ""))),
            condition=CONDITIONS.get(fold(facts.get("Stav", ""))),
            doors=_number(facts.get("Počet dveří")),
            seats=_number(facts.get("Počet míst")),
        )
        set_price(car, node_value(tree.css_first(".b-detail__head-price-num")))
        brand = facts.get("Značka")
        add_facts(car, facts, make_code(brand) if brand and brand != "Ostatní" else None)
        if car.condition == "damaged":
            car.crashed = True
        category = fold(facts.get("Kategorie", ""))
        if category and category not in CAR_CATEGORIES and "not_offer" not in car.extra:
            car.extra["not_offer"] = "not_car"
        vin = (facts.get("VIN") or "").upper()
        car.vin = vin if VIN.match(vin) else None
        region = next(
            (
                t
                for h in tree.css(".b-detail__main-aside-about h2.s-titleDet")
                if (t := node_value(h)) and fold(t).endswith(("kraj", "praha", "vysocina"))
            ),
            None,
        )
        car.place_text = region
        car.location = places.resolve(region) if region else None
        photos = (a.attributes.get("href") or "" for a in tree.css('a[data-fancybox="gallery"]'))
        car.images = list(dict.fromkeys(urljoin(BASE, p) for p in photos if p))
        return car


SOURCE = AutoInzerce()
