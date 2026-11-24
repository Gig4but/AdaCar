"""dasweltauto.cz: Das WeltAuto, the certified used cars of the Volkswagen group's dealers
(Škoda, VW, Audi, Seat, Cupra and the trade-ins they take; ~5 500 cars), each at a dealer.

The site's own pages call a JSON API that answers GET requests: the search
(`/api/locales/cs_CZ/vehicles/search/?page=<n>&pageSize=100&sortBy=recent`) and each car's
record (`/api/locales/cs_CZ/vehicles/<id>/detail`, 404 once the car is sold). robots.txt
allows both.

One nationwide listing, newest first, read to its end on every run (~56 pages): a car the
complete pass no longer lists is gone. A card has nearly everything: make, model, year,
mileage, price, fuel, gearbox, body, drive, power, colour, VIN, photos and the dealer with
its address. The detail adds the engine size, equipment, the dealer's note and whether the
price includes VAT the buyer can deduct (a net price is shown).
"""

import json
from typing import Any
from urllib.parse import urlencode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage

SOURCE_ID = "dasweltauto"
BASE = "https://www.dasweltauto.cz"
API = BASE + "/api/locales/cs_CZ/vehicles"
SEARCH = API + "/search/"
DETAIL = API + "/{id}/detail"
PAGE = BASE + "/vehicle/{id}"
PAGE_SIZE = 100
PHOTO_SIZE = "/1024.jpg"  # the image server answers a bare image URL with HTTP 405

BODIES = {"hatchback": "hatchback", "liftback": "liftback", "kombi": "combi", "suv": "suv",
          "mpv": "mpv", "pick-up": "pickup", "skrin": "van", "van": "van", "minibus": "van",
          "limuzina": "sedan", "sedan": "sedan", "kupe": "coupe", "kabriolet": "cabrio",
          "roadster": "cabrio"}  # fmt: skip


def fuel(text: str | None) -> str | None:
    """ "Benzín", "Diesel", "Elektro", "Hybr. B/E ext. nab." (plug-in), "Zemní plyn (CNG)"."""
    name = fold(text or "")
    if not name:
        return None
    for words, code in (
        (("ext. nab", "plug"), "plugin_hybrid"),
        (("hybr",), "hybrid"),
        (("cng", "zemni"), "cng"),
        (("lpg", "kapaln"), "lpg"),
        (("elektr",), "electric"),
        (("diesel", "nafta"), "diesel"),
        (("benzin",), "petrol"),
    ):
        if any(word in name for word in words):
            return code
    return "other"


def gearbox(text: str | None) -> str | None:
    name = fold(text or "")
    if name.startswith("autom"):
        return "automatic"
    return "manual" if name.startswith("manu") else None


def body(text: str | None) -> str | None:
    """ "Kombi", "SUV", "Skříň" (a van), "Limuzína/Sedan"."""
    name = fold(text or "")
    if not name:
        return None
    return next((code for word, code in BODIES.items() if word in name), "other")


def drive(text: str | None) -> str | None:
    """ "Přední pohon", "Zadní pohon", "Pohon všech kol"."""
    name = fold(text or "")
    if "vsech" in name or "4x4" in name:
        return "4x4"
    if "predni" in name:
        return "front"
    return "rear" if "zadni" in name else None


def car_id(ids: dict) -> str:
    """The id in the car's page URL: dealer number and five-digit stock number."""
    return f"{ids['dealerId']}{ids['fiveDigitsVehicleId']}"


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and value else None


def _model(name: str | None) -> str | None:
    """ "VW Tayron" -> "Tayron": the detail names the model with its make."""
    name = clean_spaces(name or "")
    first, _, rest = name.partition(" ")
    return rest if rest and make_code(first) else name or None


def _place(car: ParsedCar, dealer: dict | None, places: PlaceResolver) -> None:
    dealer = dealer or {}
    car.seller_kind = "dealer"
    car.seller_name = clean_spaces(dealer.get("name") or "") or None
    car.place_text = clean_spaces(dealer.get("city") or "") or None
    if car.place_text or dealer.get("postalCode"):
        car.location = places.resolve(
            car.place_text, street=dealer.get("street"), postal_code=dealer.get("postalCode")
        )


def parse_card(item: dict, places: PlaceResolver) -> ParsedCar:
    extra = item.get("ldJsonExtra") or {}
    external_id = car_id(item["id"])
    car = ParsedCar(
        external_id=external_id,
        title=clean_spaces(item.get("modelVariant") or "") or "Auto",
        url=PAGE.format(id=external_id),
        make=make_code(item.get("brand")),
        model=clean_spaces(item.get("nameLabel") or "") or None,
        year=_year(item.get("registration")),
        mileage_km=_int(item.get("mileage")),
        price=_int(item.get("price")),
        fuel=fuel(item.get("fuelText")),
        gearbox=gearbox(item.get("transmission") or item.get("gearTypeText")),
        body=body(item.get("bodyType")),
        drive=drive(item.get("driveText")),
        power_kw=_int(item.get("powerKw")),
        color=clean_spaces(extra.get("colorTextNew") or "") or None,
        doors=_int(item.get("numberOfDoors")),
        seats=_int(item.get("seatingCapacity")),
        vin=(item.get("vin") or "").strip().upper() or None,
        images=[u + PHOTO_SIZE for u in item.get("imageUrls") or [] if u],
    )
    _place(car, item.get("dealer"), places)
    return car


def parse_record(data: dict, places: PlaceResolver) -> ParsedCar | None:
    """A car's detail record (`overview`, `technicalData`, `equipment`, ...)."""
    view, tech = data.get("overview"), data.get("technicalData") or {}
    if not view or not view.get("id"):
        return None
    external_id = car_id(view["id"])
    price, net = _int(view.get("price")), _int(view.get("netSalesPrice"))
    equipment = data.get("equipment") or {}
    car = ParsedCar(
        external_id=external_id,
        title=clean_spaces(f"{view.get('header') or ''} {view.get('subHeader') or ''}") or "Auto",
        url=PAGE.format(id=external_id),
        description=((data.get("additionalInfo") or {}).get("text") or "").strip(),
        make=make_code(view.get("brand")),
        model=_model(view.get("model")),
        year=_year(tech.get("initialRegistrationDate") or view.get("initialRegistrationDate")),
        mileage_km=_int(tech.get("mileage") or view.get("mileage")),
        price=price,
        # Only a lower net price says the VAT is deductible; an equal one tells nothing.
        vat_deductible=True if price and net and net < price else None,
        fuel=fuel(tech.get("fuelText")),
        gearbox=gearbox(tech.get("gearTypeText") or view.get("gearTypeText")),
        body=body(tech.get("bodyText")),
        drive=drive(tech.get("driveText") or view.get("driveText")),
        power_kw=_int(tech.get("powerKw") or view.get("powerKw")),
        engine_cc=_int(tech.get("motorCapacity")),
        color=clean_spaces(tech.get("colorText") or "") or None,
        doors=_int(tech.get("doors")),
        seats=_int(tech.get("seats")),
        service_book=tech.get("serviceBook"),
        vin=(tech.get("vin") or "").strip().upper() or None,
        equipment=[
            text
            for group in ("standard", "extra", "used")
            for e in equipment.get(group) or []
            if (text := clean_spaces(e.get("text") or ""))
        ],
        images=[u + PHOTO_SIZE for u in view.get("imageUrls") or [] if u],
    )
    _place(car, view.get("vehicleLocation"), places)
    if phone := view.get("dealerPhoneNumber"):
        car.contact["phone"] = phone
    return car


class DasWeltAuto(CrawlSource):
    id = SOURCE_ID
    label = "Das WeltAuto"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 100  # ~56 pages today

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.search_url)]

    @staticmethod
    def search_url(page: int) -> str:
        params = {"page": page, "pageSize": PAGE_SIZE, "sortBy": "recent", "sortAscending": "true"}
        return f"{SEARCH}?{urlencode(params)}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        data = json.loads(html)
        leads = []
        for item in (data.get("resultListPage") or {}).get("content") or []:
            car = parse_card(item, places)
            fetch_url = DETAIL.format(id=car.external_id)
            leads.append(Lead(car.external_id, car.url or "", fetch_url, car))
        total = data.get("count")
        return ListingPage(
            leads,
            has_next=page * PAGE_SIZE < (total or 0),
            none_listed=total == 0,
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        return parse_record(json.loads(html), places)


SOURCE = DasWeltAuto()
