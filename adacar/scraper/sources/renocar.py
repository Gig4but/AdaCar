"""renocar.cz: Renocar, a BMW and MINI dealer group (~530 cars in stock in Praha, Brno and
Zlín: used cars of the BMW Premium Selection and MINI NEXT programmes, and new stock), HTML.

The site's stock list is loaded by a POST request, so the cars are read from its sitemap
(`/Sitemap.xml`, every car's page `/bmw/vuz/<slug>-<id>` and `/mini/vuz/...`; motorcycles
left out), which robots.txt allows; the whole of it on every run, cars no longer in it
expire then. A car is known only from its own page, so a first run reads ~300 of them and
the next runs the rest.

A car's page gives the title, the used-car programme (none for a new car), model, mileage,
first registration, price (also without VAT), colour, engine size, power, doors, seats,
equipment and photos. It names neither the fuel (read from the engine code: "20d", "M340i",
"30e") nor the branch the car stands at, nor the full VIN.
"""

import re

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "renocar"
BASE = "https://www.renocar.cz"
SITEMAP = BASE + "/Sitemap.xml"
AD_URL = re.compile(
    r"<loc>\s*(https://www\.renocar\.cz/(?:bmw|mini)/vuz/[a-z0-9-]+-(\d+))\s*</loc>"
)
REGISTERED = re.compile(r"(?:\d{1,2}\s*/\s*)?((?:19|20)\d{2})")
SELLER = "Renocar"
# BMW engine codes: "520d", "xDrive40d", "M340i", "330e"; "i4", "iX3", "eDrive40" are electric.
ENGINE = re.compile(r"(?<![a-z])(?:[a-z]*drive)?m?\d{2,3}([die])(?![a-z])")
ELECTRIC = re.compile(r"(?<![a-z])(?:i[x\d][\d]?|edrive\d*|cooper s?e)(?![a-z])")
ENGINE_FUELS = {"d": "diesel", "i": "petrol", "e": "plugin_hybrid"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def fuel(*texts: str | None) -> str | None:
    """Named in the title ("hybrid"), else from the engine code."""
    if named := cars.fuel_in_text(*texts):
        return named
    folded = " ".join(fold(t or "") for t in texts)
    if ELECTRIC.search(folded):
        return "electric"
    m = ENGINE.search(folded)
    return ENGINE_FUELS[m[1]] if m else None


def gearbox(equipment: list[str]) -> str | None:
    """From the gearbox's own equipment line ("Sportovní aut. převodovka"); other lines name
    "Manuál a servisní knížka"."""
    for item in equipment:
        folded = fold(item)
        if "prevodovk" not in folded:
            continue
        if re.search(r"(?<![a-z])aut|steptronic|dvouspojk", folded):
            return "automatic"
        if "manual" in folded:
            return "manual"
    return None


def model_name(text: str | None, make: str | None) -> str | None:
    """ "BMW X1" -> "X1", "BMW řady 5 Touring" -> "Řada 5 Touring", "MINI Countryman" ->
    "Countryman"."""
    name = clean_spaces(text or "")
    label = MAKES[make]["cs"] if make in MAKES else ""
    if label and fold(name).startswith(fold(label) + " "):
        name = name[len(label) :].strip()
    return re.sub(r"^řady\b", "Řada", name) or None


def labelled(tree: LexborHTMLParser, item: str, label: str, value: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.css(item):
        key, text = node_value(node.css_first(label)), node_value(node.css_first(value))
        if key and text:
            found.setdefault(key, text)
    return found


class Renocar(CrawlSource):
    id = SOURCE_ID
    label = "Renocar"
    kind: SourceKind = "dealer"
    default_enabled = False  # its cars have no place, so distance and region filters miss them
    nationwide = True
    max_pages = 1  # the sitemap lists every car

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return SITEMAP

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        leads = {m[2]: Lead(m[2], m[1], m[1]) for m in AD_URL.finditer(html)}
        return ListingPage(list(leads.values()), has_next=False)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = node_value(tree.css_first("h1"))
        if not title or tree.css_first(".r_stock-main-details") is None:
            return None
        main = labelled(tree, ".r_stock-main-details--item", "span.label", "span.value")
        spec = labelled(tree, ".r_stock-spec--item", ".r_stock-spec--label", ".r_stock-spec--value")
        make = make_code(title)
        programme = node_value(tree.css_first(".r_premium-badge span"))
        used = bool(programme or main.get("Stav tachometru") or main.get("První registrace"))
        equipment = [t for li in tree.css(".stock-car__attrs--lists li") if (t := node_value(li))]
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            make=make,
            model=model_name(main.get("Model vozu"), make),
            mileage_km=_number(main.get("Stav tachometru")),
            fuel=fuel(title, main.get("Model vozu")),
            gearbox=gearbox(equipment),
            body=cars.body_in_text(title, main.get("Model vozu")),
            condition="used" if used else "new",
            power_kw=cars.power_in_text(spec.get("Výkon")),
            engine_cc=_number(spec.get("Objem motoru")),
            color=spec.get("Základní barva (kód)"),
            doors=_number(spec.get("Počet dveří")),
            seats=_number(spec.get("Počet osob")),
            seller_kind="dealer",
            seller_name=SELLER,
            equipment=list(dict.fromkeys(equipment)),
        )
        if m := REGISTERED.search(main.get("První registrace") or ""):
            car.year = int(m[1])
        if programme:
            about = node_value(tree.css_first(".stock-cars__premium-selection--inner p"))
            car.description = f"{programme}: {about}" if about else programme
        price = tree.css_first(".r_stock-price--value")
        found = cars.parse_price(node_value(price))
        if found and found[1] == "CZK":
            car.price = found[0]
        no_vat = node_value(tree.css_first(".r_stock-price--no-vat"))
        car.vat_deductible = True if car.price and no_vat else None
        photos = (
            node.attributes.get("data-src") or ""
            for node in tree.css(
                ".stock-car__preview--gallery-for .stock-car__preview--gallery-item"
            )
        )
        car.images = list(dict.fromkeys(p for p in photos if p.startswith("https://")))
        return car


SOURCE = Renocar()
