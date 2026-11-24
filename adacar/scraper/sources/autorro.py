"""autorro.cz: Autorro, a Slovak car dealer and broker that also sells in Czechia (~2 100
cars on the Czech site, most of them standing in Slovakia; a few hundred in Czech regions,
mostly sold on behalf of their owners).

Server-rendered HTML (WordPress); robots.txt disallows only the admin. The listing is read
per region (`/nabidka-vozidel/?locality=CZ-B&pagenum=<n>`, 15 cars per page, newest first),
so the Slovak cars are never read; a region's listing is short and read whole on every run,
and cars it no longer lists expire then.

A card gives the title, mileage, year, drive, fuel, gearbox, power, body, the region the car
stands in, the price (also without VAT for some cars) and a photo. The detail page
(`/auto/<slug>-<VIN or UUID>/`) adds the VIN, engine size, colour, doors, equipment, the
broker's text and all photos; it names only the region too.
"""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ParsedLocation, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, ListingPage, node_value

SOURCE_ID = "autorro"
BASE = "https://www.autorro.cz"
LISTING = BASE + "/nabidka-vozidel/"
AD_URL = re.compile(r"^https://www\.autorro\.cz/auto/([a-z0-9-]+)/$")
# The slug ends with the car's VIN or a UUID.
AD_ID = re.compile(r"-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[a-z0-9]{17})$")
SELLER = "Autorro"
REGION_CODES = {
    "Hlavní město Praha": "CZ-A",
    "Středočeský kraj": "CZ-S",
    "Jihočeský kraj": "CZ-C",
    "Plzeňský kraj": "CZ-P",
    "Karlovarský kraj": "CZ-K",
    "Ústecký kraj": "CZ-U",
    "Liberecký kraj": "CZ-L",
    "Královéhradecký kraj": "CZ-H",
    "Pardubický kraj": "CZ-E",
    "Kraj Vysočina": "CZ-J",
    "Jihomoravský kraj": "CZ-B",
    "Olomoucký kraj": "CZ-M",
    "Moravskoslezský kraj": "CZ-T",
    "Zlínský kraj": "CZ-Z",
}
DRIVES = {"4x4": "4x4", "predni": "front", "zadni": "rear"}
# Card values, told apart by their icons (".../ponuka/km.svg").
CARD_ICONS = re.compile(r"/ponuka/([a-z]+)\.svg$")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def body(text: str | None) -> str | None:
    """ "SUV / Off-road", "Dodávka", "Skříň" (a box van: other)."""
    return cars.body_in_text(text) or ("other" if text else None)


def resolve(place: str | None, places: PlaceResolver) -> ParsedLocation | None:
    """A region ("Jihomoravský kraj"); Prague's is the city itself."""
    if not place:
        return None
    return places.resolve("Praha" if place == "Hlavní město Praha" else place)


def set_price(car: ParsedCar, node: LexborNode | None) -> None:
    """ "120 000 Kč" or "120 000 Kč 99 174 Kč bez DPH" (the VAT is deductible)."""
    price = cars.parse_price(node_value(node.css_first("p, h4"))) if node is not None else None
    if price and price[1] == "CZK":
        car.price = price[0]
    if node is not None and "bez dph" in fold(node.text()):
        car.vat_deductible = True


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    link = box.css_first("a[href]")
    m = AD_URL.match(link.attributes.get("href") or "") if link is not None else None
    if m is None:
        return None
    url = m[0]
    external_id = (AD_ID.search(m[1]) or m)[1]
    title = clean_spaces(node_value(box.css_first(".headline-car")) or "")
    values: dict[str, str] = {}
    for item in box.css(".d-flex.my-1"):
        icon = item.css_first("img")
        icon_name = CARD_ICONS.search((icon.attributes.get("src") or "") if icon else "")
        if icon_name and (text := node_value(item.css_first("p"))):
            values[icon_name[1]] = text
    make = make_code(title)
    car = ParsedCar(
        external_id=external_id,
        title=title or "Auto",
        url=url,
        make=make,
        mileage_km=_number(values.get("km")),
        year=_number(values.get("year")),
        drive=DRIVES.get(fold(values.get("pohon") or "")),
        fuel=cars.fuel_in_text(values.get("palivo")),
        gearbox=cars.gearbox_in_text(values.get("prevod")),
        power_kw=cars.power_in_text(values.get("vykon")),
        body=body(values.get("prevedenie")),
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
        place_text=values.get("lokacia"),
    )
    car.location = resolve(car.place_text, places)
    set_price(car, box.css_first(".price-display-car--cz"))
    image = box.css_first("img.img-fluid")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [src]
    return Lead(external_id, url, url, car)


def tech_table(tree: LexborHTMLParser) -> dict[str, str]:
    """ "VIN" -> "WAUZZZ8KXDA013732", "Typ karoserie" -> "Kombi", ..."""
    found: dict[str, str] = {}
    for row in tree.css(".pink_background .border-bottom-grey"):
        label = node_value(row.css_first("p.font-weight-semi-bold"))
        values = row.css("p.small")
        if label and len(values) > 1 and (value := node_value(values[-1])):
            found.setdefault(clean_spaces(label), value)
    return found


class Autorro(CrawlSource):
    id = SOURCE_ID
    label = "Autorro"
    kind: SourceKind = "dealer"
    region_slugs = REGION_CODES
    max_pages = 50

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{LISTING}?locality={region_slug}" + (f"&pagenum={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("div.efect") if (lead := parse_card(box, places))]
        has_next = any(
            f"pagenum={page + 1}" in (a.attributes.get("href") or "").split("&")
            for a in tree.css("a[href]")
        )
        grid = tree.css_first("#grid")
        none_listed = (
            page == 1
            and not leads
            and grid is not None
            and any(
                "row" in (row.attributes.get("class") or "").split() and not list(row.iter())
                for row in grid.iter()
            )
        )
        return ListingPage(leads, has_next, none_listed)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        head = tree.css_first(".d-flex.d-lg-none.pt-6")
        title = tree.css_first('meta[property="og:title"]')
        if head is None or title is None:
            return None
        info = tech_table(tree)
        make = make_code(node_value(head.css_first("p.large")))
        tech = node_value(tree.css_first("#toggle-container2 p:not(.grey)")) or ""
        seats = re.search(r"\((\d+)-míst", tech)
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(title.attributes.get("content") or ""),
            url=lead.url,
            make=make,
            model=node_value(head.css_first("h3")),
            year=_number(info.get("Rok výroby")),
            mileage_km=_number(info.get("Kilometry")),
            fuel=cars.fuel_in_text(info.get("Palivo")),
            gearbox=cars.gearbox_in_text(info.get("Převodovka")),
            body=body(info.get("Typ karoserie")),
            drive=DRIVES.get(fold(info.get("Náhon") or "").replace("×", "x")),
            condition="used",
            power_kw=cars.power_in_text(info.get("Výkon v kW")),
            engine_cc=_number(re.sub(r"cm3$", "", info.get("Objem motoru") or "")),
            color=info.get("Farba"),
            doors=_number(info.get("Dveře")),
            seats=int(seats[1]) if seats else None,
            vin=(info.get("VIN") or "").upper() or None,
            seller_kind="dealer",
            seller_name=SELLER,
        )
        set_price(car, head.css_first(".d-flex.flex-column"))
        for part in ("4", "5", "6"):  # safety, comfort, other
            text = node_value(tree.css_first(f"#toggle-container{part} p.grey")) or ""
            items = re.split(r",(?![^()]*\))", text)
            car.equipment += [e for item in items if (e := item.strip())]
        about = tree.css_first("#toggle-container8 p.grey")
        car.description = node_text(about) if about is not None else ""
        where = tree.css_first("a.btn-3 > p.text-white")
        car.place_text = node_value(where)
        car.location = resolve(car.place_text, places)
        photos = (a.attributes.get("href") for a in tree.css("a.carousel-item[href]"))
        car.images = list(dict.fromkeys(p for p in photos if p))
        return car if car.title else None


SOURCE = Autorro()
