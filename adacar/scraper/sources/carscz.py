"""cars.cz: a car portal (~33 000 passenger cars, mostly dealers'), HTML only.

The listing's parameters travel zlib-compressed and hex-encoded in one `?p=` parameter
("A_N=N&ctype=O&page=2&max=50&o=price"). robots.txt allows the listing and the ad pages
(`/inzerce/...html`), not the search form or the print pages.

The whole country is read as one listing sorted by price, 50 cars per page (~670 pages):
the default order puts the ads a dealer's software re-sent last first, which is half of
them every day, and the region filter returns cars from other regions too. A listing read
to its end expires the cars it no longer lists.

A card gives title, price, year, mileage, fuel, body, engine and power; the detail page adds
VIN, colour, text, equipment and the seller's address. The portal belongs to the company
behind tipcars.com, so many dealers' cars are listed on both.
"""

import re
import zlib
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "carscz"
BASE = "https://www.cars.cz"
LISTING = BASE + "/inzerce/osobni-auto-O/"
PAGE_SIZE = 50
# /inzerce/osobni-auto-O/<make>/<model>/<title>-<id>.html; the model may be missing.
AD_PATH = re.compile(r"^/inzerce/osobni-auto-O/([^/]+)/(?:([^/]+)/)?[^/]*?-(\d{8,})\.html")
PAGE_OF = re.compile(r"(?:^|&)page=(\d+)")
VIN = re.compile(r"(?=.*\d)[A-Z0-9]{11,17}")
PSC = re.compile(r"^\d{3}\s?\d{2}$")


def listing_query(page: int) -> str:
    return f"A_N=N&ctype=O&page={page}&max={PAGE_SIZE}&o=price"


def encode(query: str) -> str:
    return zlib.compress(query.encode()).hex()


def decode(value: str) -> str:
    try:
        return zlib.decompress(bytes.fromhex(value)).decode("ascii", "replace")
    except (ValueError, zlib.error):
        return ""


def _int(text: str | None) -> int | None:
    m = re.search(r"\d[\d\s]*", clean_spaces(text or ""))
    return int(re.sub(r"\D", "", m[0])) if m else None


def body(text: str | None) -> str | None:
    """ "Kombi", "Sedan/Limuzína", "Sportovní/Kupé", "Terénní", "Kabriolet/Roadster"."""
    if not text:
        return None
    if fold(text).startswith("teren"):
        return "offroad"
    return cars.body_in_text(text) or "other"


def price(text: str | None) -> int | None:
    found = cars.parse_price(text)
    return found[0] if found and found[1] == "CZK" else None


def make_and_model(make: str, model: str | None) -> tuple[str | None, str | None]:
    """From the ad's URL: ("Mercedes-Benz", "GLE") -> ("mercedes-benz", "GLE")."""
    name = make.replace("-", " ")
    return make_code(name) or fold(make) or None, model.replace("-", " ") if model else None


class CarsCz(CrawlSource):
    id = SOURCE_ID
    label = "Cars.cz"
    nationwide = True
    max_pages = 800  # ~670 pages hold every car

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_of)]

    @staticmethod
    def listing_url_of(page: int) -> str:
        return f"{LISTING}?p={encode(listing_query(page))}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for item in tree.css("p.carItem") if (lead := card(item))]
        pages = (
            PAGE_OF.search(decode((a.attributes.get("href") or "")[3:]))
            for a in tree.css('.ftr a[href^="?p="]')
        )
        has_next = any(m is not None and int(m[1]) == page + 1 for m in pages)
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = node_value(tree.css_first("h1"))
        facts = tree.css_first(".mainDesc")
        if not title or facts is None:
            return None
        info: dict[str, LexborNode] = {}
        for label in facts.css("span.ttl"):
            value = label.next
            while value is not None and value.tag != "span":
                value = value.next
            if value is not None:
                info.setdefault(clean_spaces(label.text()).rstrip(":"), value)
        m = AD_PATH.match((lead.url or "").removeprefix(BASE))
        make, model = make_and_model(m[1], m[2]) if m else (make_code(title), None)
        state = fold(node_value(_after(tree, "Stav vozidla")) or "")
        equipment = [
            t for li in tree.css("#accByCat li:not(.capt)") if (t := clean_spaces(li.text()))
        ]
        listed = _after(tree, "Výbava vozidla")
        if listed is not None:
            equipment += [p for p in (x.strip() for x in listed.text().split(",")) if p]
        desc = tree.css_first('p[itemprop="description"]')
        doors, _, seats = (node_value(info.get("Dveří / sedadel")) or "").partition("/")
        vin = (_meta(tree, "vehicleIdentificationNumber") or "").upper()
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(desc) if desc is not None else "",
            make=make,
            model=model,
            year=_year(node_value(info.get("V provozu od"))),
            mileage_km=_int(_meta(tree, "value")) or _int(node_value(info.get("Tachometr"))),
            price=_price(tree),
            vat_deductible=True if "odpočtu DPH" in _text(tree.css_first(".priceInfo")) else None,
            fuel=cars.fuel_in_text(node_value(info.get("Palivo")), title),
            gearbox=cars.gearbox_in_text(title, " ".join(equipment)),
            body=body(node_value(info.get("Karoserie"))),
            power_kw=_int(node_value(info.get("Výkon"))) or None,
            engine_cc=_int(node_value(info.get("Objem"))) or None,
            color=node_value(info.get("Barva")),
            doors=_int(doors) or None,
            seats=_int(seats) or None,
            first_owner=True if "prvni majitel" in state else None,
            crashed=False if "nehavarovan" in state else None,
            vin=vin if VIN.fullmatch(vin) else None,
            equipment=list(dict.fromkeys(equipment)),
        )
        photo = tree.css_first("#middlePhoto")
        if photo is not None and (src := photo.attributes.get("src")):
            car.images = [src]
        seller(car, tree.css_first(".firmInfo"), places)
        return car


def card(item: LexborNode) -> Lead | None:
    link = item.css_first("a.carLink")
    href = (link.attributes.get("href") or "") if link is not None else ""
    m = AD_PATH.match(href)
    if m is None:
        return None
    url = urljoin(BASE, href)
    title = node_value(item.css_first(".txt strong")) or ""
    make, model = make_and_model(m[1], m[2])
    # "V prov. od 2020, 56 950 km" / "Diesel, kombi" / "1 969 ccm, 140 kW (190 PS)"
    lines = [*_lines(item.css_first(".desc")), "", "", ""]
    first, kinds, engine = lines[:3]
    fuel, _, kind = kinds.partition(",")
    year = re.search(r"od (\d{4})", first)
    km = re.search(r"([\d ]+) km", first)
    car = ParsedCar(
        external_id=m[3],
        title=title,
        url=url,
        make=make,
        model=model,
        year=int(year[1]) if year else None,
        mileage_km=_int(km[1]) if km else None,
        price=price(node_value(item.css_first("strong.price"))),
        vat_deductible=True if item.css_first('img[title="Možnost odpočtu DPH"]') else None,
        fuel=cars.fuel_in_text(fuel, title),
        gearbox=cars.gearbox_in_text(title),
        body=body(kind.strip()),
        power_kw=cars.power_in_text(engine),
        engine_cc=_int(m_cc[1]) if (m_cc := re.search(r"([\d ]+) ccm", engine)) else None,
    )
    image = item.css_first(".img_wrap img")
    src = image.attributes.get("src") if image is not None else None
    if src and src.startswith("https://img.cars.cz/"):  # not a make's logo
        car.images = [src.split("?")[0]]
    return Lead(m[3], url, url, car)


def _lines(node: LexborNode | None) -> list[str]:
    if node is None:
        return []
    return [clean_spaces(p) for p in node.text(separator="\n").split("\n")]


def _text(node: LexborNode | None) -> str:
    return clean_spaces(node.text()) if node is not None else ""


def _meta(tree: LexborHTMLParser, prop: str) -> str | None:
    node = tree.css_first(f'meta[itemprop="{prop}"]')
    return node.attributes.get("content") if node is not None else None


def _after(tree: LexborHTMLParser, heading: str) -> LexborNode | None:
    """The paragraph after a section heading ("Stav vozidla", "Další informace")."""
    for h in tree.css("h2.sectTtl"):
        if clean_spaces(h.text()) == heading:
            node = h.next
            while node is not None and node.tag != "p":
                if node.tag == "h2":
                    return None
                node = node.next
            return node
    return None


def _year(text: str | None) -> int | None:
    """ "06.2020", "2020"."""
    m = re.search(r"(\d{4})", text or "")
    return int(m[1]) if m else None


def _price(tree: LexborHTMLParser) -> int | None:
    """The offer's price (0.0 for "Dohodou")."""
    try:
        value = int(float(_meta(tree, "price") or 0))
    except ValueError:
        value = 0
    return value if value >= cars.MIN_PRICE else None


def seller(car: ParsedCar, firm: LexborNode | None, places: PlaceResolver) -> None:
    """ "Maříkova 341/21 u Globusu, 62100, Brno - Ivanovice, Česká republika": a dealer's
    name links to its page on the portal."""
    if firm is None:
        return
    name = firm.css_first("h3 a")
    car.seller_kind = "dealer" if name is not None else "private"
    car.seller_name = node_value(name) if name is not None else None
    parts = [p.strip() for p in (node_value(firm.css_first(".addr")) or "").split(",")]
    parts = [p for p in parts if p and fold(p) != "ceska republika"]
    psc = next((p for p in parts if PSC.match(p)), None)
    words = [p for p in parts if p != psc]
    town = words[-1] if words else None
    street = words[0] if len(words) > 1 else None
    car.place_text = town
    car.location = places.resolve(town, street=street, postal_code=psc)


SOURCE = CarsCz()
