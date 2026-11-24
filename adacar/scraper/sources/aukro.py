"""aukro.cz: Aukro, an auction and classifieds marketplace, the "Osobní automobily" category
(~550 offers, mostly private sellers, also dealers from Czechia and Slovakia).

The pages are Angular, rendered on the server with their data as JSON in
`<script id="ng-state">` (like aaaauto.cz), so nothing is read from the markup. robots.txt
lets generic crawlers read the category and the offers. The listing
(`/osobni-automobily?page=<n>`, 60 offers per page, ~10 pages, promoted offers first) is read
whole on every run, so a complete pass tells which offers ended.

An offer is an auction, a fixed price ("Kup teď"), or an auction that can also be bought
at once. The price is the fixed price; an auction without one has no price (its bid, often
1 Kč, rises until the end, and a page read once is not read again for it); it ends at
`expires_at`. Offers of
Slovak sellers are skipped.
A listed offer gives the title, price, the place and postcode, whether the seller is a
company, a photo and, when the seller filled them, year, mileage, fuel, gearbox, body,
power, engine size, drive, colour, doors, VIN and equipment. The offer's page adds the
seller's text (which the rules in normalize/cars.py read for what the form leaves out) and
the photos.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, clean_title, fold, html_to_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, merge_card
from adacar.scraper.dedup import SERIES
from adacar.scraper.sources.bazar_cz import body as form_body
from adacar.scraper.sources.bazar_cz import gearbox as form_gearbox
from adacar.scraper.sources.bazos import MAKE_WORDS

SOURCE_ID = "aukro"
BASE = "https://aukro.cz"
LISTING = BASE + "/osobni-automobily"
NG_STATE = re.compile(r'<script id="ng-state" type="application/json">(.*?)</script>', re.S)
VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
VIN_IN_TEXT = re.compile(r"\bVIN\b\W{0,5}([A-HJ-NPR-Z0-9]{17})\b")
ENGINE = re.compile(r"\d+[.,]\d")
DRIVES = {"4 × 4": "4x4", "4x4": "4x4", "Náhon na přední kola": "front",
          "Náhon na zadní kola": "rear"}  # fmt: skip
EQUIPMENT = "Další informace"
CONDITIONS = {"nefunkcni / na dily": "damaged", "poskozeno": "damaged", "nove": "new"}


def ng_state(html: str) -> dict[str, Any]:
    m = NG_STATE.search(html)
    try:
        state = json.loads(m[1]) if m else {}
    except ValueError:
        return {}
    cache = state.get("aukCache") if isinstance(state, dict) else None
    return cache if isinstance(cache, dict) else {}


def cached(state: dict[str, Any], marker: str) -> dict[str, Any] | None:
    """The body of the page's API answer whose request contains `marker`."""
    for key, value in state.items():
        if marker in key and isinstance(value, dict) and isinstance(value.get("b"), dict):
            return value["b"]
    return None


def _int(value: Any) -> int | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) or None if digits else None


def _amount(price: Any) -> int | None:
    if isinstance(price, dict) and price.get("currency") == "CZK":
        amount = _int(price.get("amount"))
        return amount if amount and amount >= cars.MIN_PRICE else None
    return None


def _utc(text: Any) -> datetime | None:
    """ "2026-11-30T20:20:26+01:00" -> naive UTC."""
    try:
        return datetime.fromisoformat(text).astimezone(UTC).replace(tzinfo=None) if text else None
    except (TypeError, ValueError):
        return None


def attributes(item: dict[str, Any]) -> dict[str, str]:
    """The form's values by name; ranges ("Rok výroby": "1990 až 2000") come after exact ones
    of the same name and are skipped. The equipment ("Další informace") is one value in a
    listing, one per item on the offer's page: joined with commas."""
    found: dict[str, str] = {}
    for a in item.get("attributes") or []:
        name, value = a.get("attributeName"), clean_spaces(str(a.get("attributeValue") or ""))
        if not name or not value:
            continue
        if name == EQUIPMENT and name in found:
            found[name] += ", " + value
        elif name not in found:
            found[name] = value
    return found


def model_name(title: str, make: str | None = None) -> str | None:
    """The word after the make's name: "Peugeot 308 1.6 HDI" -> "308", "7místná Toyota
    Proace City" -> "Proace", "Hyundai Genesis" -> "Genesis"; None when the title does not
    name the make."""
    words = re.findall(r"[^\W_][\w.-]*", title)

    def of_make(word: str) -> bool:
        named = make_code(word)
        return (fold(word) in MAKE_WORDS or named is not None) and named in (None, make)

    for i, word in enumerate(words):
        if of_make(word):
            rest = [w for w in words[i + 1 :] if not of_make(w) and not ENGINE.match(w)]
            if len(rest) > 1 and fold(rest[0]) in SERIES:
                return " ".join(rest[:2])
            return rest[0] if rest else None
    return None


def make_of(item: dict[str, Any], title: str) -> str | None:
    """From the title, else the category under "Osobní automobily" (the make)."""
    if make := make_code(title):
        return make
    path = item.get("categoryPath") or item.get("category") or []
    return make_code(path[-1].get("name") or "") if len(path) > 3 else None


def parse_item(item: dict[str, Any], places: PlaceResolver) -> ParsedCar | None:
    """A listed offer, or the offer's own page (the same fields, partly named otherwise)."""
    item_id = item.get("itemId") or item.get("id")
    slug = item.get("seoUrl")
    if not item_id or not slug:
        return None
    title = clean_title(item.get("itemName") or item.get("name"))
    form = attributes(item)
    make = make_of(item, title)
    car = ParsedCar(
        external_id=str(item_id),
        title=title or "Auto",
        url=f"{BASE}/{slug}-{item_id}",
        make=make,
        year=_year(form.get("Rok výroby")),
        mileage_km=_int(form.get("Najeto [km]")),
        fuel=cars.fuel_in_text(form.get("Pohon")),
        gearbox=form_gearbox(form.get("Převodovka")),
        body=_body(form.get("Karoserie")),
        drive=DRIVES.get(form.get("Náhon", "")),
        condition=CONDITIONS.get(fold(form.get("Stav zboží", "")), "used"),
        power_kw=_int(form.get("Výkon motoru [kW]")),
        engine_cc=_int(form.get("Objem motoru [cm3]")),
        color=form.get("Barva") or None,
        doors=_int(form.get("Počet dveří")),
        equipment=[e for x in form.get(EQUIPMENT, "").split(",") if (e := x.strip())],
        expires_at=_utc(item.get("endingTime")),
    )
    if car.engine_cc and car.engine_cc < 50:  # "3" for a 3-litre engine
        car.engine_cc = None
    vin = form.get("VIN", "").upper()
    car.vin = vin if VIN.match(vin) else None
    car.price = _amount(item.get("buyNowPrice")) if item.get("buyNowActive") else None
    if item.get("auction") or item.get("itemType") == "BIDDING":
        car.extra["auction"] = True
    elif car.price is None:
        car.price = _amount(item.get("price"))
    seller = item.get("seller") or {}
    if seller.get("registrationDomain") not in (None, "CZ"):
        car.extra["abroad"] = True
    car.seller_kind = "dealer" if seller.get("companyAccount") else "private"
    car.seller_name = clean_spaces(item.get("sellerLogin") or seller.get("showName") or "") or None
    town = item.get("location") or (item.get("itemLocation") or "").partition(",")[0]
    postcode = item.get("postcode") or item.get("postCode")
    car.place_text = clean_spaces(town or "") or None
    if car.place_text or postcode:
        car.location = places.resolve(car.place_text, postal_code=postcode)
    if image := item.get("titleImageUrl"):
        car.images = [image]
    return car


def _year(text: str | None) -> int | None:
    year = _int(text)
    return year if year and cars.MIN_YEAR <= year <= 2100 else None


def _body(text: str | None) -> str | None:
    if not text:
        return None
    return "pickup" if fold(text).startswith("pick") else form_body(text) or "other"


def add_text_facts(car: ParsedCar) -> None:
    """What the form leaves out, from the title and the seller's text."""
    title, text = car.title, car.description
    car.model = car.model or (model_name(title, car.make) if car.make else None)
    car.year = car.year or cars.year_in_text(title, text, car.make)
    car.mileage_km = car.mileage_km or cars.km_in_text(title, text)
    car.power_kw = car.power_kw or cars.power_in_text(title, text)
    car.fuel = car.fuel or cars.fuel_in_text(title, text)
    car.gearbox = car.gearbox or cars.gearbox_in_text(title, text)
    car.body = car.body or cars.body_in_text(title, text)
    if car.vin is None and (vin := VIN_IN_TEXT.search(text)):
        car.vin = vin[1]
    if reason := cars.not_offer_reason(title):
        car.extra["not_offer"] = reason


class Aukro(CrawlSource):
    id = SOURCE_ID
    label = "Aukro – osobní auta"
    kind: SourceKind = "classifieds"
    nationwide = True
    max_pages = 30  # the whole category, ~10 pages

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        return not (lead.card and lead.card.extra.get("abroad"))

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        found = cached(ng_state(html), "/offers/searchItemsCommon")
        if found is None:
            return ListingPage([], has_next=False)
        leads = []
        for item in found.get("content") or []:
            car = parse_item(item, places) if isinstance(item, dict) else None
            if car is not None and car.url:
                add_text_facts(car)
                leads.append(Lead(car.external_id, car.url, car.url, car))
        paging = found.get("page") or {}
        has_next = (paging.get("number") or 0) + 1 < (paging.get("totalPages") or 0)
        return ListingPage(leads, has_next, none_listed=paging.get("totalElements") == 0)

    def gone(self, html: str) -> bool:
        offer = cached(ng_state(html), "/offerDetail")
        return offer is not None and (
            offer.get("state") != "ACTIVE" or bool(offer.get("itemArchived"))
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        offer = cached(ng_state(html), "/offerDetail")
        car = parse_item(offer, places) if offer else None
        if offer is None or car is None:
            return None
        car.description = html_to_text(offer.get("descriptionInHtml")) or clean_spaces(
            offer.get("descriptionStripped") or ""
        )
        photos = (
            ((image.get("sizes") or {}).get("ORIGINAL") or {}).get("url")
            for image in offer.get("itemImages") or []
            if isinstance(image, dict)
        )
        car.images = [p for p in photos if p] or car.images
        # The page's form is often shorter than the listed offer's: the form beats the text.
        merge_card(car, lead.card)
        add_text_facts(car)
        if self.gone(html):
            car.extra["not_offer"] = "sold"
        return car


SOURCE = Aukro()
