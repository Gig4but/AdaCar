"""machmotors.cz: MACH MOTORS, a BMW and Volvo dealer with prestige cars of other makes (~180
used and new cars in stock at Praha 5 and České Budějovice), server-rendered HTML.

robots.txt disallows only /includes. The whole stock is one page (`/bmw/nabidka_vozu`, every
car, no paging), read on every run; cars it no longer lists expire then. The Volvo Selekt
used cars are on Volvo's own site, not here.

Prices are written without VAT ("1 280 165 Kč + DPH"): the price kept is with VAT (×1.21, to
10 Kč: mostly the round price the dealer asks of a buyer), marked as VAT-deductible.

A card gives the title (make, model, version), first registration ("08/2020", "Nové auto"),
power, gearbox, fuel, mileage, price and a photo. The detail adds engine size, body, colour,
the branch, equipment, the dealer's notes and all photos; it has no VIN.
"""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "machmotors"
BASE = "https://www.machmotors.cz"
LISTING = BASE + "/bmw/nabidka_vozu"
AD_PATH = re.compile(r"^(?:https://www\.machmotors\.cz)?(/bmw/detail-vozu/(v?\d+-[^/?#]*))$")
REGISTERED = re.compile(r"^(?:\d{1,2}/)?((?:19|20)\d{2})$")
SELLER = "MACH MOTORS"
VAT = 1.21
# Branch name on a detail page -> (town, street, postal code), from the contact page.
BRANCHES = {
    "Praha": ("Praha 5", "Za opravnou 186/1", "15000"),
    "České Budějovice": ("České Budějovice", "Na Dlouhé louce 1730", "37011"),
}
# Range Rover models are written without "Land Rover".
RANGE_ROVERS = ("Sport", "Velar", "Evoque")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def model_name(title: str, make: str | None) -> str | None:
    """The first word after the make: "Volvo XC60 B5 AWD" -> "XC60", "Mercedes-Benz E 450d"
    -> "E", "Range Rover Velar D300" -> "Range Rover Velar"."""
    words = title.split()
    if fold(" ".join(words[:2])) == "range rover":
        extra = words[2:3] if words[2:3] and words[2] in RANGE_ROVERS else []
        return " ".join(words[:2] + extra)
    name = MAKES[make]["cs"] if make in MAKES else ""
    if name and fold(title).startswith(fold(name) + " "):
        words = title[len(name) :].split()
    else:
        words = words[1:]
    return words[0] if words else None


def set_price(car: ParsedCar, text: str | None) -> None:
    """ "Naše Cena: 1 280 165 Kč + DPH" (without VAT) -> 1 549 000, VAT-deductible."""
    price = cars.parse_price(text)
    if not price or price[1] != "CZK":
        return
    if "+ dph" in fold(text or ""):
        # To 10 Kč: the net price was rounded to whole crowns ("1 098 999" -> "1 099 000").
        car.price, car.vat_deductible = int(round(price[0] * VAT, -1)), True
    else:
        car.price = price[0]


def set_registered(car: ParsedCar, text: str | None) -> None:
    """ "08/2020", "2021" or "Nové auto"."""
    if m := REGISTERED.match(text or ""):
        car.year = int(m[1])
    elif fold(text or "").startswith("nove"):
        car.condition = "new"


def body(text: str | None) -> str | None:
    """ "kombi", "SUV", "offroad", "limuzína/ sedan", "coupé"."""
    if not text:
        return None
    if fold(text) == "offroad":
        return "offroad"
    return cars.body_in_text(text) or "other"


def parse_card(box: LexborNode) -> Lead | None:
    m = AD_PATH.match(box.attributes.get("href") or "")
    title = node_value(box.css_first(".title"))
    if m is None or not title:
        return None
    url = BASE + m[1]
    make = make_code(title)
    car = ParsedCar(
        external_id=m[2],
        title=title,
        url=url,
        make=make,
        model=model_name(title, make),
        condition="used",
        power_kw=cars.power_in_text(node_value(box.css_first(".vykon"))),
        gearbox=cars.gearbox_in_text(node_value(box.css_first(".prevodovka"))),
        fuel=cars.fuel_in_text(node_value(box.css_first(".palivo"))),
        mileage_km=_number(node_value(box.css_first(".najeto"))),
        seller_kind="dealer",
        seller_name=SELLER,
    )
    set_registered(car, node_value(box.css_first(".registrace")))
    set_price(car, clean_spaces((box.css_first(".cena") or box).text()))
    image = box.css_first(".image img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [BASE + src if src.startswith("/") else src]
    return Lead(m[2], url, url, car)


def facts(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Výkon" -> "441 kW/600 PS", "Pobočka" -> "Praha", ..."""
    found: dict[str, str] = {}
    for block in tree.css(".auta_bloky .auta_blok"):
        label = clean_spaces(block.text(deep=False))
        value = clean_spaces((block.css_first("b") or block).text())
        if label and value and block.css_first("b") is not None:
            found.setdefault(label, value)
    return found


class MachMotors(CrawlSource):
    id = SOURCE_ID
    label = "MACH MOTORS"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 1  # the whole stock is one page

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("a.carBlock") if (lead := parse_card(box))]
        return ListingPage(leads, has_next=False)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        top = tree.css_first(".auta_detail .top_line")
        title = node_value(top.css_first("h1")) if top is not None else None
        if top is None or not title:
            return None
        info = facts(tree)
        make = make_code(title)
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            make=make,
            model=model_name(title, make),
            condition="new" if fold(info.get("Stav vozu") or "") == "nove" else "used",
            power_kw=cars.power_in_text(info.get("Výkon")),
            mileage_km=_number(info.get("Stav tachometru")),
            gearbox=cars.gearbox_in_text(info.get("Převodovka")),
            fuel=cars.fuel_in_text(info.get("Palivo")),
            engine_cc=_number((info.get("Obsah") or "").partition("cm")[0]),
            body=body(info.get("Karoserie")),
            color=info.get("Barva") or None,
            seller_kind="dealer",
            seller_name=SELLER,
        )
        set_registered(car, info.get("Rok 1. registrace"))
        set_price(car, clean_spaces((top.css_first(".cena") or top).text()))
        notes = [node_value(top.css_first(".left p"))]
        notes += [node_text(n) for n in tree.css(".vybavy .info .hidden")]
        car.description = "\n\n".join(n for n in notes if n)
        packets = (node_value(p) or "" for p in tree.css(".balicky .packet"))
        features = (
            node_value(n) for n in tree.css(".vybavy .nazev") if n.css_first(".info") is None
        )
        car.equipment = list(
            dict.fromkeys(t for t in [*(p.lstrip("- ") for p in packets), *features] if t)
        )
        photos = (a.attributes.get("href") or "" for a in tree.css(".car_gallery a[href]"))
        car.images = list(dict.fromkeys(BASE + p for p in photos if p.startswith("/")))
        if branch := info.get("Pobočka"):
            car.place_text = branch
            town, street, psc = BRANCHES.get(branch, (branch, None, None))
            car.location = places.resolve(town, street=street, postal_code=psc)
        return car


SOURCE = MachMotors()
