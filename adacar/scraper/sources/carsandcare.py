"""carsandcare.cz: Cars & Care, where Business Lease (a car-leasing company of the ČSOB
Leasing group) sells its ex-fleet cars to the public (~190 cars, mostly Škoda and VW, 2–4
years old, from their first and only owner). The cars stand at Cars & Care, Peroutkova
(naproti č. 35), 158 00 Praha 5.

A Next.js site: its listing (`/list`) is drawn in the browser, but the site's sitemap
(`/dynamic-sitemap.xml`) names every car's page (`/detail/<make>-<model>-<id>`, also under
`/en/`), and those pages are rendered on the server with the car's whole record as JSON
(`__NEXT_DATA__`, `props.pageProps.carDetail`). robots.txt allows both (it forbids only the
offer for leasing clients). The sitemap is read whole on every run, so a car it no longer
names is gone; its dates change for every car each day, so they do not tell which changed.

The sitemap gives only the pages; the record has make, model, version, year, mileage,
price with and without VAT (VAT deductible), power, engine size, doors, seats, VIN, first
owner, service book and photos. Fuel, gearbox, body and colour are codes the page does not
name, so fuel, gearbox and body are read from the version ("1,5 TSI DSG").
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage
from adacar.scraper.sources.toyota import model_name

SOURCE_ID = "carsandcare"
BASE = "https://www.carsandcare.cz"
SITEMAP = BASE + "/dynamic-sitemap.xml"
AD_URL = re.compile(r"<loc>\s*(https://www\.carsandcare\.cz/detail/[a-z0-9-]+-(\d+))\s*</loc>")
SELLER = "Cars & Care (Business Lease)"
PLACE = ("Praha 5", "Peroutkova", "15800")


def fuel(version: str) -> str | None:
    """ "1,5 TSI DSG" -> petrol; Ford's "EcoBlue" is a diesel."""
    return cars.fuel_in_text(version) or ("diesel" if "ecoblue" in fold(version) else None)


def gearbox(version: str) -> str | None:
    """ "DSG", "AT8" (automatic), "MT6"."""
    found = cars.gearbox_in_text(version)
    if found is None and (m := re.search(r"\b([AM])T\d*\b", version)):
        found = "automatic" if m[1] == "A" else "manual"
    return found


def _int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and value else None


def car_detail(html: str) -> dict | None:
    script = LexborHTMLParser(html).css_first("script#__NEXT_DATA__")
    if script is None:
        return None
    data = json.loads(script.text() or "{}")
    detail = ((data.get("props") or {}).get("pageProps") or {}).get("carDetail")
    return detail if isinstance(detail, dict) and detail.get("id") else None


class CarsAndCare(CrawlSource):
    id = SOURCE_ID
    label = "Cars & Care (ČSOB Leasing)"
    kind: SourceKind = "leasing"
    nationwide = True
    max_pages = 1

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, lambda page: SITEMAP)]

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        leads = [Lead(m[2], m[1], m[1]) for m in AD_URL.finditer(html)]
        return ListingPage(leads, has_next=False)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        record = car_detail(html)
        if record is None:
            return None
        name = clean_spaces(record.get("title") or "")
        version = clean_spaces(record.get("comment") or record.get("supplement") or "")
        make = make_code(record.get("brand")) or make_code(name)
        photos = sorted(record.get("photos") or [], key=lambda p: p.get("position") or 0)
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {version}") or "Auto",
            url=lead.url,
            description=(record.get("description") or "").strip(),
            make=make,
            model=model_name(name, make),
            year=_int(record.get("year_of_manufacture")),
            mileage_km=_int(record.get("mileage")),
            price=_int(record.get("sales_price")),
            vat_deductible=True if record.get("vat_expel_flag") else None,
            fuel=fuel(version),
            gearbox=gearbox(version),
            body=cars.body_in_text(name, version),
            condition="new" if record.get("is_new") else "used",
            power_kw=_int(record.get("power")),
            engine_cc=_int(record.get("engine_stroke")),
            doors=_int(record.get("doors_number")),
            seats=_int(record.get("nr_of_places")),
            first_owner=record.get("first_owner") if record.get("nr_of_owners") else None,
            service_book=True if record.get("service_book") else None,
            vin=(record.get("VIN_code") or "").strip().upper() or None,
            seller_kind="dealer",
            seller_name=SELLER,
            place_text=PLACE[0],
            location=places.resolve(PLACE[0], street=PLACE[1], postal_code=PLACE[2]),
            images=[p["image"] for p in photos if isinstance(p.get("image"), str)],
        )
        return car


SOURCE = CarsAndCare()
