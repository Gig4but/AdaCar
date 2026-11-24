"""portaldrazeb.cz: Portál dražeb, the auctions of the Czech executors (Exekutorská komora) and
some voluntary auctions, from all executors' own auction sites (~100 lots in "Auto-moto" at a
time, among them trucks, motorcycles and machines). Bidding in an electronic auction needs a
registration on the executor's auction site and usually a deposit; others are held in person.

The site is a JavaScript app over JSON that answers GET requests (robots.txt allows all): the
upcoming and the running auctions (`/drazby/pripravovane.json`, `/drazby/probihajici.json`)
take a `filter` parameter in JSON: the "Auto-moto" category (33), 50 lots per page by
`offset`, the soonest first. Both are read to their end on every run. Lots whose title names
a truck, van, motorcycle or machine are not kept. A lot of the list has all the site shows of
it, so no other page is read; the user is shown the auction's page (`/drazba/<slug>`).

The price is the lowest bid ("nejnižší podání", for movables a third of the estimate). A lot
gives its title, the executor's text (often with the VIN, year and mileage; the rest is in the
PDF notice), the estimate, the deposit, the start and end of the auction, the town and
district and photos.
"""

import json
from datetime import UTC, datetime
from functools import partial
from urllib.parse import urlencode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, html_to_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage
from adacar.scraper.sources.auction import (
    czech_time,
    model_after_make,
    not_a_car,
    notice_fuel,
    notice_km,
    notice_vin,
    notice_year,
    utc_to_prague,
)

SOURCE_ID = "portaldrazeb"
BASE = "https://www.portaldrazeb.cz"
LISTS = ("pripravovane", "probihajici")  # upcoming, running
CARS = 33  # the "Auto-moto" category
PAGE_SIZE = 50
PHOTO = BASE + "/media/cache/thumb_large"


def _utc(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    return datetime.fromisoformat(stamp).astimezone(UTC).replace(tzinfo=None)


def _money(amount: int | float) -> str:
    return f"{int(amount):,} Kč".replace(",", " ")


def _headline(lot: dict, start: datetime | None, place: str | None) -> str:
    """ "Dražba 27. 11. 2026 9:00, Znojmo (elektronická)"."""
    when = f"Dražba {czech_time(utc_to_prague(start))}" if start else "Dražba"
    how = " (elektronická)" if lot.get("electronic") or lot.get("online") else ""
    return ", ".join(x for x in (when, place) if x) + how


def _prices(lot: dict, lowest: int | None) -> str:
    parts = [f"Nejnižší podání {_money(lowest)}" if lowest else ""]
    if lot.get("estimated_price"):
        parts.append(f"odhadní cena {_money(lot['estimated_price'])}")
    if lot.get("caution_deposit"):
        parts.append(f"dražební jistota {_money(lot['caution_deposit'])}")
    text = ", ".join(x for x in parts if x)
    return text[:1].upper() + text[1:] + "." if text else ""


def parse_lot(lot: dict, places: PlaceResolver) -> Lead | None:
    item = lot.get("item") or {}
    title = clean_spaces(item.get("title") or "")
    if not lot.get("hash") or not title:
        return None
    text = html_to_text(item.get("description"))
    flat = clean_spaces(text)
    district = item.get("location_district") or lot.get("location_district") or {}
    region = (district.get("county") or {}).get("county_name")
    town = (district.get("city") or {}).get("city_name")
    start = _utc(lot.get("start_at"))
    lowest = lot.get("item_price") or lot.get("minimal_bid")
    office = lot.get("auctioneer_office") or {}
    url = lot.get("link") or f"{BASE}/drazba/{lot.get('slug')}"
    car = ParsedCar(
        external_id=lot["hash"],
        title=title,
        url=url,
        make=make_code(title) or make_code(flat),
        model=model_after_make(title) or model_after_make(flat),
        year=notice_year(flat, title),
        mileage_km=notice_km(flat),
        price=int(lowest) if lowest else None,
        fuel=notice_fuel(title, flat),
        gearbox=cars.gearbox_in_text(title, flat),
        body=cars.body_in_text(title),
        power_kw=cars.power_in_text(title, flat),
        vin=notice_vin(flat),
        seller_kind="dealer",
        seller_name=f"Soudní exekutor {office['title']}" if office.get("title") else None,
        place_text=town or district.get("district_name"),
        expires_at=_utc(lot.get("end_at")) or start,
    )
    if car.place_text:
        car.location = places.resolve(
            car.place_text, region=region, district=district.get("district_name")
        )
    head = _headline(lot, start, car.place_text)
    car.description = "\n".join(x for x in (head, _prices(lot, car.price), text) if x)
    photos = sorted((lot.get("images") or {}).values(), key=lambda p: p.get("priority") or 0)
    car.images = [PHOTO + p["pathname"] for p in photos if p.get("pathname")]
    return Lead(car.external_id, url, None, car)


class PortalDrazeb(CrawlSource):
    id = SOURCE_ID
    label = "Portál dražeb (exekutoři)"
    kind: SourceKind = "auction"
    nationwide = True
    max_pages = 20  # 2 pages of upcoming lots today

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, partial(self.list_url, name)) for name in LISTS]

    @staticmethod
    def list_url(name: str, page: int) -> str:
        filt = {
            "category": [CARS],
            "limit": PAGE_SIZE,
            "offset": (page - 1) * PAGE_SIZE,
            "sort": "asc",
            "sort_by": "start",
        }
        return f"{BASE}/drazby/{name}.json?{urlencode({'filter': json.dumps(filt)})}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        data = json.loads(html)
        lots = [v for k, v in data.items() if k != "@count" and isinstance(v, dict)]
        leads = [lead for lot in lots if (lead := parse_lot(lot, places))]
        total = data.get("@count") or 0
        none_listed = data.get("@count") == 0 and not lots
        return ListingPage(leads, has_next=page * PAGE_SIZE < total, none_listed=none_listed)

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        return not (lead.card and not_a_car(lead.card.title))


SOURCE = PortalDrazeb()
