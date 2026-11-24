"""aaaauto.cz: AAA Auto, the largest Czech used-car dealer (~11 000 cars at ~30 branches).

The site is an Angular app rendered on the server: each page carries the data it was built
from as JSON in `<script id="ng-state">`, so nothing is read from the markup. The search API
behind it is not used: it takes POST requests only and a client key from the site's scripts.

One nationwide listing sorted newest first (`/ojete-vozy/?sort=14&page=<n>`, 35 cars per
page, ~2.5 MB each before compression); the walk stops at the first page with no new car. A
first run reads the whole stock. A car unseen for a while is checked on its own page: a sold
car's page redirects to the listing (`gone`).

A card has nearly everything: make, model, year, mileage, price (VAT deductible or not),
fuel, gearbox, body, power, engine size, colour, VIN, photos and the branch the car stands at
(`branchId`, the branch's CRM id, looked up in BRANCHES). The detail page adds equipment,
drive, the dealer's description and the current list of branches.
"""

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from adacar.core import ParsedCar, ParsedLocation, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage

SOURCE_ID = "aaaauto"
BASE = "https://www.aaaauto.cz"
NG_STATE = re.compile(r'<script id="ng-state" type="application/json">(.*?)</script>', re.S)
BRANCHES_API = "/api/branches/lite"

# Czech branches by CRM id: (name, town, PSČ). Detail pages bring the current list, for
# branches opened since; its towns are less tidy ("Praha - Čestlice" is not in Praha).
Branch = tuple[str, str, str]
BRANCHES: dict[str, Branch] = {
    "2": ("Praha 8 - Dolní Chabry", "Praha", "18400"),
    "4": ("Brno", "Brno", "61800"),
    "5": ("Ostrava", "Ostrava", "70200"),
    "6": ("Plzeň", "Plzeň", "30111"),
    "7": ("České Budějovice", "České Budějovice", "37004"),
    "8": ("Hradec Králové", "Hradec Králové", "50301"),
    "9": ("Teplice", "Teplice", "41501"),
    "10": ("Liberec", "Liberec", "46011"),
    "12": ("Olomouc", "Olomouc", "77200"),
    "13": ("Otrokovice (Zlín)", "Otrokovice", "76502"),
    "15": ("Jihlava", "Jihlava", "58601"),
    "16": ("Ústí nad Labem", "Ústí nad Labem", "40010"),
    "17": ("Kosmonosy (Mladá Boleslav)", "Kosmonosy", "29301"),
    "18": ("Znojmo", "Znojmo", "66902"),
    "27": ("Pardubice", "Pardubice", "53009"),
    "47": ("Chomutov", "Chomutov", "43001"),
    "49": ("Tábor", "Tábor", "39003"),
    "58": ("Kolín", "Kolín", "28002"),
    "62": ("Kladno", "Kladno", "27201"),
    "88": ("Sokolov", "Sokolov", "35601"),
    "95": ("Opava", "Opava", "74707"),
    "293": ("Praha - Čestlice", "Čestlice", "25243"),
    "302": ("Trutnov", "Trutnov", "54101"),
    "345": ("Praha - Zličín", "Praha", "15521"),
    "346": ("Valašské Meziříčí", "Valašské Meziříčí", "75701"),
    "356": ("Mototechna Praha - Hloubětín", "Praha", "19800"),
    "358": ("Karviná", "Karviná", "73301"),
    "360": ("Praha - Klecany", "Klecany", "25067"),
    "362": ("Šumperk", "Šumperk", "78701"),
    "365": ("České Budějovice 2", "České Budějovice", "37011"),
    "367": ("Třebíč", "Třebíč", "67401"),
    "368": ("Brno-sever", "Brno", "62100"),
}

FUELS = {"benzin": "petrol", "diesel": "diesel", "lpg": "lpg", "cng": "cng",
         "elektromotor": "electric", "hybrid": "hybrid"}  # fmt: skip
EV_TYPES = {"PHEV": "plugin_hybrid", "HEV": "hybrid", "FHEV": "hybrid", "BEV": "electric",
            "REEV": "electric"}  # fmt: skip
BODIES = {"suv": "suv", "kombi": "combi", "hatchback": "hatchback", "sedan": "sedan",
          "liftback": "liftback", "mpv": "mpv", "kompaktni-mpv": "mpv", "pickup": "pickup",
          "dodavka": "van", "bus": "van", "skrinovy-vuz": "van", "off-road": "offroad",
          "kupe": "coupe", "cabrio": "cabrio"}  # fmt: skip
DRIVES = {"4WD": "4x4", "FWD": "front", "RWD": "rear"}
COUNTRIES = {"CZ": "Česká republika", "SK": "Slovensko", "DE": "Německo", "AT": "Rakousko",
             "PL": "Polsko", "HU": "Maďarsko", "BE": "Belgie", "NL": "Nizozemsko",
             "FR": "Francie", "IT": "Itálie", "ES": "Španělsko", "CH": "Švýcarsko",
             "LU": "Lucembursko", "DK": "Dánsko", "SE": "Švédsko"}  # fmt: skip


def ng_state(html: str) -> dict[str, Any]:
    m = NG_STATE.search(html)
    if not m:
        return {}
    try:
        state = json.loads(m[1])
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def car_list(state: dict[str, Any]) -> dict[str, Any] | None:
    """The listing's {"items": [...], "pagination": {...}}; None on a page without one."""
    message = (state.get("car-list") or {}).get("message")
    return message if isinstance(message, dict) else None


def detail_car(state: dict[str, Any]) -> dict[str, Any] | None:
    for key, value in state.items():
        if key.startswith("detail-") and isinstance(value, dict):
            car = value.get("car") or ((value.get("message") or {}).get("items") or [None])[0]
            return car if isinstance(car, dict) else None
    return None


def live_branches(state: dict[str, Any]) -> dict[str, Branch]:
    """The branches a page was built with, by CRM id (Czech ones only)."""
    found: dict[str, Branch] = {}
    for value in state.values():
        if isinstance(value, dict) and value.get("u") == BRANCHES_API:
            for b in value.get("b") or []:
                if b.get("country") == "cz" and b.get("crmId") and b.get("city"):
                    city = re.sub(r"\s*\(.*\)|\s+\d{1,2}$", "", clean_spaces(b["city"]))
                    zip_code = re.sub(r"\D", "", b.get("zip") or "")
                    found[str(b["crmId"])] = (clean_spaces(b.get("title") or city), city, zip_code)
    return found


def _title(value: Any) -> str | None:
    return clean_spaces(value.get("title") or "") or None if isinstance(value, dict) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fuel(item: dict) -> str | None:
    """Base fuel and electric drive: a mild hybrid (MHEV) runs on its base fuel."""
    engine = item.get("engine") or {}
    if code := EV_TYPES.get(engine.get("engineEvType") or ""):
        return code
    fuel_info = item.get("fuel") or {}
    if slug := fuel_info.get("slug"):
        return FUELS.get(slug, "other")
    return cars.fuel_in_text(fuel_info.get("title"))


def gearbox(item: dict) -> str | None:
    box = item.get("gearbox") or {}
    if box.get("id") in ("A", "M"):
        return "automatic" if box["id"] == "A" else "manual"
    title = fold(box.get("title") or "")
    if title.startswith("automat"):
        return "automatic"
    return "manual" if title.startswith("manual") else None


def body(item: dict) -> str | None:
    info = item.get("body") or {}
    key = info.get("slug") or fold(info.get("title") or "").replace(" ", "-")
    return BODIES.get(key, "other") if key else None


def drive(item: dict) -> str | None:
    engine = item.get("engine") or {}
    if code := DRIVES.get(engine.get("drive") or ""):
        return code
    return "4x4" if engine.get("is4x4") else None


def make_of(item: dict) -> str | None:
    info = item.get("make") or {}
    slug = info.get("slug")
    return slug if slug in MAKES else make_code(info.get("title")) or slug or None


def stocked_on(days: Any) -> datetime | None:
    """The day a car came in, from its days in stock."""
    n = _int(days)
    return datetime.combine(date.today() - timedelta(days=n), time()) if n is not None else None


def branch_location(
    places: PlaceResolver, branch: Branch | None
) -> tuple[str | None, ParsedLocation | None]:
    if branch is None:
        return None, None
    name, town, psc = branch
    return name, places.resolve(town, postal_code=psc or None)


def parse_item(
    item: dict,
    places: PlaceResolver,
    base: str = BASE,
    seller: str = "AAA Auto",
    branches: dict[str, Branch] | None = None,
) -> ParsedCar:
    """A listing card or a detail page's car (the detail has more fields), also of
    mototechna.cz, which runs on the same system."""
    make, model = item.get("make") or {}, item.get("model") or {}
    car_id = str(item["id"])
    url = f"{base}/detail/{make.get('slug') or 'auto'}/{model.get('slug') or 'model'}/{car_id}"
    info, engine, price = item.get("body") or {}, item.get("engine") or {}, item.get("price") or {}
    history = [fold(h) for h in item.get("history") or []]
    km = _int(item.get("mileage")) if (item.get("mileageUnit") or "km") == "km" else None
    admin = item.get("adminToolbox") or {}
    branch_id = str(admin.get("branchId") or "")
    branch = BRANCHES.get(branch_id) or (branches or {}).get(branch_id)
    place_text, location = branch_location(places, branch)
    origin = item.get("originCountry")
    color = info.get("externalColor")
    return ParsedCar(
        external_id=car_id,
        title=clean_spaces(item.get("displayTitle") or "") or f"{_title(make)} {_title(model)}",
        url=url,
        description=clean_spaces(item.get("webHeadline") or ""),
        make=make_of(item),
        model=_title(model),
        year=_int(item.get("productionYear")),
        mileage_km=km,
        price=_int(price.get("cash")) or None,
        vat_deductible=price.get("isVATDeduct"),
        fuel=fuel(item),
        gearbox=gearbox(item),
        body=body(item),
        drive=drive(item),
        condition="used",
        power_kw=_int(engine.get("performanceKW")) or None,
        engine_cc=_int(engine.get("engineSize")) or None,
        color=_title(color) if isinstance(color, dict) else None,
        doors=_int(info.get("doors")) or None,
        seats=_int(info.get("seats")) or None,
        first_owner=True if any("prvnim majiteli" in h for h in history) else None,
        service_book=True if any("servisni kni" in h for h in history) else None,
        origin_country=COUNTRIES.get(origin, origin) if origin else None,
        vin=(info.get("vin") or "").strip().upper() or None,
        seller_kind="dealer",
        seller_name=seller,
        place_text=place_text,
        location=location,
        posted_at=stocked_on(admin.get("daysInStock")),
        images=[p for p in (item.get("photos") or {}).get("default") or [] if p],
    )


def add_detail(car: ParsedCar, item: dict) -> None:
    """What only the detail page has: equipment and the dealer's (generated) texts."""
    groups = item.get("equipment") or {}
    car.equipment = list(
        dict.fromkeys(
            name
            for group in (groups.values() if isinstance(groups, dict) else [])
            for e in group or []
            if (name := clean_spaces(e.get("title") or ""))
        )
    )
    texts = [car.description]
    for key in ("aiWebCommentGeneral", "aiWebCommentEquipment", "aiAdvantages"):
        texts += [clean_spaces(t) for t in item.get(key) or [] if t]
    car.description = "\n".join(t for t in texts if t)


class AaaAuto(CrawlSource):
    id = SOURCE_ID
    label = "AAA Auto"
    kind: SourceKind = "dealer"
    newest_first = True
    nationwide = True
    max_pages = 400  # the first run reads the whole stock (~314 pages): later runs stop early
    max_details = 100  # detail pages are large (~1.6 MB); cards already have the filters
    recheck_after_days = 7

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return f"{BASE}/ojete-vozy/?sort=14&page={page}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        message = car_list(ng_state(html))
        if message is None:
            return ListingPage([], has_next=False)
        leads = []
        for item in message.get("items") or []:
            if not isinstance(item, dict) or not item.get("id") or item.get("isSold"):
                continue
            car = parse_item(item, places)
            leads.append(Lead(car.external_id, car.url or "", car.url, car))
        pagination = message.get("pagination") or {}
        total = pagination.get("totalItems")
        return ListingPage(
            leads,
            has_next=bool(pagination.get("hasMore")),
            none_listed=total == 0,
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        state = ng_state(html)
        item = detail_car(state)
        if item is None or item.get("isSold") or not item.get("id"):
            return None
        car = parse_item(item, places, branches=live_branches(state))
        add_detail(car, item)
        return car

    def gone(self, html: str) -> bool:
        """A sold car's page redirects to the listing (or shows the car as sold)."""
        state = ng_state(html)
        item = detail_car(state)
        return bool(item and item.get("isSold")) or car_list(state) is not None


SOURCE = AaaAuto()
