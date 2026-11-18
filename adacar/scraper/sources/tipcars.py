"""tipcars.com: a Czech portal of car dealers' stock (~53 000 passenger cars), HTML only.

robots.txt disallows the JSON API the pages call (`/*/api/`), and only the API sorts or
filters by region, so the one nationwide listing is read as the site shows it: the most
recently changed ads first (`/ojete?str=<page>-20`), until the first page with nothing new.
Its pages are large (~1 MB for 20 cars): a first run reads at most `max_pages` of them. An ad
unseen for a while has its page checked (404 when gone).

A card carries the car's numbers in a JSON attribute (`data-measure-data-value`: year,
mileage, power, volume, price), the seller and town, and the price without VAT for dealers
who can deduct it. The detail page adds the schema.org Product (seller's address, photos),
the table of facts, the equipment and the seller's note.
"""

import json
import re
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "tipcars"
BASE = "https://www.tipcars.com"
FIRST_PAGE = BASE + "/ojete"
PAGE_SIZE = 20
# /<make-model>/<body>/<fuel>/<slug>-<id>.html
AD_PATH = re.compile(r"^/[^/]+/([^/]+)/([^/]+)/[^/]*?-?(\d+)\.html$")
VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
CONDITIONS = {"usedcondition": "used", "newcondition": "new", "damagedcondition": "damaged"}


def _int(value: Any) -> int | None:
    try:
        return int(value) or None
    except (TypeError, ValueError):
        return None


def _digits(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def gearbox(text: str | None) -> str | None:
    """ "aut. převodovka", "manuální převodovka"."""
    folded = fold(text or "")
    if folded.startswith("aut") or "automat" in folded:
        return "automatic"
    return "manual" if folded.startswith("manu") else None


def model_name(title: str, make: str | None) -> str | None:
    """ "BMW Řada 6" -> "Řada 6", "Mercedes-Benz Třídy E" -> "Třídy E"."""
    name = MAKES.get(make or "", {}).get("cs") or ""
    rest = title[len(name) :] if name and fold(title).startswith(fold(name)) else ""
    return clean_spaces(rest) or None


def attr_json(node: LexborNode | None, name: str) -> Any:
    """The JSON in a `data-*-<name>-value` attribute (the page's Stimulus controllers)."""
    if node is None:
        return None
    raw = next((v for k, v in node.attributes.items() if k.endswith(f"-{name}-value")), None)
    try:
        return json.loads(raw or "null")
    except ValueError:
        return None


def measure_data(node: LexborNode | None) -> dict:
    """The car's numbers the page reports to its statistics: ["advertise", {...}, hash]."""
    data = attr_json(node, "data")
    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict):
        return data[1]
    return {}


def add_numbers(car: ParsedCar, data: dict) -> None:
    car.year = car.year or _int(data.get("made_year"))
    car.mileage_km = car.mileage_km or _int(data.get("odometer"))
    car.power_kw = car.power_kw or _int(data.get("engine_power"))
    if (volume := _int(data.get("engine_capacity"))) and volume >= 50:  # electric cars: 1
        car.engine_cc = car.engine_cc or volume
    car.doors = car.doors or _int(data.get("doors"))
    car.seats = car.seats or _int(data.get("seats"))


def set_price(car: ParsedCar, text: str | None) -> None:
    price = cars.parse_price(text)
    if price and price[1] == "CZK":
        car.price = price[0]


class Tipcars(CrawlSource):
    id = SOURCE_ID
    label = "TipCars"
    newest_first = True
    nationwide = True
    max_pages = 50  # the first run reads the 1 000 most recently changed ads
    max_details = 150
    recheck_after_days = 14

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_of)]

    @staticmethod
    def listing_url_of(page: int) -> str:
        return FIRST_PAGE if page == 1 else f"{FIRST_PAGE}?str={page}-{PAGE_SIZE}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("div.advertisement[data-measure-data-value]"):
            link = card.css_first(".advertisement-name__title a[href]")
            href = link.attributes.get("href") or "" if link is not None else ""
            m = AD_PATH.match(href)
            if link is None or m is None:
                continue
            url = urljoin(BASE, href)
            name = node_value(link) or ""
            subtitle = node_value(card.css_first(".advertisement-name__title p"))
            make = make_code(name)
            car = ParsedCar(
                external_id=m[3],
                title=clean_spaces(f"{name} {subtitle or ''}"),
                url=url,
                make=make,
                model=model_name(name, make),
                body=cars.body_in_text(m[1].replace("-", " ")) or "other",
                fuel=cars.fuel_in_text(m[2].replace("-", " ")),
            )
            add_numbers(car, measure_data(card))
            price_box = card.css_first(".advertisement-name__price")
            set_price(car, node_value(price_box.css_first("h3")) if price_box else None)
            if price_box is not None and "bez dph" in fold(price_box.text()):
                car.vat_deductible = True
            # Small cards list year, mileage, power and fuel; large ones the equipment, the
            # gearbox among it.
            shown = fold(clean_spaces(card.text(separator=" ")))
            if "aut. prevodovka" in shown:
                car.gearbox = "automatic"
            elif "manualni prevodovka" in shown:
                car.gearbox = "manual"
            else:
                car.gearbox = cars.gearbox_in_text(car.title)
            self.add_seller(car, card, places)
            image = card.css_first("img.graphics-view") or card.css_first(
                ".advertisement-thumbnails img"
            )
            if image is not None and (src := image.attributes.get("src")):
                car.images = [src]
            leads.append(Lead(car.external_id, url, url, car))
        return ListingPage(leads, has_next=self.has_next(tree, page, bool(leads)))

    @staticmethod
    def add_seller(car: ParsedCar, card: LexborNode, places: PlaceResolver) -> None:
        """ "AUTOBAZAR BALANTERO s.r.o. • Uničov": the dealer and its town."""
        contact = attr_json(card.css_first("[data-contact-phone-detail-value]"), "detail")
        if not isinstance(contact, dict):
            return
        seller, dot, town = (contact.get("description") or "").rpartition("•")
        if not dot:  # no town given
            seller, town = town, ""
        car.seller_name = clean_spaces(seller) or None
        car.place_text = clean_spaces(town) or None
        car.location = places.resolve(car.place_text) if car.place_text else None
        if car.seller_name:
            car.seller_kind = "dealer"

    @staticmethod
    def has_next(tree: LexborHTMLParser, page: int, leads: bool) -> bool:
        paging = attr_json(tree.css_first("[data-offer-listing-pagination-value]"), "pagination")
        if not isinstance(paging, dict):
            return leads  # no paging data: a changed layout; read on while pages have ads
        return bool(paging.get("hasNext"))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        product = next(
            (x for x in _json_ld(tree) if x.get("@type") == "Product" and x.get("offers")), None
        )
        if product is None:
            return None
        offer = product["offers"]
        brand = clean_spaces((product.get("brand") or {}).get("name") or "")
        facts = {
            clean_spaces(label.text()).rstrip(":"): clean_spaces(
                node_text(box).removeprefix(clean_spaces(label.text()))
            )
            for box in tree.css(".detail-box-S")
            if (label := box.css_first(".detail-box__info-icon")) is not None
        }
        top = {}
        for node in tree.css(".detail-info__box-col__text"):
            label, _, value = node_text(node).partition("\n")
            top[label.rstrip(":")] = clean_spaces(value)
        note = tree.css_first(".detail-note")
        price_box = tree.css_first(".detail-layout__price") or tree.css_first("[class*=price]")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(product.get("name") or "") or brand or "Auto",
            url=lead.url,
            description=node_text(note) if note is not None else "",
            make=make_code(brand) or (lead.card.make if lead.card else None),
            model=clean_spaces(product.get("model") or "") or None,
            year=_int((re.findall(r"\d{4}", facts.get("v provozu od", "")) or [None])[0]),
            mileage_km=_digits(facts.get("tachometr")),
            fuel=cars.fuel_in_text(facts.get("palivo")),
            gearbox=gearbox(facts.get("převodovka")),
            body=(cars.body_in_text(facts.get("karoserie")) or "other")
            if facts.get("karoserie")
            else None,
            condition=CONDITIONS.get(fold(str(offer.get("itemCondition") or "")).split("/")[-1]),
            power_kw=cars.power_in_text(facts.get("výkon")),
            engine_cc=_digits(facts.get("objem")),
            doors=_digits(facts.get("počet dveří")),
            seats=_digits(facts.get("počet míst")),
            color=facts.get("barva") or None,
            equipment=[clean_spaces(li.text()) for li in tree.css(".detail-specs li")],
        )
        vin = (top.get("VIN") or "").upper()
        car.vin = vin if VIN.match(vin) else None
        if offer.get("priceCurrency") == "CZK":
            car.price = _int(offer.get("price"))
        if price_box is not None and "odpoctu dph" in fold(price_box.text()):
            car.vat_deductible = True
        if any(fold(e) == "pohon 4x4" for e in car.equipment):
            car.drive = "4x4"
        add_numbers(car, measure_data(tree.css_first("[data-measure-data-value]")))
        seller = offer.get("seller") or {}
        address = seller.get("address") or {}
        car.seller_name = clean_spaces(seller.get("name") or "") or None
        if seller.get("@type") == "Organization":
            car.seller_kind = "dealer"
        elif seller.get("@type") == "Person":
            car.seller_kind = "private"
        town, psc = address.get("addressLocality"), address.get("postalCode")
        car.place_text = clean_spaces(town or "") or None
        if town or psc:
            car.location = places.resolve(
                town, postal_code=psc, street=address.get("streetAddress")
            )
        images = product.get("image") or []
        car.images = [i["url"] for i in images if isinstance(i, dict) and i.get("url")]
        return car


def _json_ld(tree: LexborHTMLParser) -> list[dict]:
    found = []
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except ValueError:
            continue
        found.extend(x for x in (data if isinstance(data, list) else [data]) if isinstance(x, dict))
    return found


SOURCE = Tipcars()
