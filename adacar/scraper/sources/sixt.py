"""sixt.cz: SIXT Autobazar, where Sixt (car rental and leasing) sells cars returned from its
leases and rentals, some on commission (~110 cars, mostly Škoda, Peugeot, Opel and VW vans,
2–5 years old). The cars stand at Sixt Autobazar, Černokostelecká 155/130, 100 00
Praha 10-Štěrboholy.

Server-rendered HTML. The whole offer is one page (`/nabidka-ojetych-vozidel-autobazar`),
read on every run: a car it no longer lists is gone. Each car has its own page at
`sixt.cz/<slug>`; the slug is the only id the site shows. robots.txt forbids only the admin
pages.

A card gives the name (make, model and engine), the version, colour, fuel, gearbox, the
month it went into service, mileage, price, whether the VAT is deductible, the equipment and
a photo. The detail adds body, doors, seats, power, engine size, VIN, first owner, service
book, the seller's note and all photos.
"""

import re
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.arval import fuel

SOURCE_ID = "sixt"
BASE = "https://www.sixt.cz"
LISTING = BASE + "/nabidka-ojetych-vozidel-autobazar"
SELLER = "SIXT Autobazar"
PLACE = ("Praha 10-Štěrboholy", "Černokostelecká 155/130", "10000")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", re.sub(r",\d+", "", text or ""))
    return int(digits) if digits else None


def model_name(name: str, make: str | None) -> str | None:
    """ "Land Rover Discovery Sport 2.0 d" -> "Discovery Sport": the name ends with the engine."""
    label = MAKES[make]["cs"] if make in MAKES else ""
    rest = name[len(label) :] if label and fold(name).startswith(fold(label)) else name
    return clean_spaces(re.split(r"\s\d[.,]\d", rest)[0]) or None


def gearbox(text: str | None) -> str | None:
    """ "aut. převodovka", "manuální převodovka"."""
    name = fold(text or "")
    if name.startswith("aut"):
        return "automatic"
    return "manual" if name.startswith("manu") else None


def _yes(text: str | None) -> bool | None:
    return {"ano": True, "ne": False}.get(fold(text or ""))


def _name(car: ParsedCar, name: str, version: str) -> None:
    car.make = make_code(name)
    car.model = model_name(name, car.make)
    car.power_kw = cars.power_in_text(version)


def parse_card(box: LexborNode) -> Lead | None:
    href = box.attributes.get("href") or ""
    slug = urlsplit(href).path.strip("/")
    if not href.startswith(BASE) or not slug or "/" in slug:
        return None
    name = node_value(box.css_first("h3")) or ""
    version = node_value(box.css_first(".vehicle-item--headline strong")) or ""
    car = ParsedCar(
        external_id=slug,
        title=clean_spaces(f"{name} {version}") or "Auto",
        url=href,
        price=_number(node_value(box.css_first(".price-actual"))),
        vat_deductible=True
        if "odpocet dph" in fold(node_value(box.css_first(".vat-info")) or "")
        else None,
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    _name(car, name, version)
    parts = (node_value(box.css_first(".vehicle-item--annotation")) or "").split(", ")
    for i, part in enumerate(parts):
        if part.endswith("km"):
            car.mileage_km = _number(part)
        elif m := re.fullmatch(r"\d\d/((19|20)\d\d)", part):
            car.year = int(m[1])
        elif "převodovka" in part:
            car.gearbox = gearbox(part)
        elif i == 0:
            car.color = part
        elif i == 1:
            car.fuel = fuel(part, version)
    description = node_value(box.css_first(".vehicle-item--description"))
    car.equipment = [e for e in (description or "").split(", ") if e]
    image = box.css_first(".vehicle-item--photo img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [src]
    return Lead(slug, href, href, car)


class Sixt(CrawlSource):
    id = SOURCE_ID
    label = "SIXT Autobazar"
    kind: SourceKind = "leasing"
    nationwide = True
    max_pages = 1

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, lambda page: LISTING)]

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("a.vehicle-item") if (lead := parse_card(box))]
        return ListingPage(leads, has_next=False)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        name = node_value(tree.css_first(".vehicle-detail--title h1"))
        if not name:
            return None
        version = node_value(tree.css_first(".vehicle-detail--subtitle")) or ""
        info = {
            label: value
            for item in tree.css(".vehicle-detail--basic-parameter-list .parameter-item")
            if (label := node_value(item.css_first(".parameter-item--name")))
            and (value := node_value(item.css_first(".parameter-item--value")))
        }
        since = re.search(r"(19|20)\d\d", info.get("V provozu od") or "")
        body = info.get("Karoserie")
        note = tree.css_first(".vehicle-detail--note .section-content")
        photos = [a.attributes.get("href") for a in tree.css(".vehicle-detail--main-photo a")]
        photos += [li.attributes.get("data-src") for li in tree.css(".vehicle-detail--gallery li")]
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {version}"),
            url=lead.url,
            description=clean_spaces(note.text()) if note is not None else "",
            year=int(since[0]) if since else None,
            mileage_km=_number(info.get("Stav tachometru")),
            price=_number(node_value(tree.css_first(".vehicle-detail--price-box .price-actual"))),
            fuel=fuel(info.get("Palivo"), version),
            gearbox=gearbox(info.get("Převodovka")),
            body=cars.body_in_text(body) or ("other" if body else None),
            condition="used",
            engine_cc=_number(info.get("Objem")),
            color=info.get("Barva"),
            doors=_number(info.get("Počet dveří")),
            seats=_number(info.get("Počet míst")),
            first_owner=_yes(info.get("1. majitel")),
            service_book=_yes(info.get("Servisní knížka")),
            vin=(info.get("VIN") or "").upper() or None,
            seller_kind="dealer",
            seller_name=SELLER,
            place_text=PLACE[0],
            location=places.resolve(PLACE[0], street=PLACE[1], postal_code=PLACE[2]),
            equipment=[t for n in tree.css(".information-box--info-item") if (t := node_value(n))],
            images=list(dict.fromkeys(p for p in photos if p)),
        )
        _name(car, name, version)
        car.power_kw = cars.power_in_text(info.get("Výkon")) or car.power_kw
        return car


SOURCE = Sixt()
