"""sauto.cz: the largest Czech car portal (~110k passenger cars), read through the JSON API
its own pages use (`/api/v1/items/search` and `/api/v1/items/<id>`).

**Off by default**: sauto.cz's robots.txt allows only named search engines (`User-agent: *`
/ `Disallow: /`), so the user switches it on after a warning (PLAN §12). Then it is read
slowly, like one person browsing: 3 s between requests, 100 cars per listing request.

The API pages at most 10 000 results deep, so each region is read in price bands that stay
below that (Praha, the largest, has ~5 000 cars between 200 and 300 thousand), sorted by
price: the default order moves every edited or topped ad to the front while the pages are
read. A region read to the end expires the cars it no longer lists there; a band cut off at
the depth limit is not read to the end. A listing card has nearly everything
the filters use; the detail adds the VIN, text, equipment and body.
"""

import json
import re
from datetime import datetime
from functools import partial
from typing import Any
from urllib.parse import urlencode

from adacar.core import ParsedCar, ScrapeQuery, SellerKind
from adacar.geo.places import PlaceResolver
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import MAKES
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage

SOURCE_ID = "sauto"
BASE = "https://www.sauto.cz"
SEARCH = BASE + "/api/v1/items/search"
DETAIL = BASE + "/api/v1/items/{id}"
PAGE = BASE + "/osobni/detail/{make}/{model}/{id}"
CATEGORY = 838  # passenger cars
PAGE_SIZE = 100
MAX_OFFSET = 10_000
# Price bands, CZK: each region's band stays below MAX_OFFSET results.
EDGES = (0, 50_000, 100_000, 150_000, 200_000, 250_000, 300_000, 400_000, 500_000, 700_000,
         1_000_000, 1_500_000)  # fmt: skip
BANDS: tuple[tuple[int, int | None], ...] = tuple(zip(EDGES, (*EDGES[1:], None), strict=True))

FUELS = {"benzin": "petrol", "nafta": "diesel", "lpg-benzin": "lpg", "cng-benzin": "cng",
         "hybridni": "hybrid", "elektro": "electric", "vodik": "hydrogen"}  # fmt: skip
GEARBOXES = {"manualni": "manual", "automaticka": "automatic", "poloautomaticka": "automatic"}
BODIES = {"hatchback": "hatchback", "sedanlimuzina": "sedan", "liftback": "liftback",
          "kombi": "combi", "suv": "suv", "cuv": "suv", "terenni": "offroad", "mpv": "mpv",
          "kupe": "coupe", "kabriolet": "cabrio", "roadster": "cabrio", "pick-up": "pickup",
          "dodavka": "van", "van": "van"}  # fmt: skip


def _seo(value: Any) -> str:
    return (value or {}).get("seo_name") or "" if isinstance(value, dict) else ""


def _name(value: Any) -> str | None:
    return clean_spaces(value.get("name") or "") or None if isinstance(value, dict) else None


def fuel(value: Any) -> str | None:
    seo = _seo(value)
    if not seo:
        return None
    return "plugin_hybrid" if "plug" in seo else FUELS.get(seo, "other")


def drive(value: Any) -> str | None:
    """ "Pohon předních kol", "Pohon zadních kol", "Pohon všech kol (4x4)"."""
    name = fold(_name(value) or "")
    if "4x4" in name or "vsech" in name:
        return "4x4"
    if "predn" in name:
        return "front"
    if "zadn" in name:
        return "rear"
    return None


def condition(value: Any) -> str | None:
    """ "Ojeté", "Nové", "Předváděcí", "Havarované"."""
    name = fold(_name(value) or "")
    for word, code in (("ojet", "used"), ("nov", "new"), ("predvad", "demo"), ("havar", "damaged")):
        if name.startswith(word):
            return code
    return None


def _year(item: dict) -> int | None:
    for key in ("manufacturing_date", "in_operation_date"):
        if m := re.match(r"(\d{4})", item.get(key) or ""):
            return int(m[1])
    return None


def _datetime(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _photo(url: str | None) -> str | None:
    return ("https:" + url if url.startswith("//") else url) if url else None


def page_url(item: dict) -> str:
    return PAGE.format(
        make=_seo(item.get("manufacturer_cb")) or "auto",
        model=_seo(item.get("model_cb")) or "model",
        id=item["id"],
    )


def parse_item(item: dict, places: PlaceResolver) -> ParsedCar:
    """A listing card or a detail record (the detail has more fields)."""
    make = _seo(item.get("manufacturer_cb")) or None
    loc = item.get("locality") or {}
    premise = item.get("premise") or None
    seller: SellerKind = "dealer" if premise else "private"
    car = ParsedCar(
        external_id=str(item["id"]),
        title=clean_spaces(item.get("name") or "") or (_name(item.get("model_cb")) or "Auto"),
        url=page_url(item),
        description=(item.get("description") or "").strip(),
        make=make if make in MAKES else (make or None),
        model=_name(item.get("model_cb")),
        year=_year(item),
        mileage_km=item.get("tachometer") or None,
        price=None if item.get("price_by_agreement") else item.get("price") or None,
        vat_deductible=item.get("price_is_vat_deductible"),
        fuel=fuel(item.get("fuel_cb")),
        gearbox=GEARBOXES.get(_seo(item.get("gearbox_cb"))) if item.get("gearbox_cb") else None,
        body=BODIES.get(_seo(item.get("vehicle_body_cb")), "other")
        if item.get("vehicle_body_cb")
        else None,
        drive=drive(item.get("drive_cb")),
        condition=condition(item.get("condition_cb")),
        power_kw=item.get("engine_power") or None,
        engine_cc=item.get("engine_volume") or None,
        color=_name(item.get("color_cb")),
        doors=item.get("doors") or None,
        seats=item.get("capacity") or None,
        first_owner=item.get("first_owner"),
        service_book=item.get("service_book"),
        crashed=item.get("crashed_in_past"),
        origin_country=_name(item.get("country_of_origin_cb")),
        vin=(item.get("vin") or "").strip().upper() or None,
        seller_kind=seller,
        seller_name=premise.get("name") if premise else None,
        place_text=loc.get("municipality") or loc.get("district") or None,
        equipment=[e["name"] for e in item.get("equipment_cb") or [] if e.get("name")],
        posted_at=_datetime(item.get("create_date")),
        expires_at=_datetime(item.get("valid_to")),
        images=[p for i in item.get("images") or [] if (p := _photo(i.get("url")))],
    )
    if loc.get("municipality"):
        car.location = places.resolve(
            loc["municipality"],
            postal_code=loc.get("zip") or None,
            region=loc.get("region") or None,
            district=loc.get("district") or None,
        )
    elif loc.get("district"):
        car.location = places.resolve(loc["district"])
    return car


class Sauto(CrawlSource):
    id = SOURCE_ID
    label = "Sauto.cz"
    default_enabled = False
    needs_consent = True
    obey_robots = False  # only once the user switched it on (needs_consent)
    delay = 3.0
    max_pages = MAX_OFFSET // PAGE_SIZE
    max_details = 100

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        bands = [b for b in BANDS if query.max_price is None or b[0] <= query.max_price]
        return [
            (kraj, partial(self.search_url, self.region_slugs[kraj], low, high, query.makes))
            for kraj in query.kraje
            for low, high in bands
        ]

    @staticmethod
    def search_url(region: str, low: int, high: int | None, makes: list[str], page: int) -> str:
        params: dict[str, Any] = {
            "category_id": CATEGORY,
            "limit": PAGE_SIZE,
            "offset": (page - 1) * PAGE_SIZE,
            "region_seo": region,
            "price_from": low,
            "sort": "price",
        }
        if high is not None:
            params["price_to"] = high - 1
        if makes:
            params["manufacturer_model_seo"] = "|".join(makes)
        return f"{SEARCH}?{urlencode(params)}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        data = json.loads(html)
        leads = []
        for item in data.get("results") or []:
            car = parse_item(item, places)
            leads.append(Lead(car.external_id, car.url or "", DETAIL.format(id=item["id"]), car))
        pagination = data.get("pagination")
        total = (pagination or {}).get("total") or 0
        # Past the depth limit has_next stays true: the walk stops at max_pages, unfinished.
        return ListingPage(
            leads,
            has_next=page * PAGE_SIZE < total,
            none_listed=pagination is not None and not total,
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        item = json.loads(html).get("result")
        if not item or item.get("status") not in (None, "active"):
            return None
        return parse_item(item, places)

    def check_url(self, url: str) -> str:
        """The ad's record in the API: its public page (…/<id>) is HTML."""
        return DETAIL.format(id=url.rstrip("/").rsplit("/", 1)[-1])

    def gone(self, html: str) -> bool:
        try:
            item = json.loads(html).get("result") or {}
        except ValueError:
            return False
        return item.get("status") not in (None, "active")


SOURCE = Sauto()
