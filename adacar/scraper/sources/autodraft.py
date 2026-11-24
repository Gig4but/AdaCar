"""autodraft.cz: Autodraft, a Prague dealer importing 3-4 year old Volkswagen-group cars from
Germany (~370 cars), HTML only.

robots.txt allows everything. The one listing (`/auta.html?gpage=<n>`, 24 cars per page,
~16 pages, newest first) is small, so it is read whole on every run and a complete pass
tells which cars are sold.

A card gives the title (with power and gearbox), the main features (fuel for electric cars
and hybrids, gearbox, body, power, 4x4), exact mileage, first registration and price. The
detail page adds fuel, gearbox, power, country of origin, VIN, first owner, colour, the
mechanic's report, equipment and photos (its mileage is rounded to thousands).
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import (
    CrawlSource,
    Lead,
    Listing,
    ListingPage,
    has_page_link,
    node_value,
)
from adacar.scraper.sources.bazar_cz import fuel as form_fuel
from adacar.scraper.sources.bazos import set_price

SOURCE_ID = "autodraft"
BASE = "https://www.autodraft.cz"
LISTING = BASE + "/auta.html"
AD_URL = re.compile(r"^https://www\.autodraft\.cz/detail/(\d+)-[^/?#]+\.html$")
SELLER = "Autodraft"
TOWN = "Praha"  # the showroom; pages give no address
VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
REGISTERED = re.compile(r"(?:^|\D)((?:19|20)\d\d)$")
PHOTO = re.compile(r"/images/details/1920_")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def model_name(title: str, make: str | None) -> str | None:
    """The word after the make: "Škoda Octavia 4 Combi ..." -> "Octavia"."""
    label = MAKES[make]["cs"] if make in MAKES else None
    if not label or not fold(title).startswith(fold(label) + " "):
        return None
    words = title[len(label) :].split()
    return words[0] if words else None


def gearbox(text: str | None) -> str | None:
    folded = fold(text or "")
    if folded.startswith("autom"):
        return "automatic"
    return "manual" if folded.startswith("manu") else None


def card_features(features: list[str], car: ParsedCar) -> None:
    """ "Elektro", "Hybrid", "4x4", "automatická", "kombi", "147kw": the main features a card
    shows; fuel, gearbox and body are named only for some cars, the title tells the rest."""
    folded = [fold(f) for f in features]
    named = cars.fuel_in_text(car.title)
    if "elektro" in folded:
        car.fuel = "electric"
    elif "hybrid" in folded:  # also for plug-in hybrids
        car.fuel = "plugin_hybrid" if named == "plugin_hybrid" else "hybrid"
    else:
        car.fuel = named
    car.gearbox = "automatic" if "automaticka" in folded else cars.gearbox_in_text(car.title)
    car.body = cars.body_in_text(*folded) or cars.body_in_text(car.title)
    car.drive = "4x4" if "4x4" in folded else None
    car.power_kw = cars.power_in_text(*features) or cars.power_in_text(car.title)


def parse_card(card: LexborNode) -> Lead | None:
    m = AD_URL.match(card.attributes.get("href") or "")
    if m is None:
        return None
    heading = card.css_first(".car-listing-title")
    title = clean_spaces(heading.text(deep=False)) if heading is not None else ""
    make = make_code(title)
    registered = REGISTERED.search(node_value(card.css_first(".car-listing-spec-date")) or "")
    car = ParsedCar(
        external_id=m[1],
        title=title or "Auto",
        url=m[0],
        make=make,
        model=model_name(title, make),
        mileage_km=_number(node_value(card.css_first(".car-listing-spec-km"))),
        year=int(registered[1]) if registered else None,
        seller_kind="dealer",
        seller_name=SELLER,
        place_text=TOWN,
    )
    features = [t for x in card.css(".equipment-list-item") if (t := node_value(x))]
    card_features(features, car)
    set_price(car, node_value(card.css_first(".car-listing-spec-price")))
    image = card.css_first("img.card-img-top[data-src]")
    if image is not None and (src := image.attributes.get("data-src")):
        car.images = [src]
    return Lead(m[1], m[0], m[0], car)


class Autodraft(CrawlSource):
    id = SOURCE_ID
    label = "Autodraft"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 40  # the whole stock, ~16 pages

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"?gpage={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("a.car-listing-card"):
            if lead := parse_card(card):
                if lead.card is not None:
                    lead.card.location = places.resolve(TOWN)
                leads.append(lead)
        return ListingPage(leads, has_page_link(tree, "gpage", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_spaces(node_value(tree.css_first("h1.car-v2-title")) or "")
        if not title:
            return None
        facts = {
            fold(label): node_value(row.css_first(".car-v2-value")) or ""
            for row in tree.css(".car-v2-params .car-v2-row")
            if (label := node_value(row.css_first(".car-v2-label")))
        }
        chips = [t for x in tree.css(".car-v2-chip") if (t := node_value(x))]
        report = tree.css_first(".car-v2-report-paper-text")
        make = make_code(title)
        card = lead.card
        registered = REGISTERED.search(facts.get("registrace", ""))
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(report) if report is not None else "",
            make=make,
            model=model_name(title, make),
            year=int(registered[1]) if registered else None,
            # "113 tis. km": the card has the exact number.
            mileage_km=card.mileage_km if card else None,
            fuel=form_fuel(facts.get("palivo"))
            or ("electric" if "baterie" in facts else None)
            or (card.fuel if card else None),
            gearbox=gearbox(facts.get("prevodovka")) or (card.gearbox if card else None),
            power_kw=cars.power_in_text(facts.get("vykon")),
            origin_country=facts.get("puvod") or None,
            first_owner=True if "1. majitel" in chips else None,
            color=next((c for c in chips if c != "1. majitel" and c != "Elektroauto"), None),
            seller_kind="dealer",
            seller_name=SELLER,
            place_text=TOWN,
            location=places.resolve(TOWN),
            equipment=[t for li in tree.css(".car-v2-eq-col li") if (t := node_value(li))],
        )
        vin = facts.get("vin", "").upper()
        car.vin = vin if VIN.match(vin) else None
        price = tree.css_first(".car-v2-price-block")
        if price is not None:
            set_price(car, node_value(price.css_first(".car-v2-price")))
            car.vat_deductible = True if "odectete" in fold(price.text()) else None
        photos = (img.attributes.get("data-src") or "" for img in tree.css("img[data-src]"))
        car.images = list(dict.fromkeys(urljoin(BASE, p) for p in photos if PHOTO.search(p)))
        return car


SOURCE = Autodraft()
