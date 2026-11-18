"""proverenevozy.toyota.cz: Toyota Prověřené vozy (Toyota Approved Used), the used cars of
Toyota's Czech dealers (~3 700 cars, mostly Toyota and Lexus, also trade-ins of other makes).

Server-rendered HTML; the site has no robots.txt (404), and its search has no JSON API. One
nationwide listing, newest first by default (`/nabidky?strona=<n>`, 20 cars per page, a few
promoted cars on top of the first); the walk stops at the first page with nothing new, so
only the first run reads the whole stock. A car unseen for a while is checked on its own
page: a sold car's page answers 410 Gone.

A card gives the make and model, the version (often with the power), the dealer's name,
year, mileage, fuel, engine size, price and whether the VAT is deductible ("DPH"). The
detail adds the dealer's address, body, gearbox, power, colour, VIN, text, equipment and
photos.
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "toyota"
BASE = "https://proverenevozy.toyota.cz"
LISTING = BASE + "/nabidky"
AD_PATH = re.compile(r"^/nabidka/[^/]+/(\d+)$")
PSC_TOWN = re.compile(r"^(\d{3}\s?\d{2})\s+(.+)$")
HP_TO_KW = 0.7355


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def model_name(name: str, make: str | None) -> str | None:
    """ "Toyota RAV4" -> "RAV4", "Toyota PROACE City Verso" -> "PROACE City Verso"."""
    label = MAKES[make]["cs"] if make in MAKES else None
    if label and fold(name).startswith(fold(label) + " "):
        return name[len(label) :].strip()
    first, _, rest = name.partition(" ")
    return rest if rest and make_code(first) else name or None


def fuel(stated: str | None, version: str) -> str | None:
    """The fuel field names only the petrol engine of many hybrids ("Benzín" for a
    "2.5Hybrid" RAV4), and "Hybrid" for a plug-in: the version tells them apart."""
    named = cars.fuel_in_text(version)
    if named in ("hybrid", "plugin_hybrid") and stated in (None, "petrol", "hybrid"):
        return named
    return stated or named


def gearbox(text: str | None) -> str | None:
    """ "Automatická", "Manuální"."""
    name = fold(text or "")
    if name.startswith("autom"):
        return "automatic"
    return "manual" if name.startswith("manu") else None


def _facts(car: ParsedCar, name: str, version: str) -> None:
    car.make = make_code(name)
    car.model = model_name(name, car.make)
    car.power_kw = cars.power_in_text(version)


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    link = box.css_first(".o-bx__title a[href]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    url = urljoin(BASE, m[0])
    name = node_value(link.css_first("strong")) or node_value(link) or ""
    version = node_value(box.css_first(".o-bx__title > span")) or ""
    car = ParsedCar(
        external_id=m[1],
        title=clean_spaces(f"{name} {version}") or "Auto",
        url=url,
        seller_kind="dealer",
        seller_name=node_value(box.css_first(".o-bx__info b")),
    )
    _facts(car, name, version)
    for item in box.css(".o-bx__info li"):
        text = node_value(item) or ""
        if item.css_first("strong") is not None:
            car.fuel = fuel(cars.fuel_in_text(text), version)
        elif text.endswith("km"):
            car.mileage_km = _number(text)
        elif "cm" in text:
            car.engine_cc = _number(text)
        elif re.fullmatch(r"(19|20)\d\d", text):
            car.year = int(text)
    car.price = _number(node_value(box.css_first(".o-bx__price .retail-price strong")))
    tags = {fold(node_value(t) or "") for t in box.css(".o-bx__tags .tag")}
    car.vat_deductible = True if "dph" in tags else None
    image = box.css_first(".o-bx__gallery img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [src]
    return Lead(m[1], url, url, car)


class Toyota(CrawlSource):
    id = SOURCE_ID
    label = "Toyota Prověřené vozy"
    newest_first = True
    nationwide = True
    max_pages = 200  # the first run reads the whole stock (~190 pages)
    recheck_after_days = 7

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"?strona={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [
            lead for box in tree.css("div.o-bx[offer-id]") if (lead := parse_card(box, places))
        ]
        next_href = f"/nabidky?strona={page + 1}"
        has_next = any(a.attributes.get("href") == next_href for a in tree.css("a[href]"))
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        name = node_value(tree.css_first(".vdp-header__title h1"))
        if not name:
            return None
        version = node_value(tree.css_first(".vdp-header__title > strong")) or ""
        tech = {
            prop: node_value(node)
            for node in tree.css(".vdp-tech [itemprop]")
            if (prop := node.attributes.get("itemprop"))
        }
        text = tree.css_first(".vdp-description__text")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {version}"),
            url=lead.url,
            description=node_text(text) if text is not None else "",
            year=_number(tech.get("productionDate")),
            mileage_km=_number(tech.get("mileageFromOdometer")),
            body=cars.body_in_text(tech.get("bodyType"))
            or ("other" if tech.get("bodyType") else None),
            gearbox=gearbox(tech.get("vehicleTransmission")),
            engine_cc=_number(tech.get("engineDisplacement")),
            color=tech.get("color") if tech.get("color") not in (None, "-") else None,
            doors=_number(tech.get("numberOfDoors")),
            seats=_number(tech.get("numberOfSeats")),
            vin=(tech.get("vehicleIdentificationNumber") or "").strip("-").upper() or None,
            seller_kind="dealer",
            equipment=[t for li in tree.css(".vdp-eq li") if (t := node_value(li))],
        )
        _facts(car, name, version)
        if car.power_kw is None and (hp := _number(tech.get("horsepower"))):
            car.power_kw = round(hp * HP_TO_KW)
        # The card says "Hybrid" for some cars whose detail names only the petrol engine.
        card_fuel = lead.card.fuel if lead.card else None
        car.fuel = fuel(card_fuel or cars.fuel_in_text(tech.get("fuelType")), version)
        price = tree.css_first(".vdp-aside__price .retail-price")
        if price is not None:
            car.price = _number(node_value(price.css_first("strong")))
            car.vat_deductible = True if "dph" in fold(price.text()) else None
        photos = (img.attributes.get("data-img-src") for img in tree.css(".vdp-thumbs img"))
        car.images = list(dict.fromkeys(p for p in photos if p))
        dealer = tree.css_first(".vdp-dealer address")
        if dealer is not None:
            car.seller_name = node_value(dealer.css_first("strong"))
            lines = [node_value(s) or "" for s in dealer.css("span")]
            town = PSC_TOWN.match(lines[-1]) if lines else None
            if town:
                car.place_text = town[2]
                street = lines[0] if len(lines) > 1 else None
                # "Praha 9 - Kyje": the resolver reads city parts written "Praha 9-Kyje".
                car.location = places.resolve(
                    re.sub(r"\s+-\s+", "-", town[2]), street=street, postal_code=town[1]
                )
        return car


SOURCE = Toyota()
