"""emilfreyselect.cz: Emil Frey Select, the used cars of Emil Frey's two Prague branches,
Černý Most (Praha 14) and Stodůlky (Praha 5) (~220 cars of ~25 makes, mostly Toyota, Subaru,
and Peugeot, Citroën and Opel, which Emil Frey imports).

emilfrey.cz lists the same branches' used cars (~190) on another system; this listing has them
all plus the young demonstrators of the "Roční vozy" programme, so only this one is read.
Server-rendered HTML; robots.txt allows everything. One listing (`/ojete-vozy?page=<n>`, 9 cars
per page, "recommended" order), the whole of it on every run (~25 pages); cars it no longer
lists expire then.

A card gives make, model, version, the branch's town, fuel, mileage, year, gearbox, price and
a photo. The detail adds body, power, engine size, doors, seats, colour, VIN, equipment, the
car's history (service book, bought in Czechia), the seller's note, whether VAT is deductible,
the branch's address and all photos.
"""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.toyota import fuel, model_name

SOURCE_ID = "emilfrey"
BASE = "https://www.emilfreyselect.cz"
LISTING = BASE + "/ojete-vozy"
# .../detail-vozu/<make>/<model>/<body>/<version>/id<dealer>/<id>
AD_URL = re.compile(
    r"^https://www\.emilfreyselect\.cz/[a-z-]+/detail-vozu/[^/]+/[^/]+/(?P<body>[^/]+)/[^?#]+/(?P<id>\d+)$"
)
TOWN_PSC = re.compile(r"^(?P<town>.+?),\s*(?P<psc>\d{3}\s?\d{2})\b")
SELLER = "Emil Frey"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _year(text: str | None) -> int | None:
    """ "2026", "03/2026"."""
    m = re.search(r"(?:19|20)\d\d$", (text or "").strip())
    return int(m[0]) if m else None


def _name(node: LexborNode | None) -> str:
    """ "Citroën\\n SpaceTourer" -> "Citroën SpaceTourer"."""
    return clean_spaces(node.text()) if node is not None else ""


def _price(node: LexborNode | None) -> int | None:
    price = cars.parse_price(node_value(node))
    return price[0] if price and price[1] == "CZK" else None


def _specs(car: ParsedCar, box: LexborNode, version: str) -> None:
    """A card's icon row: fuel, mileage, year, gearbox."""
    for icon in box.css("div.fuel, div.km, div.year, div.trans"):
        span = icon.next
        while span is not None and span.tag != "span":
            span = span.next
        text = node_value(span) or ""
        kind = icon.attributes.get("class")
        if kind == "fuel":
            car.fuel = fuel(cars.fuel_in_text(text), version)
        elif kind == "km":
            car.mileage_km = _number(text)
        elif kind == "year":
            car.year = _year(text)
        else:
            car.gearbox = cars.gearbox_in_text(f"{text} převodovka")


def _facts(car: ParsedCar, name: str) -> None:
    car.make = make_code(name)
    car.model = model_name(name, car.make)


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    link = box.css_first("a[href]")
    m = AD_URL.match(link.attributes.get("href") or "") if link is not None else None
    if m is None:
        return None
    name = _name(box.css_first("h5"))
    # Long versions end in "(...)" on cards; the detail has them whole.
    version = clean_spaces(
        (node_value(box.css_first(".card-car__subtitle")) or "").removesuffix("(...)")
    )
    car = ParsedCar(
        external_id=m["id"],
        title=clean_spaces(f"{name} {version}") or "Auto",
        url=m[0],
        body=cars.body_in_text(m["body"]),
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
        price=_price(box.css_first("a.btn strong")),
    )
    _facts(car, name)
    car.power_kw = cars.power_in_text(version)
    _specs(car, box, version)
    car.place_text = node_value(box.css_first(".card-car__distance .text-left"))
    car.location = places.resolve(car.place_text) if car.place_text else None
    image = box.css_first(".card-car__thumbnail-img img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [src]
    return Lead(m["id"], m[0], m[0], car)


def params(tree: LexborHTMLParser) -> dict[str, str]:
    """The detail's table: "Typ" -> "MPV", "Výkon" -> "132 kW", "VIN" -> "VF7…"."""
    found: dict[str, str] = {}
    for row in tree.css("#collapseOne .row"):
        cells = list(row.iter())
        for label, value in zip(cells, cells[1:], strict=False):
            if "bold" in (label.attributes.get("class") or "").split():
                key = clean_spaces(label.text()).rstrip(":").strip()
                if key and (text := clean_spaces(value.text())):
                    found.setdefault(key, text)
    return found


class EmilFrey(CrawlSource):
    id = SOURCE_ID
    label = "Emil Frey Select"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 60

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("div[role=article]") if (lead := parse_card(box, places))]
        next_url = f"{LISTING}?page={page + 1}"
        has_next = any(a.attributes.get("href") == next_url for a in tree.css("a[href]"))
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        top = tree.css_first(".car__info__top")
        name = _name(top.css_first("h1") if top is not None else None)
        if top is None or not name:
            return None
        version = node_value(top.css_first("h5")) or ""
        info = params(tree)
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {version}"),
            url=lead.url,
            condition="used",
            seller_kind="dealer",
            seller_name=SELLER,
            mileage_km=_number(info.get("Najeto")),
            year=_year(info.get("Rok uvedení do provozu")),
            power_kw=cars.power_in_text(info.get("Výkon")),
            engine_cc=_number((info.get("Objem") or "").removesuffix("cm3")),
            gearbox=cars.gearbox_in_text(info.get("Převodovka")),
            doors=_number(info.get("Počet dveří")),
            seats=_number(info.get("Počet míst k sezení")),
            color=info.get("Barva"),
            vin=(info.get("VIN") or "").upper() or None,
        )
        _facts(car, name)
        car.fuel = fuel(cars.fuel_in_text(info.get("Palivo")), version)
        car.body = cars.body_in_text(info.get("Typ")) or ("other" if info.get("Typ") else None)
        price = top.css_first(".car__info__top__price")
        car.price = _price(price.css_first("h2 strong") if price is not None else None)
        car.vat_deductible = (
            True if price is not None and "lze odecist dph" in fold(price.text()) else None
        )
        car.equipment = list(
            dict.fromkeys(t for li in tree.css("#collapseOne .col-6 li") if (t := node_value(li)))
        )
        set_history(car, tree)
        set_dealer(car, tree, places)
        photos = (a.attributes.get("href") or "" for a in tree.css("a[href]"))
        own = f"/files/gallery/cars/{lead.external_id}/1680_1024/"
        car.images = list(dict.fromkeys(p for p in photos if own in p))
        return car


def set_history(car: ParsedCar, tree: LexborHTMLParser) -> None:
    """The lists after "Historie vozu" and the text after "Poznámka prodejce"."""
    for heading in tree.css("#collapseOne h3"):
        title = fold(node_value(heading) or "")
        after = heading.next
        while after is not None and after.tag in ("-text", "-comment"):
            after = after.next
        if after is None:
            continue
        if title.startswith("historie"):
            history = {fold(t) for li in after.css("li") if (t := node_value(li))}
            car.service_book = True if "servisni kniha" in history else None
            car.origin_country = "Česká republika" if "koupeno v cz" in history else None
        elif title.startswith("poznamka"):
            car.description = node_text(after)


def set_dealer(car: ParsedCar, tree: LexborHTMLParser, places: PlaceResolver) -> None:
    """ "Emil Frey, s.r.o. - Černý Most", "Skorkovská 1759", "Praha 14, 198 00, Česká republika"."""
    block = tree.css_first(".dealer p.bold")
    address = block.next if block is not None else None
    while address is not None and address.tag != "p":
        address = address.next
    if block is None or address is None:
        return
    car.seller_name = node_value(block) or SELLER
    lines = [t for line in address.text(separator="\n").split("\n") if (t := clean_spaces(line))]
    m = next((m for line in lines if (m := TOWN_PSC.match(line))), None)
    if m:
        car.place_text = m["town"]
        street = lines[0] if lines and not TOWN_PSC.match(lines[0]) else None
        car.location = places.resolve(m["town"], street=street, postal_code=m["psc"])


SOURCE = EmilFrey()
