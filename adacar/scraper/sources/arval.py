"""arvalauto.cz: Arval, a car-leasing company, sells its ex-fleet cars to the public (~500
cars for sale, mostly 2–5 years old). A buyer pays an admin fee of 8 000 Kč on top of the
price.

Server-rendered HTML. One nationwide listing of the cars for sale with prices incl. VAT
(`/nabidka-vozu/prodej/ojeta-vozidla/cena-s-dph/?pg=<n>`, 18 cars per page, ~29 pages), read
to its end on every run: its order is kept in the session, not the URL. A page past the end
redirects to the last one. The cars Arval offers on lease (`/cz/operativni-leasing/...`) are
not read. robots.txt forbids only rating, discussion and order pages.

A card gives the make and model, the version, mileage, the month it went into service,
price, fuel, gearbox, power and a photo. The detail page (`/cz/prodej/ojeta-vozidla/<make>/
<slug>/<id>/cena-s-dph/`) has a schema.org Car record (VIN, year, colour, body, drive,
photos) and adds the engine size, equipment, whether the VAT is deductible and the
commission centre the car stands at (e.g. GlobalAuto, K Austisu 680, Praha 5).
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.mototechna import car_record
from adacar.scraper.sources.toyota import gearbox, model_name

SOURCE_ID = "arval"
BASE = "https://www.arvalauto.cz"
LISTING = BASE + "/nabidka-vozu/prodej/ojeta-vozidla/cena-s-dph/"
AD_PATH = re.compile(r"^/cz/prodej/ojeta-vozidla/[^/]+/[^/]+/(\d+)/cena-s-dph/$")
PSC_TOWN = re.compile(r"^(\d{3}\s?\d{2})\s+(.+)$")
SELLER = "Arval"
DRIVES = {"fourwheel": "4x4", "allwheel": "4x4", "front": "front", "rear": "rear"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def fuel(stated: str | None, version: str) -> str | None:
    """The fuel names only the petrol or diesel engine of a hybrid ("x3008-phev")."""
    named = cars.fuel_in_text(version)
    if named in ("hybrid", "plugin_hybrid"):
        return named
    return cars.fuel_in_text(stated) or named


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first("a[data-offer-button]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if m is None:
        return None
    url = urljoin(BASE, m[0])
    name = node_value(box.css_first(".title-info h2")) or ""
    version = node_value(box.css_first(".model-info")) or ""
    make = make_code(name)
    car = ParsedCar(
        external_id=m[1],
        title=clean_spaces(f"{name} {version}") or "Auto",
        url=url,
        make=make,
        model=model_name(name, make),
        price=_number(node_value(box.css_first(".price p.price"))),
        seller_kind="dealer",
        seller_name=SELLER,
    )
    for item in box.css(".title-info > .info > div"):
        text = node_value(item) or ""
        if text.endswith("km"):
            car.mileage_km = _number(text)
        elif year := re.search(r"(19|20)\d\d", text):
            car.year = int(year[0])
    for item in box.css(".info2 > div"):
        text = node_value(item) or ""
        if "kW" in text:
            car.power_kw = cars.power_in_text(text)
        elif "," in text:
            stated, _, box_text = text.partition(",")
            car.fuel = fuel(stated, url)
            car.gearbox = gearbox(box_text.replace("Převodovka", "").strip())
    image = box.css_first(".photo img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [urljoin(BASE, src)]
    return Lead(m[1], url, url, car)


def _location(car: ParsedCar, tree: LexborHTMLParser, places: PlaceResolver) -> None:
    lines = [node_value(p) or "" for p in tree.css(".tour-seller .__location-block p")]
    town = PSC_TOWN.match(lines[-1]) if lines else None
    if town:
        car.place_text = town[2]
        street = lines[0] if len(lines) > 1 else None
        car.location = places.resolve(town[2], street=street, postal_code=town[1])


class Arval(CrawlSource):
    id = SOURCE_ID
    label = "Arval"
    kind: SourceKind = "leasing"
    nationwide = True
    max_pages = 60  # ~29 today

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"?pg={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [
            lead
            for box in tree.css("#car-container .content-container")
            if (lead := parse_card(box))
        ]
        has_next = tree.css_first("a.productPaginationNextButton") is not None
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        record = car_record(tree)
        if record is None:
            return None
        brand = clean_spaces((record.get("brand") or {}).get("name") or "")
        model = clean_spaces(record.get("model") or "") or None
        version = clean_spaces(record.get("vehicleConfiguration") or "")
        offer = record.get("offers") or {}
        specs = [node_value(li) or "" for li in tree.css(".--slide-technicke-specifikace li")]
        body_text = record.get("bodyType")
        drive_text = fold(record.get("driveWheelConfiguration") or "").replace("schema.org/", "")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{brand} {model or ''} {version}") or "Auto",
            url=lead.url,
            make=make_code(brand),
            model=model,
            year=_number(str(record.get("vehicleModelDate") or "")),
            mileage_km=_number(str((record.get("mileageFromOdometer") or {}).get("value") or "")),
            price=_number(str(offer.get("price") or ""))
            if offer.get("priceCurrency") == "CZK"
            else None,
            vat_deductible=True
            if "odpoctu dph" in fold(node_value(tree.css_first(".price-vat-deduction")) or "")
            else None,
            fuel=fuel((record.get("vehicleEngine") or {}).get("fuelType"), f"{version} {lead.url}"),
            gearbox=gearbox(record.get("vehicleTransmission")),
            body=cars.body_in_text(body_text) or ("other" if body_text else None),
            drive=next((code for word, code in DRIVES.items() if word in drive_text), None),
            condition="used",
            power_kw=cars.power_in_text(*specs),
            engine_cc=next((_number(s) for s in specs if s.endswith("ccm")), None),
            color=clean_spaces(record.get("color") or "") or None,
            doors=_number(str(record.get("numberOfDoors") or "")),
            vin=(record.get("vehicleIdentificationNumber") or "").strip().upper() or None,
            seller_kind="dealer",
            seller_name=SELLER,
            equipment=[
                text
                for slide in ("interier", "exterier", "dalsi-vybava")
                for li in tree.css(f".--slide-{slide} li")
                if (text := node_value(li))
            ],
            images=[i for i in record.get("image") or [] if isinstance(i, str)],
        )
        _location(car, tree, places)
        return car


SOURCE = Arval()
