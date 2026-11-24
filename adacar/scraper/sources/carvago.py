"""carvago.com: Carvago, an online car shop that sells used cars from dealers all over the EU
(~1 000 000 cars, mostly in Germany) and delivers them imported and registered in Czechia.
None of its cars stands in Czechia: its car-location filter (`country[]=10`) finds none.

**Off by default**: every car is an import from abroad, and the stock is far too large to
read whole. Server-rendered Next.js pages carry their data in `__NEXT_DATA__`; robots.txt
allows everything. The listing (`/cs/auta[/<make>]?price-to=<max>&limit=100&page=<n>`) is
narrowed by the user's makes (one listing each) and highest price, and is newest first by
default (asking for that order redirects to the URL without it). Only its first pages are
read on each run, and a car not seen there for a few days expires, so the app keeps the
newest imports that fit the search. The cars have no Czech place, so they are listed only
while no travel-time limit is set.

A card has the make, model, year, mileage, power, price in CZK, whether the VAT is
deductible, fuel, gearbox, body, drive, colour, condition, the country the car stands in and
photos; detail pages (seller's town, engine size) are not read.
"""

import json
from functools import partial
from typing import Any
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage

SOURCE_ID = "carvago"
BASE = "https://carvago.com"
LISTING = BASE + "/cs/auta"
PAGE = BASE + "/cs/auto/{id}/{slug}"
PAGE_SIZE = 100

FUELS = {"FUELTYPE_PETROL": "petrol", "FUELTYPE_DIESEL": "diesel", "FUELTYPE_LPG": "lpg",
         "FUELTYPE_CNG": "cng", "FUELTYPE_HYBRID": "hybrid", "FUELTYPE_ELECTRIC": "electric",
         "FUELTYPE_HYDROGEN": "hydrogen", "FUELTYPE_ETHANOL": "other",
         "FUELTYPE_OTHER": "other"}  # fmt: skip
GEARBOXES = {"TRANSMISSION_AUTOMATIC": "automatic", "TRANSMISSION_MANUAL": "manual"}
BODIES = {"CARSTYLE_HATCHBACK": "hatchback", "CARSTYLE_ESTATE_CAR": "combi",
          "CARSTYLE_SEDANS_SALOONS": "sedan", "CARSTYLE_SUV_OFFROAD": "suv",
          "CARSTYLE_MPV": "mpv", "CARSTYLE_COUPE": "coupe", "CARSTYLE_CABRIOLET": "cabrio",
          "CARSTYLE_PICK_UP": "pickup", "CARSTYLE_VAN": "van"}  # fmt: skip
DRIVES = {"DRIVE_4X4": "4x4", "DRIVE_FRONT": "front", "DRIVE_REAR": "rear"}
CONDITIONS = {"CONDITION_USED": "used", "CONDITION_CLASSIC_CAR": "used", "CONDITION_NEW": "new",
              "CONDITION_DEMONSTRATION_CAR": "demo", "CONDITION_DAMAGED": "damaged",
              "CONDITION_SPARE_PARTS": "damaged"}  # fmt: skip


def next_data(html: str) -> dict:
    node = LexborHTMLParser(html).css_first("script#__NEXT_DATA__")
    return json.loads(node.text()) if node is not None else {}


def labels(page_props: dict) -> dict[str, str]:
    """Czech names of the page's codes: "COLOR_GREY" -> "Šedá", "COUNTRY_GERMANY" -> "Německo"."""
    reference = (page_props.get("initialState") or {}).get("referenceData") or {}
    names = {c["const_key"]: c["name"] for c in reference.get("country") or [] if c.get("name")}
    for group in (reference.get("catalog") or {}).get("enum") or []:
        for key in group.get("keys") or []:
            if key.get("label"):
                names.setdefault(key["const_key"], key["label"])
    return names


def _pick(table: dict[str, str], features: list[str]) -> str | None:
    return next((table[f] for f in features if f in table), None)


def fuel(features: list[str], title: str) -> str | None:
    """Plug-in hybrids are listed as "FUELTYPE_HYBRID": the title tells them apart."""
    listed = _pick(FUELS, features)
    if listed == "hybrid" and cars.fuel_in_text(title) == "plugin_hybrid":
        return "plugin_hybrid"
    return listed


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and value else None


def parse_item(item: dict, names: dict[str, str]) -> ParsedCar:
    features = [f for f in item.get("catalog_features") or [] if isinstance(f, str)]
    title = clean_spaces(item.get("title") or "")
    price = item.get("price_information") or {}
    seller = (item.get("seller") or {}).get("type")
    color = next((f for f in features if f.startswith("COLOR_")), None)
    return ParsedCar(
        external_id=str(item["id"]),
        title=title or "Auto",
        url=PAGE.format(id=item["id"], slug=item.get("slug") or "auto"),
        make=make_code(item.get("make")),
        model=clean_spaces(item.get("model") or "") or None,
        year=_year(item.get("registration_date")),
        mileage_km=_int(item.get("mileage")),
        price=_int((price.get("nice_price_data") or {}).get("price"))
        if price.get("currency") == "CZK"
        else None,
        vat_deductible=True if item.get("vat_reclaimable") else None,
        fuel=fuel(features, title),
        gearbox=_pick(GEARBOXES, features),
        body=_pick(BODIES, features),
        drive=_pick(DRIVES, features),
        condition=_pick(CONDITIONS, features),
        power_kw=_int(item.get("power")) if item.get("power_unit") == "kW" else None,
        color=names.get(color) if color else None,
        seller_kind="private" if seller == "SELLERTYPE_PRIVATE_SELLER" else "dealer",
        place_text=names.get(item.get("location_country") or ""),
        images=[u for u in item.get("images") or [] if isinstance(u, str) and u],
    )


class Carvago(CrawlSource):
    id = SOURCE_ID
    label = "Carvago (dovoz z EU)"
    kind: SourceKind = "portal"
    default_enabled = False
    nationwide = True
    max_pages = 10  # the newest 1 000 cars of each listing
    expire_after_days = 3

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [
            (None, partial(self.search_url, make, query.max_price))
            for make in query.makes or [None]
        ]

    @staticmethod
    def search_url(make: str | None, max_price: int | None, page: int) -> str:
        params: dict[str, Any] = {}
        if max_price is not None:
            params["price-to"] = max_price
        params["limit"] = PAGE_SIZE
        if page > 1:
            params["page"] = page
        return f"{LISTING}{'/' + make if make else ''}?{urlencode(params)}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        props = (next_data(html).get("props") or {}).get("pageProps") or {}
        results = props.get("searchResults")
        if not isinstance(results, dict):
            return ListingPage([], has_next=False)
        names = labels(props)
        leads = []
        for item in results.get("items") or []:
            if isinstance(item, dict) and item.get("id"):
                car = parse_item(item, names)
                leads.append(Lead(car.external_id, car.url or "", None, car))
        total = results.get("total")
        return ListingPage(leads, has_next=page * PAGE_SIZE < (total or 0), none_listed=total == 0)


SOURCE = Carvago()
