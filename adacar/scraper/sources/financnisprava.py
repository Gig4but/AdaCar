"""financnisprava.gov.cz: auctions of the tax offices (Finanční správa), which sell seized
vehicles (~20 motor vehicles at a time, also trailers and vans). Most are electronic auctions
on drazby.fs.gov.cz: bidding needs an account there (APED), opened after the bidder's
identity is checked at a tax office or with a bank identity / data box.

Server-rendered HTML; robots.txt allows the auction pages. One nationwide listing of the
upcoming motor-vehicle auctions, the soonest first (`/cs/financni-sprava/drazby-financni-spravy/
drazby-prehled?page=<n>&stav=1&druh=DRAZBA_TYP_MOT`, 10 per page), read to its end on every
run. Lots whose title names a trailer, van or truck are not kept. An auction's page is
`.../drazby-prehled/app/detail/<id>/APED`.

The price is the lowest bid ("nejnižší podání"). A card gives the title (often just the make
and model), the day and hour of the auction, the tax office and the kind of auction. The
detail adds the lowest bid, the deposit, where the car is picked up and a short description;
the year, mileage and the rest are only in the auction's PDF notice.
"""

import re
from datetime import datetime
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import (
    CrawlSource,
    Lead,
    Listing,
    ListingPage,
    has_page_link,
    node_value,
)
from adacar.scraper.sources.auction import (
    auction_time,
    czech_time,
    model_after_make,
    not_a_car,
    utc_to_prague,
)

SOURCE_ID = "financnisprava"
LISTING = "https://financnisprava.gov.cz/cs/financni-sprava/drazby-financni-spravy/drazby-prehled"
AD_URL = re.compile(re.escape(LISTING) + r"/app/detail/(\d+)/APED$")
AMOUNT = re.compile(r"(\d{1,3}(?:[\s\xa0]\d{3})*|\d+)(?:,\d\d)?\s*Kč")
PSC_TOWN = re.compile(r"(\d{3}\s?\d{2})\s+([^\W\d][^,.;\d]*)")
OFFICE_REGION = re.compile(r"Finanční úřad pro (.+)$")


def _amount(text: str | None) -> int | None:
    """ "10 000,00 Kč" -> 10000; ",00 Kč" (none set) -> None."""
    m = AMOUNT.search(text or "")
    value = int(re.sub(r"\D", "", m[1])) if m else 0
    return value or None


def _local(utc: datetime | None) -> str:
    return czech_time(utc_to_prague(utc)) if utc else ""


def _title(text: str) -> str:
    """ "Dražba ŠKODA OCTAVIA" -> "ŠKODA OCTAVIA"."""
    return re.sub(r"^Dražba\s+", "", clean_spaces(text)) or text


def _office_place(office: str | None, places: PlaceResolver):
    """The tax office's region: "Finanční úřad pro Olomoucký kraj", "... pro hlavní město Prahu"."""
    m = OFFICE_REGION.match(office or "")
    if not m:
        return None
    region = "Hlavní město Praha" if "Prah" in m[1] else m[1]
    return places.resolve(region)


def parse_card(box: LexborNode, places: PlaceResolver) -> Lead | None:
    link = box.css_first(".b-jobs__title a[href]")
    m = AD_URL.match(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    url = m[0]
    title = _title(node_value(link) or "") or "Auto"
    meta = [node_text(span) for span in box.css(".b-jobs__meta span")]
    day, office, hour = (meta + ["", "", ""])[:3]
    kind = next((x for x in meta[3:] if "dražba" in x), "Dražba")
    when = auction_time(f"{day} {hour}")
    car = ParsedCar(
        external_id=m[1],
        title=title,
        url=url,
        description=", ".join(x for x in (f"{kind} {_local(when)}".strip(), office) if x),
        make=make_code(title),
        model=model_after_make(title),
        year=cars.year_in_text(title),
        fuel=cars.fuel_in_text(title),
        seller_kind="dealer",
        seller_name=office or None,
        location=_office_place(office, places),
        expires_at=when,
    )
    return Lead(m[1], url, url, car)


def _facts(tree: LexborHTMLParser) -> dict[str, str]:
    facts = {}
    for caption in tree.css(".b-info__caption"):
        value = caption.parent.css_first(".b-info__title") if caption.parent else None
        key = (node_value(caption) or "").rstrip(":")
        if key and value is not None:
            facts[key] = node_text(value)
    return facts


def parse_detail(html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
    tree = LexborHTMLParser(html)
    heading = node_value(tree.css_first(".b-box__title"))
    facts = _facts(tree)
    when = facts.get("Datum a čas")
    if not heading or not when:
        return None
    office = heading.split(" oznamuje")[0]
    about = tree.css_first(".b-accordion__content")
    text = node_text(about).strip(" ,") if about is not None else ""
    title = lead.card.title if lead.card else _title(text) or "Auto"
    pickup = facts.get("Místo vyzvednutí") or ""
    lowest = _amount(facts.get("Celková výše nejnižšího/nejnižších podání"))
    deposit = _amount(facts.get("Dražební jistota"))
    prices = f"Nejnižší podání {lowest:,} Kč".replace(",", " ") if lowest else ""
    if deposit:
        prices += f", dražební jistota {deposit:,} Kč".replace(",", " ")
    car = ParsedCar(
        external_id=lead.external_id,
        title=title,
        url=lead.url,
        make=make_code(title) or make_code(text),
        model=model_after_make(title) or model_after_make(text),
        year=cars.year_in_text(title, text),
        mileage_km=cars.km_in_text(title, text),
        price=lowest,
        fuel=cars.fuel_in_text(title, text),
        power_kw=cars.power_in_text(title, text),
        seller_kind="dealer",
        seller_name=office,
        expires_at=auction_time(when),
    )
    m = PSC_TOWN.search(pickup)
    if m:
        car.place_text = m[2].strip()
        car.location = places.resolve(car.place_text, postal_code=m[1])
    if car.location is None:
        car.location = _office_place(office, places)
    head = ", ".join(x for x in (f"Dražba {_local(car.expires_at)}", car.place_text or office) if x)
    pickup_line = f"Místo vyzvednutí: {pickup}" if pickup else ""
    car.description = "\n".join(x for x in (head, prices, text, pickup_line) if x)
    return car


class FinancniSprava(CrawlSource):
    id = SOURCE_ID
    label = "Dražby Finanční správy"
    kind: SourceKind = "auction"
    nationwide = True
    max_pages = 20  # 3 pages today

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        params = {"page": page} if page > 1 else {}
        return f"{LISTING}?{urlencode({**params, 'stav': 1, 'druh': 'DRAZBA_TYP_MOT'})}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css(".b-jobs__item") if (lead := parse_card(box, places))]
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        return not (lead.card and not_a_car(lead.card.title))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        return parse_detail(html, lead, places)


SOURCE = FinancniSprava()
