"""automodul.cz: a portal of dealers' stock (~32 000 passenger cars, a few private ads), run by
VLTAVA LABE MEDIA, HTML only.

robots.txt only disallows the page size on a car's own page (`*-c*?pocet_inz`). The listing
cannot be sorted newest first (its "stáří inzerátu" order is oldest first), so the one
nationwide listing is read whole on every run, oldest first (`/osobni/?order=dt_created__ASC
&pocet_inz=100&stranka=<n>`, 100 cars per page, ~325 pages). Cars of the same day change
places within a page between requests and sold ones leave during a long pass, so a car can
slip past a page edge unseen: cars expire after 3 days unseen, not after one pass. Its pages
are large (~1.7 MB, most of it the search form), so the source is off until the user
switches it on.

A card gives the title, price, mileage, year, fuel, body, engine size, power and the
district. The detail page adds schema.org Car data (make, model, gearbox, VIN), condition,
colour, equipment, the dealer's text, name and address, and the photos.
"""

import json
import re
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SellerKind, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, clean_title, fold, node_text
from adacar.normalize.vocab import make_code, make_end
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.dedup import SERIES
from adacar.scraper.sources.bazos import MAKE_WORDS, set_price

SOURCE_ID = "automodul"
PLACEHOLDER_KM = 999_999
BASE = "https://www.automodul.cz"
LISTING = BASE + "/osobni/?order=dt_created__ASC&pocet_inz=100"
AD_PATH = re.compile(r"^/[^/?]+-c(\d+)(?=\?|$)")
FOUND = re.compile(r"Nalezeno\s+(\d+)")
PAGE_SIZE = 100
PSC_TOWN = re.compile(r"^(\d{3}\s?\d{2})\s+(.+)$")
VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
CONDITIONS = (("ojet", "used"), ("predvad", "demo"), ("nov", "new"), ("havar", "damaged"))
SELLER_KINDS: dict[str, SellerKind] = {"Organization": "dealer", "Person": "private"}


def mileage(text: str | None) -> int | None:
    km = _number(text)
    return None if km == PLACEHOLDER_KM else km


def _number(text: str | None) -> int | None:
    """ "74 000 Km" -> 74000; the site writes 0 for a value not given."""
    digits = re.sub(r"\D", "", text or "")
    return int(digits) or None if digits else None


def body(text: str | None) -> str | None:
    """ "kombi", "limuzína", "terénní", "pick up", "ostatní"."""
    folded = fold(text or "")
    if not folded or folded == "neuvedeno":
        return None
    if folded.startswith("teren"):
        return "offroad"
    if folded.startswith("pick"):
        return "pickup"
    return cars.body_in_text(folded) or "other"


def gearbox(text: str | None) -> str | None:
    """ "automatická převodovka", "manuální převodovka"."""
    folded = fold(text or "")
    if "automat" in folded:
        return "automatic"
    return "manual" if "manual" in folded else None


def condition(text: str | None) -> str | None:
    folded = fold(text or "")
    return next((code for word, code in CONDITIONS if folded.startswith(word)), None)


def car_ld(tree: LexborHTMLParser) -> dict[str, Any]:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Car":
            return data
    return {}


def model_of(name: str, make: str | None) -> str | None:
    """ "BMW Řada 2" (the Car's name) -> "Řada 2"."""
    end = make_end(name)
    words = name[end:].strip(" -,").split() if end is not None else []
    while words and fold(words[0]) in MAKE_WORDS:
        words.pop(0)
    return " ".join(words) or None


def card_model(title: str, make: str | None) -> str | None:
    rest = model_of(title, make)
    words = (rest or "").split()
    if len(words) > 1 and fold(words[0]) in SERIES:
        return " ".join(words[:2])
    return words[0] if words else None


def card_facts(card: LexborNode) -> dict[str, str]:
    """The card's values by their icon: fa-clock (mileage), fa-pen (year), fa-gas-pump,
    fa-car (body), fa-cogs (engine), fa-map-marker (district)."""
    facts = {}
    for span in card.css(".f-smallest span"):
        icon = span.css_first("i.fa")
        classes = (icon.attributes.get("class") or "").split() if icon is not None else []
        kind = next((c for c in classes if c.startswith("fa-")), None)
        if kind:
            facts[kind[3:]] = clean_spaces(span.text(separator=" "))
    return facts


def parse_card(card: LexborNode, places: PlaceResolver) -> Lead | None:
    link = card.css_first("a[href]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if m is None:
        return None
    url = urljoin(BASE, m[0])
    title = clean_title(node_value(card.css_first(".inzerat_text .color-modraSvetla")))
    facts = card_facts(card)
    engine = facts.get("cogs", "")
    place = facts.get("map-marker") or None
    car = ParsedCar(
        external_id=m[1],
        title=title or "Auto",
        url=url,
        make=make_code(title),
        model=card_model(title, make_code(title)),
        mileage_km=mileage(facts.get("clock")),
        year=_number(facts.get("pen")),
        fuel=cars.fuel_in_text(facts.get("gas-pump")),
        body=body(facts.get("car")),
        engine_cc=_number(engine.partition(",")[0]),
        power_kw=cars.power_in_text(engine),
        place_text=place,
        location=places.resolve(place) if place else None,
    )
    set_price(car, node_value(card.css_first(".inzerat_text .color-bezovaTmava")))
    image = card.css_first("img[data-srcorig]")
    src = image.attributes.get("data-srcorig") if image is not None else None
    if src and "bez_fotky" not in src:
        car.images = [src]
    mark_no_car(car)
    return Lead(m[1], url, url, car)


def mark_no_car(car: ParsedCar) -> None:
    if reason := cars.not_offer_reason(car.title):
        car.extra["not_offer"] = reason
    elif car.make is None and car.year is None and car.mileage_km is None:
        car.extra["not_offer"] = "not_car"


class Automodul(CrawlSource):
    id = SOURCE_ID
    label = "Automodul.cz"
    kind: SourceKind = "portal"
    nationwide = True
    max_pages = 400  # the whole stock, ~325 pages
    default_enabled = False  # ~550 MB of listing pages on every run
    expire_after_days = 3

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"&stranka={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for card in tree.css("div.auto_bunka") if (lead := parse_card(card, places))]
        found = FOUND.search(html)
        # The "Další" link stays on the last page (greyed out): the ad count tells.
        has_next = page * PAGE_SIZE < int(found[1]) if found else bool(leads)
        return ListingPage(leads, has_next, none_listed=bool(found) and found[1] == "0")

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first("h1.nadpis_auta")))
        if not title:
            return None
        ld = car_ld(tree)
        facts = {}
        for row in tree.css(".d-block.w-100"):
            cells = [c for c in row.iter() if c.tag == "div"]
            if len(cells) >= 2 and (label := node_value(cells[0])):
                facts[label] = clean_spaces(cells[1].text(separator=" "))
        equipment = [t for div in tree.css(".maincontent .d-table-cell") if (t := node_value(div))]
        text = tree.css_first(".karta_popis")
        name = clean_spaces(ld.get("name") or "")
        make = make_code(ld.get("manufacturer") or "") or make_code(title)
        motor = facts.get("Motor", "")
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text) if text is not None else "",
            make=make,
            model=model_of(name, make) if name else None,
            year=_number(facts.get("Vyrobeno")),
            mileage_km=mileage(facts.get("Tachometr")),
            fuel=cars.fuel_in_text(facts.get("Palivo")),
            gearbox=gearbox(ld.get("vehicleTransmission")),
            body=body(facts.get("Tvar")),
            condition=condition(facts.get("Stav")),
            engine_cc=_number(motor.partition("(")[0]),
            power_kw=cars.power_in_text(motor),
            color=facts.get("Barva") or None,
            doors=_number(ld.get("numberOfDoors")),
            equipment=equipment,
        )
        vin = (facts.get("VIN") or "").upper()
        car.vin = vin if VIN.match(vin) else None
        if any(fold(e).replace(" ", "") == "pohon4x4" for e in equipment):
            car.drive = "4x4"
        set_price(car, node_value(tree.css_first("h1.nadpis_auta + div")))
        self.add_seller(car, tree, ld, places)
        photos = (img.attributes.get("src") or "" for img in tree.css("img.foto_auta"))
        car.images = list(
            dict.fromkeys(
                p.replace("-minigalerie2.", "-velky.") for p in photos if p.startswith("https://")
            )
        )
        mark_no_car(car)
        return car

    @staticmethod
    def add_seller(
        car: ParsedCar, tree: LexborHTMLParser, ld: dict[str, Any], places: PlaceResolver
    ) -> None:
        """ "CarTec Liberec, s.r.o.", "Obchodní 622", "46001 Liberec 11"."""
        offers = ld.get("offers") or {}
        seller_type = (offers.get("seller") or {}).get("@type")
        car.seller_kind = SELLER_KINDS.get(seller_type or "")
        heading = tree.css_first('a[href^="/prodejce/"] h3')
        car.seller_name = node_value(heading) or clean_spaces(offers.get("name") or "") or None
        box = heading.parent.parent if heading is not None and heading.parent else None
        address = box.css_first("div.f-medium") if box is not None else None
        text = address.text(separator="\n") if address is not None else ""
        lines = [x for p in text.split("\n") if (x := clean_spaces(p))]
        town = PSC_TOWN.match(lines[-1]) if lines else None
        if town is None:
            return
        # "19800 Praha, část obce Hloubětín": the town, then its part.
        name, _, part = town[2].partition(", část obce ")
        car.place_text = clean_spaces(name)
        street = lines[0] if len(lines) > 1 else None
        car.location = places.resolve(
            f"{name}-{part}" if part else name, street=street, postal_code=town[1]
        )


SOURCE = Automodul()
