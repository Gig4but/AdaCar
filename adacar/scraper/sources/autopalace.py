"""autopalace.cz: Auto Palace (AutoBinck), a dealer group (~580 used cars at branches in Praha,
Tuchoměřice and Brno), HTML.

robots.txt disallows every URL with a query and filter paths with many underscores, so the
used-car listing is read by its plain paths (`/skladove-vozy/typy_ojete/<n>/`, 12 cars per
page, cheapest first; a page past the end redirects to the last one), the whole of it on every
run (~50 pages); cars it no longer lists expire then.

A card gives make, model, engine, mileage, first registration, power, fuel, gearbox, body,
the branch, price (with "s DPH" when the VAT is deductible) and a photo. The detail adds
engine size, colour, doors, service book, the seller's text (with the VIN), equipment, all
photos and the branch's address.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ParsedLocation, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "autopalace"
BASE = "https://www.autopalace.cz"
LISTING = BASE + "/skladove-vozy/typy_ojete/"
# The id after the last hyphen has its stock system's prefix: "czto988022", "skoo50956".
AD_PATH = re.compile(
    r"^(?:https://www\.autopalace\.cz)?(/skladove-vozy/[a-z0-9-]+-([a-z]{2,6}\d+)\.html)$"
)
REGISTERED = re.compile(r"^((?:19|20)\d{2})(?:/\d{1,2})?$")
PSC_TOWN = re.compile(r"^(\d{3}\s?\d{2}),?\s+(.+)$")
VIN = re.compile(r"VIN\s*:\s*([A-HJ-NPR-Z0-9]{17})", re.IGNORECASE)
PHOTOS = "/uploads/images/vehicles/stock/"
SELLER = "Auto Palace"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def set_year(car: ParsedCar, text: str | None) -> None:
    """ "2014/4" or "2014"."""
    if m := REGISTERED.match(clean_spaces(text or "")):
        car.year = int(m[1])


def gearbox(text: str | None) -> str | None:
    """ "manuál", "automat"."""
    return cars.gearbox_in_text(f"{text or ''} převodovka")


def body(text: str | None) -> str | None:
    """ "MPV", "SUV", "kombi", "kabriolet"."""
    return cars.body_in_text(text) or ("other" if text else None)


def set_price(car: ParsedCar, box: LexborNode | None) -> None:
    """A price box: "49 900 Kč Není možný odpočet DPH" or "1 949 900 Kč s DPH 1 611 488 Kč
    bez DPH"."""
    if box is None:
        return
    text = clean_spaces(box.text(deep=False)) or node_value(box)
    price = cars.parse_price(text)
    if price and price[1] == "CZK":
        car.price = price[0]
    car.vat_deductible = True if "bez dph" in fold(box.text()) else None


def branch_place(name: str | None, places: PlaceResolver) -> ParsedLocation | None:
    """ "Praha Spořilov", "Tuchoměřice", "Brno": the town, as the card names the branch."""
    if not name:
        return None
    return places.resolve(name) or places.resolve(name.split()[0])


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    link = box.css_first("h3.title a[href]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    url = BASE + m[1]
    make = link.attributes.get("data-vehicle-manufacturer") or node_value(link.css_first("span"))
    car = ParsedCar(
        external_id=m[2],
        title=clean_spaces(link.attributes.get("data-vehicle-title") or "") or "Auto",
        url=url,
        make=make_code(make),
        model=clean_spaces(link.text(deep=False)) or None,
        body=body(link.attributes.get("data-vehicle-bodywork")),
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    for item in box.css("ul.info li"):
        icon = item.css_first("i")
        kind = (icon.attributes.get("class") or "") if icon is not None else ""
        text = node_value(item)
        if "ssg-tachometer" in kind:
            car.mileage_km = _number(text)
        elif "ssg-calendar" in kind:
            set_year(car, text)
        elif "ssg-engine-flash" in kind:
            car.power_kw = cars.power_in_text(text)
        elif "ssg-drop-flash" in kind:
            car.fuel = cars.fuel_in_text(text)
        elif "ssg-transmission" in kind:
            car.gearbox = gearbox(text)
    set_price(car, box.css_first(".prices .price"))
    branch = box.css_first("a.branch")
    car.place_text = branch.attributes.get("title") if branch is not None else None
    car.location = branch_place(car.place_text, places)
    image = box.css_first(".imagesContainer img")
    src = (image.attributes.get("src") or "") if image is not None else ""
    if src.startswith(PHOTOS):
        car.images = [BASE + src]
    return Lead(m[2], url, url, car)


def product_record(tree: LexborHTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text() or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return None


def parameters(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Objem" -> "1 560 ccm", "Barva" -> "modrá", ..."""
    found: dict[str, str] = {}
    for item in tree.css(".vehicle-detail-parameters .col > div"):
        label = node_value(item.css_first("b"))
        value = clean_spaces(item.text(deep=False))
        if label and value:
            found.setdefault(label, value)
    return found


def set_branch(car: ParsedCar, card: LexborNode | None, places: PlaceResolver) -> None:
    """The branch's name and address ("Na Chodovci 2457/1", "141 00, Praha 4")."""
    address = card.css_first("address") if card is not None else None
    if card is None or address is None:
        return
    car.place_text = node_value(card.css_first(".title"))
    lines = [line for t in address.text().splitlines() if (line := clean_spaces(t))]
    if lines and (m := PSC_TOWN.match(lines[-1])):
        street = lines[0] if len(lines) > 1 else None
        car.location = places.resolve(m[2], street=street, postal_code=m[1])


class AutoPalace(CrawlSource):
    id = SOURCE_ID
    label = "Auto Palace"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 100

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"{page}/" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [
            lead
            for box in tree.css("article.vehicle-smallCard")
            if (lead := parse_card(box, places))
        ]
        next_path = f"/skladove-vozy/typy_ojete/{page + 1}/"
        has_next = any(
            (a.attributes.get("href") or "").removeprefix(BASE) == next_path
            for a in tree.css("a[href]")
        )
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        product = product_record(tree)
        heading = tree.css_first(".vehicle-detail h1")
        if product is None or heading is None:
            return None
        info = parameters(tree)
        text_node = tree.css_first(".vehicle-detail .col-md-6 h2 + div")
        description = node_text(text_node) if text_node is not None else ""
        vin = VIN.search(description)
        offer = product.get("offers") or {}
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(product.get("name") or ""),
            url=lead.url,
            description=description,
            make=make_code((product.get("brand") or {}).get("name")),
            model=node_value(tree.css_first(".vehicle-detail h1 > span:not(.d-inline)")),
            mileage_km=_number(info.get("Stav tachometru")),
            fuel=cars.fuel_in_text(info.get("Palivo")),
            gearbox=gearbox(info.get("Převodovka")),
            body=body(info.get("Karosérie")),
            condition="used",
            power_kw=cars.power_in_text(info.get("Motor"), product.get("name")),
            engine_cc=_number(info.get("Objem")),
            color=info.get("Barva"),
            doors=_number(info.get("Počet dvěří") or info.get("Počet dveří")),
            service_book={"ano": True, "ne": False}.get(fold(info.get("Servisní knížka") or "")),
            vin=vin[1].upper() if vin else None,
            seller_kind="dealer",
            seller_name=SELLER,
        )
        if "UsedCondition" not in str(offer.get("itemCondition") or "UsedCondition"):
            car.condition = None
        set_year(car, info.get("Uvedení do provozu"))
        set_price(car, tree.css_first(".price-container .price"))
        if car.price is None and offer.get("priceCurrency") == "CZK":
            car.price = _number(str(offer.get("price") or ""))
        car.equipment = list(
            dict.fromkeys(t for li in tree.css("ul.equipments li") if (t := node_value(li)))
        )
        photos = (a.attributes.get("href") or "" for a in tree.css("a[href]"))
        car.images = list(dict.fromkeys(BASE + p for p in photos if p.startswith(PHOTOS)))
        set_branch(car, tree.css_first(".branch-smallCard"), places)
        if car.location is None and lead.card is not None:
            car.place_text = car.place_text or lead.card.place_text
            car.location = branch_place(lead.card.place_text, places)
        return car if car.title else None


SOURCE = AutoPalace()
