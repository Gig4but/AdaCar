"""autoklokocka.cz: Autosalon Klokočka, a Prague dealer (VW, Audi, Škoda, Kia) with ~570 used
and demonstration cars at its branches in Praha 6 - Řepy and elsewhere in Prague.

Server-rendered HTML. The used-car listing (`/nabidka-vozu?stav[]=ojete&page=<n>`, 10 cars per
page, ~58 pages) is read whole on every run; cars it no longer lists expire then. robots.txt
disallows only the e-shop, cart, payment and /ajax/ paths.

A card gives the title, price (and a price without VAT when it is deductible), fuel, mileage,
year, a photo and whether the car is used or a demonstrator. The detail page adds power,
gearbox, body, colour, doors, drive, text, equipment, all photos and the address of the branch
the car stands at; the site shows no VIN.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, has_page_link, node_value

SOURCE_ID = "klokocka"
BASE = "https://autoklokocka.cz"
LISTING = BASE + "/nabidka-vozu?stav[]=ojete"
AD_PATH = re.compile(r"^/nabidka-vozu/vuz/[a-z0-9-]+$")
YEAR_KM = re.compile(r"^((?:19|20)\d\d),\s*(.*km)$")
ADDRESS = re.compile(r"^(?P<street>.+?),\s*(?P<psc>\d{3}\s?\d{2})\s+(?P<town>.+)$")
SELLER = "Autosalon Klokočka"
CONDITIONS = {"ojete": "used", "predvadeci": "demo"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def model_name(title: str) -> str | None:
    """ "Škoda Kamiq 1,0 TSI Top Selection" -> "Kamiq"."""
    words = title.split()
    return words[1] if len(words) > 1 and make_code(words[0]) else None


def _value(text: str | None) -> str | None:
    """The site writes "---" for a value it lacks."""
    text = clean_spaces(text or "")
    return text if text.strip("-") else None


def vehicle_record(tree: LexborHTMLParser) -> dict | None:
    """The schema.org Vehicle in the page's ld+json list."""
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text() or "")
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "Vehicle":
                return node
    return None


def labelled(items: list[LexborNode]) -> dict[str, str]:
    """ "<span>Výkon:</span> 110 Kw" -> {"Výkon": "110 Kw"}."""
    found: dict[str, str] = {}
    for item in items:
        label = item.css_first("span")
        if label is None:
            continue
        name = (node_value(label) or "").rstrip(":")
        value = _value(item.text().replace(label.text(), "", 1))
        if name and value:
            found.setdefault(name, value)
    return found


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first("a.card-link[href]")
    href = link.attributes.get("href") or "" if link is not None else ""
    car_id = box.attributes.get("data-car-id")
    if link is None or not car_id or not AD_PATH.match(href):
        return None
    url = BASE + href
    title = clean_spaces(link.text())
    tag = fold(node_value(box.css_first(".card-tags .tag")) or "")
    car = ParsedCar(
        external_id=car_id,
        title=title,
        url=url,
        make=make_code(title),
        model=model_name(title),
        condition=CONDITIONS.get(tag, "used"),
        seller_kind="dealer",
        seller_name=SELLER,
    )
    prices = [node_value(p) or "" for p in box.css(".card-body p")]
    if prices and (price := cars.parse_price(prices[0])) and price[1] == "CZK":
        car.price = price[0]
    car.vat_deductible = True if any("bez DPH" in p for p in prices) else None
    for item in box.css(".card-foot li"):
        text = node_value(item) or ""
        if text.endswith("km"):
            car.mileage_km = _number(text)
        elif re.fullmatch(r"(19|20)\d\d", text):
            car.year = int(text)
        elif fuel := cars.fuel_in_text(text):
            car.fuel = fuel
    image = box.css_first(".card-head img")
    if image is not None and (src := image.attributes.get("data-lazyload")):
        car.images = [src]
    return Lead(car_id, url, url, car)


class Klokocka(CrawlSource):
    id = SOURCE_ID
    label = "Autosalon Klokočka"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 100

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"&page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("article.car[data-car-id]") if (lead := parse_card(box))]
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        record = vehicle_record(tree)
        title = clean_spaces(node_value(tree.css_first(".car-detail h1")) or "")
        if record is None or not title:
            return None
        info = labelled(tree.css(".car-detail-overview li"))
        info.update(labelled(tree.css(".car-detail-table-list li")))
        offer = record.get("offers") or {}
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            make=make_code((record.get("brand") or {}).get("name")) or make_code(title),
            model=clean_spaces(record.get("model") or "") or model_name(title),
            price=_number(str(offer.get("price") or ""))
            if offer.get("priceCurrency") == "CZK"
            else None,
            fuel=cars.fuel_in_text(info.get("Pohon")),
            # "Automatická" alone: the rules want "automat" or "automatická převodovka".
            gearbox=cars.gearbox_in_text(f"{info.get('Převodovka') or ''} převodovka"),
            power_kw=cars.power_in_text(info.get("Výkon")),
            color=info.get("Barva"),
            doors=_number(info.get("Počet dveří")),
            drive="4x4" if fold(info.get("Pohon kol") or "") == "4x4" else None,
            vat_deductible=True if fold(info.get("Odpočet DPH") or "") == "ano" else None,
            seller_kind="dealer",
            seller_name=SELLER,
            images=[i for i in record.get("image") or [] if isinstance(i, str)],
        )
        if body := info.get("Karoserie") or info.get("Karosérie"):
            car.body = cars.body_in_text(body) or "other"
        if m := YEAR_KM.match(node_value(tree.css_first(".car-detail h1 + p")) or ""):
            car.year, car.mileage_km = int(m[1]), _number(m[2])
        texts = tree.css(".car-detail-body-content > p:not(.text-xs)")
        car.description = "\n".join(t for p in texts if (t := node_text(p)))
        car.equipment = list(
            dict.fromkeys(t for li in tree.css(".car-detail-checklist li") if (t := node_value(li)))
        )
        place = tree.css_first("article.place .card-body p.text-bold")
        if m := ADDRESS.match(node_value(place) or ""):
            car.place_text = m["town"]
            # "Praha 6 - Řepy": the resolver reads city parts written "Praha 6-Řepy".
            car.location = places.resolve(
                re.sub(r"\s+-\s+", "-", m["town"]), street=m["street"], postal_code=m["psc"]
            )
        return car


SOURCE = Klokocka()
