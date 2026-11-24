"""exdrazby.cz: Exdražby, a portal where many auctioneers and executors hold online auctions
and public tenders ("e-veřejná soutěž"); ~30 passenger cars upcoming or running at a time.
Anyone registered on the portal may bid; executor auctions and some others ask for a
deposit first.

The site is a JavaScript app over an open JSON API (robots.txt allows everything). The list
(`/api/web/auction/?page=<n>&perPage=15&sort=<json>&filter=<json>`) is filtered to the
"Osobní automobil" category and the upcoming and running auctions, soonest first; it is read
to its end on every run. Lots whose title names a van or truck are not kept (the category has
some). Each auction's record is `/api/web/auction/<id>?type=<auction type>`; the page shown to
the user is `/drazba/<id>` (`/verejna-soutez/<id>`, `/aukce/<id>` for those types).

The price is the starting bid ("vyvolávací cena"; the lowest offer in a public tender). A
card gives the title, the starting bid, the start and end of the bidding, the town and region
and photos. The record adds the auctioneer, the district, the estimate, the deposit, where
and when the car can be seen, and the notice's text, from which the year, mileage, VIN, fuel,
gearbox and power are read.
"""

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlencode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, html_to_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage
from adacar.scraper.sources.auction import (
    model_after_make,
    not_a_car,
    notice_fuel,
    notice_km,
    notice_vin,
    notice_year,
)

SOURCE_ID = "exdrazby"
BASE = "https://www.exdrazby.cz"
API = "https://exdrazby.cz/api/web/auction/"
PAGE_SIZE = 15  # the API's own page size: it gives no more
CARS = 17  # the "Osobní automobil" category
PAGES = {"auction": "/aukce/{id}", "public_competition": "/verejna-soutez/{id}"}
STATUS = {"operator": "eq", "type": "string", "property": "auction.status"}
FILTER = [
    [{**STATUS, "value": "prepared"}, {**STATUS, "value": "ongoing"}],  # one group: either
    {"value": CARS, "operator": "eq", "type": "int", "property": "auctionCategory.id"},
]


def _utc(stamp: str | None) -> datetime | None:
    """ "2026-12-03T10:00:00+01:00" as naive UTC."""
    if not stamp:
        return None
    return datetime.fromisoformat(stamp).astimezone(UTC).replace(tzinfo=None)


def _local(stamp: str | None) -> str | None:
    """ "2026-12-03T10:00:00+01:00" -> "3. 12. 2026 10:00", as the site shows it."""
    if not stamp:
        return None
    when = datetime.fromisoformat(stamp)
    return f"{when.day}. {when.month}. {when.year} {when.hour}:{when.minute:02d}"


def _money(amount: int | float) -> str:
    return f"{int(amount):,} Kč".replace(",", " ")


def page_url(item: dict) -> str:
    path = PAGES.get(item.get("auctionType") or "", "/drazba/{id}")
    return BASE + path.format(id=item["id"])


def _headline(item: dict, place: str | None) -> str:
    kind = "Veřejná soutěž" if item.get("auctionType") == "public_competition" else "Dražba"
    start = _local(item.get("startDt"))
    return ", ".join(x for x in (f"{kind} {start}" if start else kind, place) if x)


def _photos(item: dict) -> list[str]:
    return [
        path
        for m in item.get("auctionMedia") or []
        if (m.get("media") or {}).get("type") == "image" and (path := m.get("detailImagePath"))
    ]


def _place(car: ParsedCar, city: str | None, region: str | None, district: str | None,
           places: PlaceResolver) -> None:  # fmt: skip
    car.place_text = clean_spaces(city or "") or None
    if car.place_text:
        car.location = places.resolve(car.place_text, region=region, district=district)
    elif region:
        car.location = places.resolve(region)


def parse_card(item: dict, places: PlaceResolver) -> Lead | None:
    title = clean_spaces(item.get("title") or "")
    if not item.get("id") or not title:
        return None
    external_id = str(item["id"])
    url = page_url(item)
    address = item.get("auctionAddress") or {}
    region = (address.get("region") or {}).get("name")
    car = ParsedCar(
        external_id=external_id,
        title=title,
        url=url,
        make=make_code(title),
        model=model_after_make(title),
        year=cars.year_in_text(title),
        price=int(item["minimalBid"]) if item.get("minimalBid") else None,
        fuel=cars.fuel_in_text(title),
        gearbox=cars.gearbox_in_text(title),
        body=cars.body_in_text(title),
        power_kw=cars.power_in_text(title),
        seller_kind="dealer",
        expires_at=_utc(item.get("endDt") or item.get("startDt")),
        images=_photos(item),
    )
    _place(car, address.get("city"), region, None, places)
    car.description = _headline(item, car.place_text)
    fetch_url = f"{API}{external_id}?{urlencode({'type': item.get('auctionType') or ''})}"
    return Lead(external_id, url, fetch_url, car)


def parse_record(data: dict, places: PlaceResolver) -> ParsedCar | None:
    item = data.get("data") or {}
    title = clean_spaces(item.get("title") or "")
    if not item.get("id") or not title:
        return None
    text = html_to_text(re.sub(r"\s+", " ", item.get("description") or ""))
    flat = clean_spaces(text)
    tender = item.get("auctionType") == "public_competition"
    bid = "Nejnižší nabídka" if tender else "Vyvolávací cena"
    prices = f"{bid} {_money(item['minimalBid'])}" if item.get("minimalBid") else ""
    if item.get("estimatedPrice"):
        prices += f", odhad {_money(item['estimatedPrice'])}"
    if item.get("cautionDeposit"):
        prices += f", dražební jistota {_money(item['cautionDeposit'])}"
    viewing = (item.get("visitationDescription") or "").strip()
    auctioneer = item.get("auctioneer") or {}
    car = ParsedCar(
        external_id=str(item["id"]),
        title=title,
        url=page_url(item),
        make=make_code(title) or make_code(flat),
        model=model_after_make(title),
        year=notice_year(flat, title),
        mileage_km=notice_km(flat),
        price=int(item["minimalBid"]) if item.get("minimalBid") else None,
        fuel=notice_fuel(title, flat),
        gearbox=cars.gearbox_in_text(title, flat),
        body=cars.body_in_text(title),
        power_kw=cars.power_in_text(title, flat),
        vin=notice_vin(flat),
        seller_kind="dealer",
        seller_name=clean_spaces(auctioneer.get("auctioneerName") or "") or None,
        seller_ico=auctioneer.get("ic") or None,
        expires_at=_utc(item.get("endDt") or item.get("startDt")),
        images=_photos(item),
    )
    _place(car, item.get("city"), item.get("region"), item.get("district"), places)
    head = _headline(item, car.place_text)
    parts = (head, f"{prices}." if prices else "", text, f"Prohlídka: {viewing}" if viewing else "")
    car.description = "\n".join(x for x in parts if x)
    return car


class Exdrazby(CrawlSource):
    id = SOURCE_ID
    label = "Exdražby"
    kind: SourceKind = "auction"
    nationwide = True
    max_pages = 20  # 2 pages today

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.search_url)]

    @staticmethod
    def search_url(page: int) -> str:
        params = {
            "page": page,
            "perPage": PAGE_SIZE,
            "sort": json.dumps({"property": "auction.startDt", "direction": "ASC"}),
            "filter": json.dumps(FILTER),
        }
        return f"{API}?{urlencode(params)}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        data = json.loads(html)
        items = data.get("data") or []
        leads = [lead for item in items if (lead := parse_card(item, places))]
        total = data.get("total")
        return ListingPage(leads, has_next=page * PAGE_SIZE < (total or 0), none_listed=total == 0)

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        return not (lead.card and not_a_car(lead.card.title))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        return parse_record(json.loads(html), places)


SOURCE = Exdrazby()
