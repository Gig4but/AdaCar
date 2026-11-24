"""hyundai-promise.cz: Hyundai Promise, the used cars of Hyundai's Czech dealers (~1 350 cars of
every make, mostly trade-ins).

Server-rendered HTML; robots.txt allows the listing and the car pages. One nationwide listing
(`/ojete-vozy?strana=<n>`, 9 cars per page; a bigger page size is kept in a cookie, so not
used) whose order is not by date, so the whole of it is read on every run (~150 pages); cars
it no longer lists expire then, and a sold car's page answers 404. The dealers' demonstration
cars (/predvadeci-vozy) link to each dealer's own site and are not read.

A card gives the make and model, the version (often with the power), year, mileage, fuel,
gearbox, the region the car stands in, the price and whether a net price is shown (VAT the
buyer can deduct). The detail adds the dealer with its address, body, drive, engine size,
power, colour, seats, doors, VIN, country of origin, service book, the dealer's note,
equipment and photos.
"""

import re
from urllib.parse import quote, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.toyota import fuel, gearbox, model_name

SOURCE_ID = "hyundai"
BASE = "https://www.hyundai-promise.cz"
LISTING = BASE + "/ojete-vozy"
AD_PATH = re.compile(r"^/([^/]+)/([^/]+)/([A-Za-z0-9]+)$")
PSC_TOWN = re.compile(r"^(\d{3}\s?\d{2}),?\s+(.+)$")
DRIVES = {"4x4": "4x4", "predni": "front", "zadni": "rear"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _year(text: str | None) -> int | None:
    m = re.search(r"(?:19|20)\d\d", text or "")
    return int(m[0]) if m else None


def _ad(href: str) -> tuple[str, str] | None:
    """(external id, page URL); car pages are /<make>/<model>/<VIN or stock number>, the model
    sometimes with a space ("/jeep/grand cherokee/...")."""
    parts = urlsplit(href)
    if parts.netloc and parts.netloc != urlsplit(BASE).netloc:
        return None
    m = AD_PATH.match(parts.path)
    return (m[3].upper(), urljoin(BASE, quote(m[0]))) if m else None


def _name(heading: LexborNode | None) -> tuple[str, str]:
    """ "<strong>Dodge Durango</strong> 3,6 Pentastar 216kW GT Plus" -> (name, version)."""
    if heading is None:
        return "", ""
    name = node_value(heading.css_first("strong")) or ""
    whole = clean_spaces(heading.text())
    return name, clean_spaces(whole[len(name) :] if whole.startswith(name) else whole)


def _facts(car: ParsedCar, name: str, version: str) -> None:
    car.make = make_code(name)
    car.model = model_name(name, car.make)
    car.power_kw = cars.power_in_text(version)


def _labelled(nodes: list[LexborNode]) -> dict[str, str]:
    """ "Najeto: <strong>112 569 km</strong>" -> {"Najeto": "112 569 km"}."""
    found: dict[str, str] = {}
    for node in nodes:
        label = clean_spaces(node.text(deep=False)).rstrip(":")
        value = node_value(node.css_first("strong"))
        if label and value:
            found.setdefault(label, value)
    return found


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    link = box.css_first("h3 a[href]")
    ad = _ad(link.attributes.get("href") or "") if link is not None else None
    if ad is None:
        return None
    external_id, url = ad
    name, version = _name(link)
    info = _labelled(box.css(".CarList-spec div div"))
    region = node_value(box.css_first(".CarList-map"))
    where = "Praha" if fold(region or "").startswith("hl.m") else region  # "Hl.m. Praha"
    car = ParsedCar(
        external_id=external_id,
        title=clean_spaces(f"{name} {version}") or "Auto",
        url=url,
        year=_year(info.get("Do provozu")),
        mileage_km=_number(info.get("Najeto")),
        price=_number(node_value(box.css_first(".CarList-priceWithVat"))),
        vat_deductible=True if box.css_first(".CarList-priceWithoutVat") else None,
        gearbox=gearbox(info.get("Převodovka")),
        seller_kind="dealer",
        place_text=region,
        location=places.resolve(where) if where else None,
    )
    _facts(car, name, version)
    car.fuel = fuel(cars.fuel_in_text(info.get("Palivo")), version)
    image = box.css_first("img.CarList-img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [urljoin(BASE, src)]
    return Lead(external_id, url, url, car)


class Hyundai(CrawlSource):
    id = SOURCE_ID
    label = "Hyundai Promise"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 250

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"?strana={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [
            lead
            for box in tree.css(".CarList-item")
            if (lead := parse_card(box, places)) is not None
        ]
        # A page past the end shows no cars and a "next" link back to page 2.
        next_link = tree.css_first("a.Paginator-next[href]")
        has_next = next_link is not None and (next_link.attributes.get("href") or "").endswith(
            f"strana={page + 1}"
        )
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        name, version = _name(tree.css_first("#car-detail h1"))
        if not name:
            return None
        info = _labelled(tree.css(".CarDetail-spec > div"))
        engine, _, power = (info.get("Objem / Výkon") or "").partition("/")
        drive = fold(info.get("Pohon") or "")
        note = tree.css_first("#poznamka")
        origin = info.get("Země původu")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {version}"),
            url=lead.url,
            description=node_text(note) if note is not None else "",
            year=_year(info.get("Rok výroby")) or _year(info.get("Do provozu")),
            mileage_km=_number(info.get("Najeto")),
            price=_number(node_value(tree.css_first(".CarDetail-priceWithVat"))),
            vat_deductible=True if fold(info.get("Odpočet DPH") or "") == "ano" else None,
            gearbox=gearbox(info.get("Převodovka")),
            body=cars.body_in_text(info.get("Karoserie"))
            or ("other" if info.get("Karoserie") else None),
            drive=next((code for word, code in DRIVES.items() if word in drive), None),
            condition="used",
            engine_cc=_number(engine) if "ccm" in engine else None,
            color=info.get("Barva"),
            doors=_number(info.get("Počet dveří")),
            seats=_number(info.get("Počet míst")),
            service_book=True if fold(info.get("Servisní knížka") or "") == "ano" else None,
            origin_country=origin,
            vin=(info.get("VIN") or "").upper() or None,
            seller_kind="dealer",
            equipment=[t for li in tree.css(".CarDetail-specVybava li") if (t := node_value(li))],
        )
        _facts(car, name, version)
        car.power_kw = cars.power_in_text(power) or car.power_kw
        car.fuel = fuel(cars.fuel_in_text(info.get("Palivo")), version)
        photos = (img.attributes.get("src") for img in tree.css("img.ImageGallery-mainImage"))
        car.images = list(dict.fromkeys(urljoin(BASE, p) for p in photos if p))
        dealer = tree.css_first(".CarDetail-dealerAdress")
        if dealer is not None:
            car.seller_name = node_value(dealer.css_first("h4"))
            address = dealer.css_first("p")
            lines = [clean_spaces(x) for x in (address.text() if address else "").split("\n")]
            lines = [x for x in lines if x]
            town = PSC_TOWN.match(lines[-1]) if lines else None
            if town:
                street = lines[0] if len(lines) > 1 else None
                # "Praha 14 - Hloubětín": the resolver reads "Praha 14-Hloubětín".
                car.place_text = info.get("K vidění") or town[2]
                car.location = places.resolve(
                    re.sub(r"\s+-\s+", "-", town[2]), street=street, postal_code=town[1]
                )
        return car


SOURCE = Hyundai()
