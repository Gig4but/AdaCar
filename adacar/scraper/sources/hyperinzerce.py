"""autobazar.hyperinzerce.cz: general classifieds, the "Auta" category, HTML.

One nationwide listing sorted newest first (`/auta?sortBy=3&page=<n>`, 20 per page, ~1 300
ads); the walk stops at the first page with no new ad. Ads unseen for a while are checked on
their own page, which answers 404 once the ad is gone.

Cards give the title, price, place ("Celá ČR" for no place) and the seller's whole text;
detail pages add a form the seller filled (make, model, body, fuel, gearbox, colour, year,
mileage, engine size), equipment, private or company ad, date and photos. What the form
leaves empty is read from the text with the rules in normalize/cars.py. Wanted ads ("poptávka"
in the URL), buyers and parts sellers are kept but marked like on bazos.
"""

import re
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SellerKind, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize.text import clean_spaces, clean_title, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, has_page_link, node_value
from adacar.scraper.sources.bazar_cz import body, fuel, gearbox, mileage, number, year
from adacar.scraper.sources.bazos import add_facts, set_price

SOURCE_ID = "hyperinzerce"
BASE = "https://autobazar.hyperinzerce.cz"
AD_URL = re.compile(r"^https://autobazar\.hyperinzerce\.cz/auta/inzerat/(\d+)-[^/?#]+$")
POSTED = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})(?:\s*-\s*(\d{1,2}):(\d{2}))?")
CONDITIONS = (("havar", "damaged"), ("nahradni", "damaged"), ("nov", "new"), ("ojet", "used"))


def posted(text: str | None) -> datetime | None:
    """ "17. 11. 2026 - 11:00"."""
    m = POSTED.search(text or "")
    if not m:
        return None
    try:
        return datetime(int(m[3]), int(m[2]), int(m[1]), int(m[4] or 0), int(m[5] or 0))
    except ValueError:
        return None


def seller_kind(text: str | None) -> SellerKind | None:
    """ "soukromá inzerce" or "firemní inzerce"."""
    folded = fold(text or "")
    if "soukrom" in folded:
        return "private"
    return "dealer" if "firem" in folded or "komercni" in folded else None


def condition(text: str | None) -> str | None:
    folded = fold(text or "")
    return next((code for word, code in CONDITIONS if word in folded), None) if folded else None


def mark_wanted(car: ParsedCar, url: str) -> None:
    if "-poptavka-" in url and "not_offer" not in car.extra:
        car.extra["not_offer"] = "wanted"


class Hyperinzerce(CrawlSource):
    id = SOURCE_ID
    label = "Hyperinzerce – auta"
    kind: SourceKind = "classifieds"
    newest_first = True
    nationwide = True
    max_pages = 150  # the first run reads every ad (~67 pages): later runs stop early
    recheck_after_days = 14
    refresh_after_days = None  # private ads rarely change

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return f"{BASE}/auta?sortBy=3" + (f"&page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for item in tree.css("div.c-ad-list__item[data-link]"):
            m = AD_URL.match(item.attributes.get("data-link") or "")
            if m is None:
                continue
            url = m[0]
            place = node_value(item.css_first(".c-ad-list__item-location"))
            text = item.css_first(".c-ad-list__item-description")
            car = ParsedCar(
                external_id=m[1],
                title=clean_title(node_value(item.css_first(".c-ad-list__item-name"))),
                url=url,
                description=node_text(text) if text is not None else "",
                place_text=place,
                location=places.resolve(place),
            )
            set_price(car, node_value(item.css_first(".c-ad-list__item-price")))
            add_facts(car, None)
            mark_wanted(car, url)
            image = item.css_first("img.c-ad-list__item-image")
            if image is not None and (src := image.attributes.get("src")):
                car.images = [src]
            leads.append(Lead(m[1], url, url, car))
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first("h1.c-ad-detail__header-title")))
        if not title:
            return None
        params = labelled(tree, ".c-ad-detail__parameters-item")
        info = labelled(tree, ".c-ad-detail__basic-info-item")
        text = tree.css_first(".c-ad-detail__description-text")
        place = info.get("Oblast inzerce") or node_value(
            tree.css_first(".c-ad-detail__header-info-location")
        )
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text) if text is not None else "",
            place_text=place,
            location=places.resolve(place),
            posted_at=posted(node_value(tree.css_first(".c-ad-detail__header-info-date"))),
            seller_name=node_value(tree.css_first(".c-ad-detail__info-seller-name")),
            seller_kind=seller_kind(info.get("Druh inzerátu")),
            color=params.get("Barva vozu"),
            condition=condition(params.get("Stav vozu")),
        )
        set_price(car, node_value(tree.css_first(".c-ad-detail__info-price")))
        add_facts(car, make_code(params.get("Výrobce / značka")))
        mark_wanted(car, lead.url)
        car.model = params.get("Modelová řada") or (car.model if car.make else None)
        car.year = year(params.get("Rok výroby")) or car.year
        car.mileage_km = mileage(params.get("Najeto (km)")) or car.mileage_km
        car.fuel = fuel(params.get("Druh paliva")) or car.fuel
        car.gearbox = gearbox(params.get("Převodovka (řazení)")) or car.gearbox
        car.body = body(params.get("Druh karoserie")) or car.body
        car.engine_cc = number(params.get("Obsah motoru (ccm)"))
        # "Výkon motoru (koní)" holds kW or hp, as the seller understood it: not read.
        car.equipment = [
            x
            for node in tree.css(".c-ad-detail__additional-info-item")
            if (x := clean_spaces(node.text()))
        ]
        if "Odpočet DPH" in car.equipment:
            car.vat_deductible = True
        photos = (
            img.attributes.get("data-gallery-full-url") for img in tree.css("img.js-gallery-item")
        )
        car.images = list(dict.fromkeys(p for p in photos if p))[:20]
        return car


def labelled(tree: LexborHTMLParser, selector: str) -> dict[str, str]:
    """<div class="…-item"><div class="…-label">Rok výroby</div><div …>2018</div></div>."""
    values = {}
    for item in tree.css(selector):
        label, content = item.css_first("[class$=-label]"), item.css_first("[class$=-content]")
        if label is not None and content is not None and (value := node_value(content)):
            values.setdefault(clean_spaces(label.text()), value)
    return values


SOURCE = Hyperinzerce()
