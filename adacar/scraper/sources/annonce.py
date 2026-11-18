"""www.annonce.cz: general classifieds, the "Osobní auta" category, HTML.

One nationwide listing, newest first (`/osobni-auta.html?page=<n>`, 20 per page; one paid ad
may lead page 1). robots.txt asks for 20 s between requests, so a run reads few pages: the
walk stops at the first page with no new ad, a first run at `max_pages`, and only
`max_details` detail pages per run (the cards already list the car). Ads unseen for a while
are checked on their own page (404 once gone); each ad also tells when it runs out.

Sellers fill a form: cards give make, model, year, mileage, price, place, date and offer or
wanted; detail pages add body, power, engine size, colour, fuel, gearbox, VAT, crash, the
street, the whole text, photos, and (JSON-LD) whether a company or a person sells. What the
form leaves empty is read from the text with the rules in normalize/cars.py; wanted ads,
buyers and parts sellers are kept but marked like on bazos.
"""

import json
import re
from datetime import datetime
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import card_date, clean_spaces, clean_title, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, has_page_link, node_value
from adacar.scraper.sources.bazar_cz import body, fuel, gearbox, mileage, number, year
from adacar.scraper.sources.bazos import add_facts, set_price

SOURCE_ID = "annonce"
BASE = "https://www.annonce.cz"
# "/inzerat/skoda-octavia-iii-1-6-89879915-w3j4ad.html": the number is the ad's id.
AD_PATH = re.compile(r"^/inzerat/[^/]*-(\d+)-[a-z0-9]+\.html$")
CRAWL_DELAY = 20.0  # robots.txt


def yes_no(text: str | None) -> bool | None:
    folded = fold(text or "")
    return True if folded == "ano" else False if folded == "ne" else None


def condition(text: str | None) -> str | None:
    """ "ojeté", "nové", "předváděcí"."""
    folded = fold(text or "")
    for word, code in (("ojet", "used"), ("nov", "new"), ("predvad", "demo")):
        if folded.startswith(word):
            return code
    return None


def mark_wanted(car: ParsedCar, kind: str | None) -> None:
    """ "Nabídka" or "Poptávka" (wanted)."""
    if "poptav" in fold(kind or "") and "not_offer" not in car.extra:
        car.extra["not_offer"] = "wanted"


def attributes(table: LexborNode | None) -> dict[str, LexborNode]:
    """Form rows by their field name: <th data-name-id="mileage">Najeto:</th><td>…</td>;
    rows without one by their label ("Kraj")."""
    rows: dict[str, LexborNode] = {}
    for row in table.css("tr") if table is not None else []:
        th, td = row.css_first("th"), row.css_first("td")
        if th is not None and td is not None:
            name = th.attributes.get("data-name-id") or clean_spaces(th.text()).rstrip(":")
            rows.setdefault(name, td)
    return rows


def offered_car(tree: LexborHTMLParser) -> dict[str, Any]:
    """The JSON-LD "Car" of a detail page (seller, dates, photos); {} when unreadable."""
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text(), strict=False)  # descriptions keep raw newlines
        except ValueError:
            continue
        car = data.get("mainEntity") if isinstance(data, dict) else None
        if isinstance(car, dict) and car.get("@type") == "Car":
            return car
    return {}


def part(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def timestamp(text: Any) -> datetime | None:
    """ "2026-11-17T14:46:43.000+01:00" -> local time, as the site shows it."""
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None) if text else None
    except (TypeError, ValueError):
        return None


class Annonce(CrawlSource):
    id = SOURCE_ID
    label = "Annonce – osobní auta"
    kind: SourceKind = "classifieds"
    newest_first = True
    nationwide = True
    delay = CRAWL_DELAY
    max_pages = 10  # the first run reads the newest 200 ads
    max_details = 40  # ~15 min at 20 s a page
    recheck_after_days = 14
    refresh_after_days = None  # the ads rarely change

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return f"{BASE}/osobni-auta.html" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for box in tree.css("div.box.q"):
            link = box.css_first("h2 a[href]")
            m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
            if link is None or m is None:
                continue
            url = BASE + m[0]
            attrs = attributes(box.css_first("table.attrs"))
            place = node_value(box.css_first("table.attrs td.right"))
            text = box.css_first("p.ad-desc")
            car = ParsedCar(
                external_id=m[1],
                title=clean_title(node_value(link)),
                url=url,
                description=node_text(text) if text is not None else "",
                place_text=place,
                location=places.resolve(place),
                posted_at=card_date(node_value(box.css_first(".ad-date"))),
            )
            set_price(car, node_value(box.css_first(".mini-sticker")))
            fill(car, attrs)
            mark_wanted(car, node_value(box.css_first(".request-type")))
            image = box.css_first("a.thumbnail img")
            if image is not None and (src := image.attributes.get("src")):
                car.images = [src]
            leads.append(Lead(m[1], url, url, car))
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first("h1")))
        table = tree.css_first("table.table-car")
        if not title or table is None:
            return None
        attrs = attributes(table)

        def value(name: str) -> str | None:
            return node_value(attrs.get(name))

        offer = part(offered_car(tree), "offers")
        seller = part(offer, "seller")
        address = part(seller, "address")
        city, street = value("location_city"), value("location_street")
        # A dealer's address is the car's place only when it names the same town.
        psc = address.get("postalCode") if address.get("addressLocality") == city else None
        text = tree.css_first("p.ad-desc")
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text) if text is not None else "",
            place_text=city,
            location=places.resolve(city, street=street, postal_code=psc, region=value("Kraj")),
            posted_at=timestamp(offer.get("validFrom")),
            expires_at=timestamp(offer.get("validThrough")),
            color=value("color"),
            condition=condition(value("state")),
            crashed=yes_no(value("crashed")),
            vat_deductible=yes_no(value("vat_expel_flag")),
            engine_cc=number(value("cubature")),
        )
        if seller.get("@type") == "Organization":
            car.seller_kind, car.seller_name = "dealer", seller.get("name")
        elif seller.get("@type") == "Person":
            car.seller_kind = "private"
        set_price(car, value("price"))
        fill(car, attrs)
        mark_wanted(car, value("Typ inzerátu"))
        car.body = body(value("body_style")) or car.body
        car.fuel = fuel(value("fuel")) or car.fuel
        car.gearbox = gearbox(value("transmission")) or car.gearbox
        if (load := attrs.get("load")) is not None:  # "85\nkW"
            car.power_kw = cars.power_in_text(clean_spaces(load.text())) or car.power_kw
        photos = (a.attributes.get("href") for a in tree.css("ul.thumbnails li a[href]"))
        car.images = list(dict.fromkeys(p for p in photos if p))[:20]
        return car


def fill(car: ParsedCar, attrs: dict[str, LexborNode]) -> None:
    """Text rules first, then the form's values where the seller gave them."""
    make = make_code(node_value(attrs.get("manufacturer")))
    add_facts(car, make)
    car.model = node_value(attrs.get("model")) or (car.model if car.make else None)
    car.year = year(node_value(attrs.get("year"))) or car.year
    car.mileage_km = mileage(node_value(attrs.get("mileage"))) or car.mileage_km


SOURCE = Annonce()
