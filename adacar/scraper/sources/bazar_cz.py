"""www.bazar.cz: general classifieds (mostly private sellers), the "Osobní auta" category, HTML.

One nationwide listing, newest first (`/osobni-auta/<page>/`, 23 per page, ~6 000 ads); a
few paid TOP ads come first on page 1 only. The walk stops at the first page with no new ad;
a first run reads them all. Ads unseen for a while are checked on their own
page, which answers 404 once the ad is deleted.

The sellers fill a form, so cards already give year, mileage, gearbox, power, fuel, place,
price and whether the seller is private or commercial; the detail page adds the make, model,
body, colour, VIN, engine size, equipment and the whole text. What the form leaves empty is
read from the text with the rules in normalize/cars.py; buyers and parts sellers are kept but
marked like on bazos.
"""

import re
from datetime import datetime, time

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ParsedLocation, ScrapeQuery, SellerKind, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, clean_title, czech_date, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.bazos import add_facts, set_price

SOURCE_ID = "bazar_cz"
BASE = "https://www.bazar.cz"
AD_URL = re.compile(r"^https://www\.bazar\.cz/xad(\d+)-[^/]*\.aspx$")
# The last breadcrumb of a detail page (JSON-LD) is the make: {"name": "Volkswagen"}.
BREADCRUMB_MAKE = re.compile(r'"position"\s*:\s*4,\s*"item"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"')


def number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def gearbox(value: str | None) -> str | None:
    """ "manuální", "automatická", "poloautomatická"."""
    folded = fold(value or "")
    if folded.startswith("manual"):
        return "manual"
    return "automatic" if "automat" in folded else None


def fuel(value: str | None) -> str | None:
    """ "nafta", "benzín", "LPG + benzín", "elektřina"."""
    if "elektr" in fold(value or ""):
        return "electric"
    return cars.fuel_in_text(value)


def body(value: str | None) -> str | None:
    return "offroad" if "teren" in fold(value or "") else cars.body_in_text(value)


def location(places: PlaceResolver, text: str | None) -> ParsedLocation | None:
    """ "Bílina, Teplice": the town, then its district; a card shows only one of them."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts:
        return None
    return places.resolve(parts[0], district=parts[1] if len(parts) > 1 else None)


def year(text: str | None) -> int | None:
    value = number(text)
    return value if value and cars.MIN_YEAR <= value <= 2100 else None


def mileage(text: str | None) -> int | None:
    value = number(text)
    return value if value and value <= cars.MAX_KM else None


class BazarCz(CrawlSource):
    id = SOURCE_ID
    label = "Bazar.cz – osobní auta"
    kind: SourceKind = "classifieds"
    newest_first = True
    nationwide = True
    max_pages = 400  # the first run reads every ad (~261 pages): later runs stop early
    recheck_after_days = 14
    refresh_after_days = None  # private ads rarely change

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return f"{BASE}/osobni-auta/" + (f"{page}/" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for item in tree.css("div.sale-item[data-id]"):
            link = item.css_first("a[href]")
            m = AD_URL.match(link.attributes.get("href") or "") if link is not None else None
            if m is None:
                continue
            url = m[0]
            place = node_value(item.css_first(".location"))
            car = ParsedCar(
                external_id=m[1],
                title=clean_title(node_value(item.css_first("h2.title"))),
                url=url,
                place_text=place,
                location=location(places, place),
            )
            set_price(car, node_value(item.css_first(".price")))
            add_facts(car, None)
            if car.make is None:
                car.model = None  # "Prodej osobního vozu": no make, so no model either
            # <li class="najeto"><div class="attrib-label">Najeto</div>230 000 km</li>
            attrs = {
                li.attributes.get("class") or "": li.text(deep=False)
                for li in item.css("ul.auta-attribs-grid li")
            }
            car.year = year(attrs.get("vyroba")) or car.year
            car.mileage_km = mileage(attrs.get("najeto")) or car.mileage_km
            car.gearbox = gearbox(attrs.get("razeni")) or car.gearbox
            car.fuel = fuel(attrs.get("palivo")) or car.fuel
            car.power_kw = cars.power_in_text(attrs.get("vykon")) or car.power_kw
            car.seller_kind = seller_kind(item)
            image = item.css_first("img.img")
            if image is not None and (src := image.attributes.get("src")):
                car.images = [src]
            leads.append(Lead(m[1], url, url, car))
        next_url = self.listing_page(page + 1)
        has_next = any(a.attributes.get("href") == next_url for a in tree.css("a[href]"))
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first("#detail-main h1")))
        text = tree.css_first("#ad_description p.text-justify")
        if not title:
            return None
        info: dict[str, LexborNode] = {}
        for row in tree.css("table.ad_attribs tr"):
            th, td = row.css_first("th"), row.css_first("td")
            if th is not None and td is not None:
                info.setdefault(clean_spaces(th.text()).rstrip(":"), td)

        def value(label: str) -> str | None:
            return node_value(info.get(label))

        place = value("Místo") or node_value(tree.css_first("#detail-city"))
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text) if text is not None else "",
            place_text=place,
            location=location(places, place),
            seller_name=node_value(tree.css_first(".contact .seller")),
            color=value("Barva"),
            vin=value("VIN"),
            origin_country=value("Země původu"),
        )
        if day := czech_date(value("Vložen")):
            car.posted_at = datetime.combine(day, time())
        set_price(car, node_value(tree.css_first("#detail-price")))
        crumb = BREADCRUMB_MAKE.search(html)
        add_facts(car, make_code(crumb[1]) if crumb else None)
        car.model = value("Model") or (car.model if car.make else None)
        car.year = year(value("Rok výroby")) or car.year
        car.mileage_km = mileage(value("Najeto [km]")) or car.mileage_km
        car.gearbox = gearbox(value("Převodovka")) or car.gearbox
        car.fuel = fuel(value("Palivo")) or car.fuel
        car.body = body(value("Karosérie")) or car.body
        car.power_kw = number(value("Výkon [kW]")) or car.power_kw
        car.engine_cc = number(value("Objem [ccm]"))
        car.equipment = equipment(tree)
        photos = (div.attributes.get("data-src") for div in tree.css("div.detail-image-container"))
        car.images = list(dict.fromkeys(p for p in photos if p))[:20]
        return car


def seller_kind(item: LexborNode) -> SellerKind | None:
    """The card's tags: "Komerční" or "Soukromý", the other one hidden."""
    for tag in item.css("li.tag"):
        classes = (tag.attributes.get("class") or "").split()
        if "hidden" in classes:
            continue
        if "tag-commercial" in classes:
            return "dealer"
        if "tag-noncommercial" in classes:
            return "private"
    return None


def equipment(tree: LexborHTMLParser) -> list[str]:
    """ "Výbava vozidla" and a paragraph after it: "ESP, el.ovládání zrcátek, ..."."""
    for title in tree.css("#ad_description div.title"):
        if clean_spaces(title.text()) != "Výbava vozidla":
            continue
        node = title.next
        while node is not None and node.tag != "p":
            node = node.next
        if node is not None:
            return [x for p in node.text().split(",") if (x := clean_spaces(p))]
    return []


SOURCE = BazarCz()
