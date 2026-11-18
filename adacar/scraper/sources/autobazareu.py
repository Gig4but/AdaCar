"""autobazar.eu: a Slovak portal that also lists ~18 000 cars of Czech dealers and private
sellers (autobazar.cz redirects here). Read from the JSON its Next.js pages carry
(`__NEXT_DATA__`), HTML pages only.

robots.txt allows result pages only up to `page=50` (20 cars each), so each region is its
own listing (`?location=<region id>`), sorted by the ad's last change (`sortBy=dateDesc`,
newest first) and read until the first page with nothing new. Praha (~4 600 cars) is deeper
than 50 pages: its first run gets the newest 1 000. A region read to its end expires the
cars it no longer lists; a car unseen for a while has its page checked (404 when gone).

A record's `price` is in the seller's currency and `priceCurrent` in euro: a price that
differs from its euro amount is in koruna. A price set in euro cannot be compared with the
app's CZK prices: `price` stays empty and the amount is kept in `extra["price_eur"]`.
Values are Slovak ("Predný", "Diesel").

A listing card has nearly every field; the detail page adds the text, the dates, volume,
colour, condition, history ("1.majiteľ", "Servisná knižka") and more photos.
"""

import json
import re
from datetime import datetime
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ParsedLocation
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, slugify
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, ListingPage

SOURCE_ID = "autobazareu"
BASE = "https://www.autobazar.eu"
LISTING = BASE + "/cs/vysledky/osobne-vozidla/?location={region}&sortBy=dateDesc"
DETAIL = BASE + "/cs/detail/{sef}/{id}/"
PAGE_SIZE = 20
MAX_PAGES = 50  # robots.txt allows `page=` up to 50
DEALER = 30726  # userSubjectType of a firm
REGIONS = {
    "Hlavní město Praha": "200000200",
    "Středočeský kraj": "200000210",
    "Jihočeský kraj": "200000220",
    "Plzeňský kraj": "200000230",
    "Karlovarský kraj": "200000240",
    "Ústecký kraj": "200000250",
    "Liberecký kraj": "200000260",
    "Královéhradecký kraj": "200000270",
    "Pardubický kraj": "200000280",
    "Kraj Vysočina": "200000290",
    "Jihomoravský kraj": "200000300",
    "Olomoucký kraj": "200000310",
    "Moravskoslezský kraj": "200000320",
    "Zlínský kraj": "200000330",
}
# Checked in order: "Hybrid (Elektrina - benzín)" names petrol too.
FUELS = (("plug", "plugin_hybrid"), ("hybrid", "hybrid"), ("lpg", "lpg"), ("cng", "cng"),
         ("elektr", "electric"), ("vodik", "hydrogen"), ("diesel", "diesel"),
         ("nafta", "diesel"), ("benzin", "petrol"))  # fmt: skip
DRIVES = {"predny": "front", "zadny": "rear", "4x4": "4x4"}
CONDITIONS = (("pouzi", "used"), ("nov", "new"), ("predv", "demo"), ("havar", "damaged"))
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def page_props(html: str) -> dict:
    m = NEXT_DATA.search(html)
    if m is None:
        node = LexborHTMLParser(html).css_first("#__NEXT_DATA__")
        text = node.text() if node is not None else "{}"
    else:
        text = m[1]
    return json.loads(text).get("props", {}).get("pageProps") or {}


def _code(table: tuple[tuple[str, str], ...], value: str | None) -> str | None:
    folded = fold(value or "")
    return next((code for word, code in table if word in folded), None) if folded else None


def fuel(value: str | None) -> str | None:
    return _code(FUELS, value) or ("other" if value else None)


def gearbox(value: str | None) -> str | None:
    """ "6-st. manuálna", "Automatická"."""
    folded = fold(value or "")
    return "automatic" if "automat" in folded else "manual" if "manual" in folded else None


def body(value: str | None) -> str | None:
    """ "Combi", "Mpv/Van", "SUV / Off-road", "Iné" (other)."""
    return (cars.body_in_text(value) or "other") if value else None


def _datetime(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None) if value else None
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) or None
    except (TypeError, ValueError):
        return None


def _photo(image: Any, *sizes: str) -> str | None:
    urls = (image or {}).get("previewUrls") or {} if isinstance(image, dict) else {}
    return next((urls[size] for size in sizes if urls.get(size)), None)


def location(record: dict, places: PlaceResolver) -> tuple[str | None, ParsedLocation | None]:
    """The ad's municipality (name, place); an ad placed only in a region ("Vysočina kraj")
    is where its seller is."""
    loc = record.get("location") or {}
    parents = loc.get("parentNames") or []
    if len(parents) >= 3:  # okres, kraj, country
        district = parents[0].removeprefix("okres ")
        found = places.resolve(loc.get("name"), region=parents[-2], district=district)
        return loc.get("name"), found
    user = record.get("user") or {}
    address = user.get("address") or user
    town = clean_spaces(address.get("city") or "") or None
    found = places.resolve(town, postal_code=address.get("zip")) if town else None
    if found is None and loc.get("name"):
        found = places.resolve(loc["name"])
    return town or loc.get("name"), found


def parse_record(record: dict, places: PlaceResolver) -> ParsedCar:
    """A listing card or the detail page's `advertisement` (with more fields)."""
    brand = clean_spaces(record.get("brandValue") or "")
    user = record.get("user") or {}
    place_text, place = location(record, places)
    car = ParsedCar(
        external_id=record["id"],
        title=clean_spaces(record.get("title") or "") or brand or "Auto",
        url=DETAIL.format(sef=record.get("sefName") or "auto", id=record["id"]),
        make=make_code(brand) or (slugify(brand) or None),
        model=clean_spaces(record.get("carModelValue") or "") or None,
        year=_int(record.get("yearValue")),
        mileage_km=_int(record.get("mileage")),
        fuel=fuel(record.get("fuelValue")),
        gearbox=gearbox(record.get("gearboxValue")),
        body=body(record.get("bodyworkValue")),
        drive=DRIVES.get(fold(record.get("driveValue") or "")),
        power_kw=_int(record.get("enginePower")),
        vin=clean_spaces(record.get("vin") or "").upper() or None,
        seller_kind="dealer" if record.get("userSubjectType") == DEALER else "private",
        seller_name=clean_spaces(user.get("displayName") or "") or None,
        place_text=place_text,
        location=place,
        equipment=[e for e in (record.get("carEquipmentValue") or "").split(", ") if e.strip()],
    )
    price, euro = _int(record.get("price")), record.get("priceCurrent")
    if price and euro is not None and abs(price - float(euro)) >= 1:
        car.price = price
    elif price:
        car.extra["price_eur"] = price
    main = _photo(record.get("image"), "og_share", "record_premium", "record_preview")
    photos = [
        _photo(i, "detail_thumbnail_2x", "record_premium") for i in record.get("images") or []
    ]
    car.images = list(dict.fromkeys(p for p in [main, *photos] if p))
    return car


class AutobazarEu(CrawlSource):
    id = SOURCE_ID
    label = "Autobazar.eu"
    region_slugs = REGIONS
    max_pages = MAX_PAGES
    newest_first = True
    recheck_after_days = 14  # an ad page that is gone answers 404

    def listing_url(self, region_slug: str, page: int) -> str:
        url = LISTING.format(region=region_slug)
        return url if page == 1 else f"{url}&page={page}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        props = page_props(html)
        found = props.get("searchRecords") or {}
        leads = []
        for record in found.get("data") or []:
            car = parse_record(record, places)
            leads.append(Lead(car.external_id, car.url or "", car.url, car))
        total = found.get("total") or 0
        last = min(props.get("maxPage") or MAX_PAGES, MAX_PAGES)
        return ListingPage(
            leads,
            has_next=page < last and page * PAGE_SIZE < total,
            none_listed="searchRecords" in props and not total,
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        record = page_props(html).get("advertisement")
        if not record or not record.get("id"):
            return None
        car = parse_record(record, places)
        info = fold(record.get("additionalInformationValue") or "")
        car.description = (record.get("description") or "").strip()
        car.posted_at = _datetime(record.get("clientCreatedAt") or record.get("createdAt"))
        car.expires_at = _datetime(record.get("publishDown"))
        car.engine_cc = _int(record.get("engineCapacity"))
        car.doors = _int(record.get("numberOfDoorsValue"))
        car.seats = _int(record.get("numberOfSeatsValue"))
        car.color = clean_spaces(record.get("colorValue") or "") or None
        car.condition = _code(CONDITIONS, record.get("conditionValue"))
        car.vat_deductible = True if "odpocet dph" in info else None
        car.first_owner = True if "1.majitel" in info.replace(" ", "") else None
        car.service_book = True if "servisna knizka" in info else None
        car.crashed = False if "nehavarovane" in info else None
        return car


SOURCE = AutobazarEu()
