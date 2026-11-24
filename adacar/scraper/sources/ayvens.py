"""drive.ayvens.cz: Ayvens Carmarket (formerly ALD Automotive and LeasePlan), where the
car-leasing company sells cars from its own fleet after their lease (~120 cars, mostly 2–4
years old). Ayvens sells from U Stavoservisu 527/1, 108 00 Praha 10.

The site's pages are drawn in the browser from an open JSON API that answers GET requests
(`drive-api.myayvens.cz/api/usedcars?pageNumber=<n>&pageSize=100`, ~2 pages); the API host has
no robots.txt (404). The whole list is read on every run: a car it no longer lists is gone.
The page shown to the user is the car's page on the site (`/cs/bazar/<id>-<name>`).

A record has everything the app reads: make, model, version, first registration, model
year, mileage, price with and without VAT (whether the VAT is deductible), fuel, gearbox,
body, power, engine size, colour, doors, seats, VIN and photos; no detail page is read.
"""

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize.text import clean_spaces
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage

SOURCE_ID = "ayvens"
API = "https://drive-api.myayvens.cz"
SEARCH = API + "/api/usedcars"
PAGE = "https://drive.ayvens.cz/cs/bazar/{url}"
PAGE_SIZE = 100
SELLER = "Ayvens"
PLACE = ("Praha 10", "U Stavoservisu 527/1", "10800")

# A mild hybrid (MHEV) runs on its base fuel; "ba_el" are the plug-in hybrids (Octavia iV).
FUELS = {"petrol_95": "petrol", "petrol_98": "petrol", "ba_mhev": "petrol", "diesel": "diesel",
         "nm_mhev": "diesel", "ba_hev": "hybrid", "hybrid": "hybrid", "ba_el": "plugin_hybrid",
         "ba_erev": "plugin_hybrid", "phev": "plugin_hybrid", "electric": "electric"}  # fmt: skip
BODIES = {"suv": "suv", "kombi": "combi", "hatchback": "hatchback", "sedan": "sedan",
          "minivan": "mpv", "mini_mpv": "mpv", "pickup": "pickup", "offroad": "offroad",
          "delivery_van": "van"}  # fmt: skip
COLOURS = {"grey": "šedá", "dark_grey": "tmavě šedá", "light_grey": "světle šedá",
           "white": "bílá", "blue": "modrá", "black": "černá", "red": "červená",
           "brown": "hnědá", "green": "zelená", "yellow": "žlutá", "orange": "oranžová",
           "silver": "stříbrná", "beige": "béžová"}  # fmt: skip


def _int(value: Any) -> int | None:
    return round(value) if isinstance(value, int | float) and value else None


def model_name(text: str | None) -> str | None:
    """ "Octavia IV '22 5 dv. kombi" -> "Octavia IV"."""
    return clean_spaces(re.split(r"\s'\d\d\b", text or "")[0]) or None


def _created(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(text or "", "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None


def parse_item(item: dict, places: PlaceResolver) -> ParsedCar:
    model = model_name(item.get("model"))
    version = clean_spaces(item.get("version") or "")
    registered = re.search(r"(19|20)\d\d", item.get("dateFirstRegistration") or "")
    photos = sorted(item.get("photos") or [], key=lambda p: (not p.get("isMain"), p.get("order")))
    fuel_code, body_code = item.get("fuelCode"), item.get("bodyTypeCode")
    gearbox = {"A": "automatic", "M": "manual"}.get(item.get("transmissionCode") or "")
    return ParsedCar(
        external_id=str(item["id"]),
        title=clean_spaces(f"{item.get('make') or ''} {model or ''} {version}") or "Auto",
        url=PAGE.format(url=item.get("url") or item["id"]),
        make=make_code(item.get("make")),
        model=model,
        year=_int(item.get("modelYear")) or (int(registered[0]) if registered else None),
        mileage_km=_int(item.get("mileage")),
        price=_int(item.get("priceWithVat")),
        vat_deductible=True if item.get("vatIsDeducible") else None,
        fuel=FUELS.get(fuel_code, "other") if fuel_code else None,
        gearbox=gearbox,
        body=BODIES.get(body_code, "other") if body_code else None,
        condition="used",
        power_kw=_int(item.get("power")),
        engine_cc=_int(item.get("displacement")) if fuel_code != "electric" else None,
        color=COLOURS.get(item.get("colourCode") or ""),
        doors=_int(item.get("numberOfDoors")),
        seats=_int(item.get("numberOfSeats")),
        vin=(item.get("vin") or "").strip().upper() or None,
        seller_kind="dealer",
        seller_name=SELLER,
        place_text=PLACE[0],
        location=places.resolve(PLACE[0], street=PLACE[1], postal_code=PLACE[2]),
        posted_at=_created(item.get("dateCreated")),
        images=[API + p["url"] for p in photos if isinstance(p.get("url"), str)],
    )


class Ayvens(CrawlSource):
    id = SOURCE_ID
    label = "Ayvens"
    kind: SourceKind = "leasing"
    nationwide = True
    max_pages = 10

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.search_url)]

    @staticmethod
    def search_url(page: int) -> str:
        return f"{SEARCH}?{urlencode({'pageNumber': page, 'pageSize': PAGE_SIZE})}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        data = json.loads(html)
        leads = []
        for item in data.get("items") or []:
            if isinstance(item, dict) and item.get("id"):
                car = parse_item(item, places)
                leads.append(Lead(car.external_id, car.url or "", None, car))
        total = data.get("totalItemCount")
        return ListingPage(leads, has_next=page * PAGE_SIZE < (total or 0), none_listed=total == 0)


SOURCE = Ayvens()
