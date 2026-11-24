"""okdrazby.cz: OK Dražby, a portal of voluntary and executor auctions held online by several
auctioneers (OK dražebník and others); ~45 vehicles at a time, among them motorcycles and
vans. Anyone registered on the portal may bid; some auctions ask for a deposit.

Server-rendered HTML (Next.js); robots.txt disallows only the account, admin and test
auctions. One nationwide listing of upcoming and running auctions, the soonest first
(`/drazby/movity/automobily?page=<n>`, 15 per page, a few promoted ones on top of the first),
read to its end on every run. Lots whose title names a motorcycle, trailer, truck or van are
not kept (the site files vans under "Osobní" too).

The price is the lowest bid ("nejnižší podání"). A card gives the title, the district, the
start of the auction, the lowest bid and a photo. The detail adds the vehicle's own table
(VIN, first registration, fuel, mileage; the plate), the seller's text, where the car can
be seen, the estimate, the deposit, the earliest end of the bidding, the auctioneer and
photos.
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import (
    CrawlSource,
    Lead,
    Listing,
    ListingPage,
    has_page_link,
    node_value,
)
from adacar.scraper.sources.auction import auction_time, model_after_make, not_a_car

SOURCE_ID = "okdrazby"
BASE = "https://okdrazby.cz"
LISTING = BASE + "/drazby/movity/automobily"
AD_PATH = re.compile(r"^/drazba/(\d+)-[\w-]*$")
PSC_TOWN = re.compile(r"(\d{3}\s?\d{2})\s+([^\W\d][^,.;\d]*)")
PHOTO = re.compile(r"https://[\w.]+/api/v1/portal/auctions/\d+/images/\d+\?created=[\w:.-]+")
REGION_DISTRICT = re.compile(r"^(.*(?:kraj|Vysočina|Praha)),\s*(.+)$")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _year(text: str | None) -> int | None:
    m = re.search(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", text or "")
    return int(m[1]) if m else None


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    link = box.css_first("a[class*=auctionName]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    title = node_value(link) or "Auto"
    url = urljoin(BASE, m[0])
    place_node = link.next
    while place_node is not None and place_node.tag != "div":
        place_node = place_node.next
    # An empty place is written as a zero-width space.
    district = clean_spaces((node_value(place_node) or "").replace("​", "")) or None
    start = node_value(box.css_first("[class*=auctionExactDateTime]"))
    price = node_value(box.css_first("[class*=auctionPrice__]"))
    car = ParsedCar(
        external_id=m[1],
        title=title,
        url=url,
        description=", ".join(x for x in (f"Dražba {start}" if start else None, district) if x),
        make=make_code(title),
        model=model_after_make(title),
        year=cars.year_in_text(title),
        price=_number(price),
        fuel=cars.fuel_in_text(title),
        gearbox=cars.gearbox_in_text(title),
        power_kw=cars.power_in_text(title),
        seller_kind="dealer",
        place_text=district,
        location=places.resolve(district) if district else None,
        expires_at=auction_time(start),
    )
    image = box.css_first("img[src]")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [src]
    return Lead(m[1], url, url, car)


def _facts(tree: LexborHTMLParser) -> dict[str, str]:
    """The auction's table: label cells ("Nejnižší podání") each followed by its value. The
    page has it twice (for wide and narrow screens)."""
    facts: dict[str, str] = {}
    cells = tree.css("div[role=gridcell]")
    for label, value in zip(cells, cells[1:], strict=False):
        if "rs-col-md-7" in (label.attributes.get("class") or ""):
            key, text = node_value(label), node_value(value)
            if key and text:
                facts.setdefault(key, text)
    return facts


def _section(tree: LexborHTMLParser, heading: str) -> LexborNode | None:
    """The text box under a heading ("Popis", "Prohlídky")."""
    for h2 in tree.css("h2"):
        if node_value(h2) == heading:
            stack = h2.parent.parent if h2.parent is not None else None
            if stack is not None:
                return stack.css_first("[class*=rtfTextContainer]")
    return None


def _lines(node: LexborNode | None) -> list[str]:
    """Lines as the seller wrote them; the vehicle's table has tabs: "Palivo\tNafta"."""
    if node is None:
        return []
    lines = (line.replace("\xa0", " ").strip() for line in node.text(separator="\n").split("\n"))
    return [line for line in lines if line]


def _place(car: ParsedCar, tree: LexborHTMLParser, viewing: str, places: PlaceResolver) -> None:
    """Where the car can be seen ("... na adrese Špindlerova třída 1, 413 01 Roudnice nad
    Labem"), else the region and district of the auction."""
    m = PSC_TOWN.search(viewing)
    if m:
        car.place_text = m[2].strip()
        car.location = places.resolve(car.place_text, postal_code=m[1])
        if car.location is not None:
            return
    for span in tree.css("span"):
        found = REGION_DISTRICT.match(node_value(span) or "")
        if found:
            car.place_text = found[2]
            car.location = places.resolve(found[2], region=found[1])
            return


def parse_detail(html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
    tree = LexborHTMLParser(html)
    title = node_value(tree.css_first("h1"))
    facts = _facts(tree)
    if not title or "Nejnižší podání" not in facts:
        return None
    lines = _lines(_section(tree, "Popis"))
    pairs = (line.split("\t", 1) for line in lines if "\t" in line)
    table = {clean_spaces(k): clean_spaces(v) for k, v in pairs}
    text = "\n".join(line for line in lines if "\t" not in line)
    viewing = "\n".join(_lines(_section(tree, "Prohlídky")))
    vehicle = next((v for k, v in table.items() if fold(k).endswith("automobil")), "")
    start, end = facts.get("Začátek"), facts.get("Konec nejdříve")
    seller = node_value(tree.css_first("[class*=contactSectionOther] p"))
    prices = f"Nejnižší podání {facts['Nejnižší podání']}"
    if estimate := facts.get("Odhadní cena"):
        prices += f", odhadní cena {estimate}"
    if deposit := facts.get("Dražební jistota"):
        prices += f", dražební jistota {deposit.lower()}"
    folded = fold(f"{title} {text}")
    car = ParsedCar(
        external_id=lead.external_id,
        title=title,
        url=lead.url,
        make=make_code(vehicle) or make_code(title),
        model=model_after_make(vehicle) or model_after_make(title),
        year=_year(table.get("První registrace")) or cars.year_in_text(title, text),
        mileage_km=_number(table.get("Stav tachometru")),
        price=_number(facts["Nejnižší podání"]),
        vat_deductible=True if "odpocet dph" in folded else None,
        fuel=cars.fuel_in_text(table.get("Palivo"), title, text),
        gearbox=cars.gearbox_in_text(title, text),
        body=cars.body_in_text(title),
        power_kw=cars.power_in_text(title, text),
        vin=(table.get("VIN karoserie") or table.get("VIN") or "").replace(" ", "").upper() or None,
        seller_kind="dealer",
        seller_name=seller,
        expires_at=auction_time(end or start),
    )
    _place(car, tree, viewing, places)
    kind = f" ({facts['Druh'].lower()})" if facts.get("Druh") else ""
    head = ", ".join(x for x in (f"Dražba {start}{kind}" if start else None, car.place_text) if x)
    car.description = "\n".join(x for x in (head, prices + ".", text, viewing) if x)
    main = tree.css_first("img[src$='/images/main']")
    photos = [main.attributes.get("src") if main is not None else None]
    photos += PHOTO.findall(html)  # the gallery is in the page's script data only
    car.images = list(dict.fromkeys(p for p in photos if p))
    return car


class OkDrazby(CrawlSource):
    id = SOURCE_ID
    label = "OK Dražby"
    kind: SourceKind = "auction"
    nationwide = True
    max_pages = 20  # 3 pages today

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        boxes = tree.css("[class*=auctionContentContainer]")
        leads = [lead for box in boxes if (lead := parse_card(box, places))]
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        return not (lead.card and not_a_car(lead.card.title))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        return parse_detail(html, lead, places)


SOURCE = OkDrazby()
