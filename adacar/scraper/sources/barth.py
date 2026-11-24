"""autocentrum.cz: Autocentrum BARTH, a dealer group in Pardubice and Hradec Králové (~160
used cars, mostly Škoda and Volkswagen; also campers and a few vans).

The listing page (`/nabidka-vozu/ojete/?page=<n>`, 12 cars per page, newest first) carries its
cars as JSON in a script (`window._SSR['filter'] = {...}`, with `stats.lastPage`). The whole of
it is read on every run (~14 pages); cars it no longer lists expire then. robots.txt disallows
the search pages and filter parameters (price, colour, labels...), not the listing's `?page=`.

A card gives make, model, version, year of first registration, mileage, fuel, gearbox, power,
engine size, colour, body, price with VAT, whether VAT is deductible, the branch the car
stands at and a photo. The detail page adds whether the car was crashed, equipment, the
branch's address and all photos.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "barth"
BASE = "https://www.autocentrum.cz"
LISTING = BASE + "/nabidka-vozu/ojete/"
STATE = "window._SSR['filter']"
SELLER = "Autocentrum BARTH"
ADDRESS = re.compile(r"^(?P<street>.+?),\s*(?P<psc>\d{3}\s?\d{2})$")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _year(text: str | None) -> int | None:
    """ "říjen 2024"."""
    m = re.search(r"(?:19|20)\d\d", text or "")
    return int(m[0]) if m else None


def gearbox(text: str | None) -> str | None:
    """ "Manuální", "Automatická"."""
    return cars.gearbox_in_text(f"{text or ''} převodovka")


def town(branch: str | None) -> str | None:
    """ "Pardubice - Dubina", "Pardubice – Dubina" -> "Pardubice"."""
    name = re.split(r"\s+[-–]\s+", clean_spaces(branch or ""))[0]
    return name or None


def listing_state(html: str) -> dict | None:
    start = html.find(STATE)
    brace = html.find("{", start) if start >= 0 else -1
    if brace < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html, brace)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_item(item: dict, places: PlaceResolver) -> ParsedCar:
    url = f"{BASE}/nabidka-vozu/{item['seo_url']}/"
    price = item.get("price_customer") or {}
    branch = ((item.get("delivery_condition") or {}).get("stock") or {}).get("branch") or {}
    image = item.get("image") or {}
    car = ParsedCar(
        external_id=str(item["id"]),
        title=clean_spaces(item.get("full_name") or "") or "Auto",
        url=url,
        make=make_code(item.get("brand_slug")) or make_code(item.get("full_name")),
        model=clean_spaces(item.get("model") or "") or None,
        year=_year(item.get("to_traffic")),
        mileage_km=item.get("tachometer") or None,
        price=round(price["with_vat"]) if price.get("with_vat") else None,
        vat_deductible=True if item.get("vat_deduction") else None,
        fuel=cars.fuel_in_text(item.get("fuel")),
        gearbox=gearbox(item.get("transmission")),
        body=cars.body_in_text(item.get("body")) or ("other" if item.get("body") else None),
        condition="used",
        power_kw=item.get("power") or None,
        engine_cc=item.get("engine_capacity") or None,
        color=clean_spaces((item.get("helios_color") or {}).get("name") or "") or None,
        seller_kind="dealer",
        seller_name=SELLER,
        place_text=clean_spaces(branch.get("title") or "") or None,
    )
    if place := town(car.place_text):
        car.location = places.resolve(place)
    if isinstance(image, dict) and image.get("url"):
        car.images = [image["url"]]
    return car


def car_record(tree: LexborHTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text() or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Car":
            return data
    return None


def params(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Havarováno" -> "Ano", "Do provozu" -> "Říjen 2022", ..."""
    found: dict[str, str] = {}
    for label in tree.css("div"):
        if label.attributes.get("class") != "w-1/2":
            continue
        value = label.next
        while value is not None and value.tag != "div":
            value = value.next
        key, text = node_value(label), node_value(value)
        if key and text:
            found.setdefault(key, text)
    return found


def section_after(tree: LexborHTMLParser, heading: str) -> LexborNode | None:
    """The element that follows a section's heading (or the block holding the heading)."""
    for node in tree.css("h4, div.text-large"):
        if node_value(node) != heading:
            continue
        anchor = node.parent if node.tag == "h4" else node
        after = anchor.next if anchor is not None else None
        while after is not None and after.tag != "div":
            after = after.next
        return after
    return None


class Barth(CrawlSource):
    id = SOURCE_ID
    label = "Autocentrum BARTH"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 40

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        state = listing_state(html)
        if state is None:
            return ListingPage([], has_next=False)
        leads = []
        for item in state.get("results") or []:
            if not isinstance(item, dict) or not item.get("id") or not item.get("seo_url"):
                continue
            car = parse_item(item, places)
            leads.append(Lead(car.external_id, car.url or "", car.url, car))
        stats = state.get("stats") or {}
        return ListingPage(
            leads,
            has_next=(stats.get("page") or page) < (stats.get("lastPage") or 0),
            none_listed=stats.get("total") == 0,
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        record = car_record(tree)
        if record is None:
            return None
        info = params(tree)
        engine = record.get("vehicleEngine") or {}
        crashed = fold(info.get("Havarováno") or "")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(record.get("name") or ""),
            url=lead.url,
            make=make_code((record.get("brand") or {}).get("name")),
            model=clean_spaces(record.get("model") or "") or None,
            year=_year(info.get("Do provozu")),
            mileage_km=_number(str((record.get("mileageFromOdometer") or {}).get("value") or "")),
            fuel=cars.fuel_in_text(info.get("Pohon/Palivo")),
            gearbox=gearbox(info.get("Převodovka")),
            body=cars.body_in_text(record.get("bodyType"))
            or ("other" if record.get("bodyType") else None),
            condition="used",
            power_kw=cars.power_in_text(info.get("Výkon")),
            engine_cc=_number(str((engine.get("engineDisplacement") or {}).get("value") or "")),
            color=clean_spaces(record.get("color") or "") or None,
            crashed={"ano": True, "ne": False}.get(crashed),
            seller_kind="dealer",
            seller_name=SELLER,
            images=[i for i in record.get("image") or [] if isinstance(i, str)],
        )
        set_price(car, tree)
        equipment = section_after(tree, "Výbava vozu")
        if equipment is not None and (text := node_value(equipment)):
            car.equipment = list(dict.fromkeys(e for p in text.split(",") if (e := p.strip())))
        set_branch(car, tree, places)
        return car if car.title else None


def set_price(car: ParsedCar, tree: LexborHTMLParser) -> None:
    """ "Vaše cena s DPH" -> "499 900 Kč"; "Vaše cena bez DPH" is less when VAT is deductible."""
    for label in tree.css("div"):
        if node_value(label) != "Vaše cena s DPH":
            continue
        value = label.next
        while value is not None and value.tag != "div":
            value = value.next
        price = cars.parse_price(node_value(value))
        if price and price[1] == "CZK":
            car.price = price[0]
        return


def set_branch(car: ParsedCar, tree: LexborHTMLParser, places: PlaceResolver) -> None:
    """ "Pardubice – Dubina", "Hůrka 1798, 530 12", "Tel.: ...", "E-mail: ..."."""
    block = section_after(tree, "Vůz je k vidění na adrese")
    paragraph = block.css_first("p") if block is not None else None
    if paragraph is None:
        return
    lines = [t for line in paragraph.text(separator="\n").split("\n") if (t := clean_spaces(line))]
    if not lines:
        return
    car.place_text = lines[0]
    m = next((m for line in lines[1:] if (m := ADDRESS.match(line))), None)
    if place := town(lines[0]):
        car.location = places.resolve(
            place, street=m["street"] if m else None, postal_code=m["psc"] if m else None
        )


SOURCE = Barth()
