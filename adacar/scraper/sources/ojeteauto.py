"""ojeteauto.cz: used-car ads of private sellers and small dealers (~8 400 cars), HTML only.

One nationwide listing (`/inzeraty/?paginator-page=<n>`, 20 ads a page after three promoted
ones), newest first by ad number, read until the first page with nothing new; a first run
reads them all. robots.txt disallows the sorted and filtered listings and
the photo pages, not these. An ad unseen for a while has its page checked (404 when gone).

A card gives the title, year, mileage, price and fuel. The detail page adds a schema.org Car
(make, model, body, seller), the table of facts, the equipment, the free text and the
seller's address; many ads are placed by an agent for private sellers, with the district
only in the text ("OKR: LIBEREC").
"""

import json
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, clean_title, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "ojeteauto"
BASE = "https://www.ojeteauto.cz"
LISTING = BASE + "/inzeraty/"
AD_PATH = re.compile(r"^/inzeraty/[^/?]+-(\d+)/$")
DISTRICT = re.compile(r"^OKR:\s*(.+)$", re.M)
CARD_FACT = re.compile(r"(Rok|Najeto|Palivo):\s*([^\n]+)")


def _digits(text: str | None) -> int | None:
    """The first number: "117 000 km" -> 117000, "1598 cm3" -> 1598."""
    m = re.search(r"\d(?:[\d \xa0]*\d)?", text or "")
    return int(re.sub(r"\D", "", m[0])) if m else None


def _year(text: str | None) -> int | None:
    m = re.search(r"(19|20)\d{2}", text or "")
    return int(m[0]) if m else None


def set_price(car: ParsedCar, text: str | None) -> None:
    price = cars.parse_price(text)
    if price and price[1] == "CZK":
        car.price = price[0]


def _pairs(nodes: list[LexborNode]) -> dict[str, str]:
    """Label and value cells that follow each other ("Rok:", "2021")."""
    found: dict[str, str] = {}
    label = None
    for node in nodes:
        classes = node.attributes.get("class") or ""
        text = clean_spaces(node.text())
        if "label-col" in classes:
            label = text.rstrip(":").strip()
        elif "value-col" in classes and label:
            found.setdefault(label, text)
            label = None
    return found


class OjeteAuto(CrawlSource):
    id = SOURCE_ID
    label = "OjetéAuto.cz"
    kind: SourceKind = "classifieds"
    newest_first = True
    nationwide = True
    max_pages = 600  # the first run reads every ad (~421 pages): later runs stop early
    recheck_after_days = 14

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.page_url)]

    @staticmethod
    def page_url(page: int) -> str:
        return LISTING if page == 1 else f"{LISTING}?paginator-page={page}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for row in tree.css("div.post-preview[data-detail-url]"):
            m = AD_PATH.match(row.attributes.get("data-detail-url") or "")
            link = row.css_first("h3.post-title a")
            if m is None or link is None:
                continue
            url = urljoin(BASE, m[0])
            title = clean_title(link.attributes.get("title") or node_value(link))
            facts = dict(CARD_FACT.findall(node_text(row)))
            make = make_code(title)
            car = ParsedCar(
                external_id=m[1],
                title=title,
                url=url,
                make=make,
                year=_year(facts.get("Rok")),
                mileage_km=_digits(facts.get("Najeto")),
                fuel=cars.fuel_in_text(title, facts.get("Palivo")),
                power_kw=cars.power_in_text(title),
                gearbox=cars.gearbox_in_text(title),
            )
            set_price(car, node_value(row.css_first(".post-cena .price")))
            image = row.css_first(".post-img img")
            if image is not None and (src := image.attributes.get("src")):
                car.images = [src]
            leads.append(Lead(m[1], url, url, car))
        has_next = tree.css_first("a.strankovani-next") is not None
        return ListingPage(leads, has_next=has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        item = next((x for x in _json_ld(tree) if "Car" in _types(x)), None)
        if item is None:
            return None
        params = _pairs(tree.css(".post-params .label-col, .post-params .value-col"))
        seller = {
            clean_spaces(label.text()).rstrip(":"): clean_spaces(value.text())
            for box in tree.css(".seller-info")
            if (label := box.css_first(".label-col")) and (value := box.css_first(".value-col"))
        }
        other = tree.css_first(".other-info")
        text = node_text(other) if other is not None else ""
        lines = {fold(line) for line in text.splitlines()}
        equipment = [clean_spaces(li.text()) for li in tree.css("ul.extras li")]
        offer = item.get("offers") or {}
        brand = clean_spaces((item.get("brand") or {}).get("name") or "")
        mileage = item.get("mileageFromOdometer") or {}
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_title(item.get("name")) or brand or "Auto",
            url=lead.url,
            description=text,
            make=make_code(brand) or (lead.card.make if lead.card else None),
            model=clean_spaces(item.get("model") or "") or None,
            year=_year(str(item.get("vehicleModelDate") or "")) or _year(params.get("Rok")),
            mileage_km=_digits(str(mileage.get("value") or "")) or _digits(params.get("Tachometr")),
            fuel=cars.fuel_in_text(item.get("name"), item.get("fuelType") or params.get("Palivo")),
            gearbox=_gearbox(equipment) or cars.gearbox_in_text(item.get("name"), text),
            body=_body(item.get("bodyType") or params.get("Karoserie")),
            power_kw=cars.power_in_text(params.get("Výkon"), item.get("name")),
            engine_cc=_digits(params.get("Objem")),
            doors=_digits(params.get("Počet dveří")),
            seats=_digits(params.get("Počet sedadel")),
            color=params.get("Barva") or None,
            equipment=equipment,
            seller_name=seller.get("Firma") or seller.get("Jméno") or None,
        )
        if offer.get("priceCurrency") == "CZK":
            car.price = _digits(str(offer.get("price") or "")) or None
        else:
            set_price(car, params.get("Cena (s DPH)") or params.get("V hotovosti"))
        if "prvni majitel" in lines:
            car.first_owner = True
        elif any(line.endswith("majitel") for line in lines):
            car.first_owner = False
        if "servisni knizka" in lines:
            car.service_book = True
        if "nehavarovane" in lines:
            car.crashed = False
        if m := re.search(r"^Koupeno v:\s*(.+)$", text, re.M):
            car.origin_country = m[1].strip()
        street, town = seller.get("Ulice", "").strip(". "), seller.get("Město", "").strip(". ")
        if town:
            car.seller_kind = "dealer"  # a business address; agents' ads have none
            car.place_text = town
            car.location = places.resolve(town, street=street or None)
        elif m := DISTRICT.search(text):
            car.place_text = m[1].strip().title()
            car.location = places.resolve(car.place_text)
        photos = [img.attributes.get("src") for img in tree.css(".post-imgs img")]
        car.images = list(dict.fromkeys(p for p in photos if p))
        return car


def _gearbox(equipment: list[str]) -> str | None:
    """ "aut. převodovka", "manuální převodovka"."""
    for item in map(fold, equipment):
        if item.startswith("aut") and "prevodovk" in item:
            return "automatic"
        if item.startswith("manu"):
            return "manual"
    return None


def _body(text: str | None) -> str | None:
    return (cars.body_in_text(text) or "other") if text else None


def _types(item: dict) -> list[str]:
    kind = item.get("@type")
    return kind if isinstance(kind, list) else [kind] if isinstance(kind, str) else []


def _json_ld(tree: LexborHTMLParser) -> list[dict]:
    found = []
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except ValueError:
            continue
        found.extend(x for x in (data if isinstance(data, list) else [data]) if isinstance(x, dict))
    return found


SOURCE = OjeteAuto()
