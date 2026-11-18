"""www.inzeruj.cz: a small general classifieds site, the "Osobní auta" category, HTML.

The whole category is one nationwide listing of ~90 ads (`?page=<n>`, 24 per page), so every
run reads it to the end and ads no longer listed are gone (a deleted ad's page answers 500,
not 404, so pages are not checked one by one).

Ads are free text: the card gives title, price, town and the start of the text; the detail
page the whole text, the seller's name and photos, and its URL the make's category. Year,
mileage, power, fuel, gearbox and body come from the rules in normalize/cars.py; buyers and
parts sellers (the category also holds wheels) are kept but marked like on bazos.
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize.text import clean_spaces, clean_title, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, has_page_link, node_value
from adacar.scraper.sources.bazos import add_facts, set_price

SOURCE_ID = "inzeruj"
BASE = "https://www.inzeruj.cz"
# "/inzerat/auto/osobni-auta/mazda/mazda-6-….html": the make's category, then the ad.
AD_PATH = re.compile(r"^/inzerat/auto/osobni-auta/([a-z-]+)/[^/]+\.html$")


def category_make(url: str) -> str | None:
    """ "…/osobni-auta/mercedes-benz/…" -> "mercedes-benz"; "ostatni-znacky", "veterani": None."""
    m = AD_PATH.match(url.removeprefix(BASE))
    return make_code(m[1].replace("-", " ")) if m else None


def post_info(tree: LexborHTMLParser) -> dict[str, str]:
    """<p>Lokalita: Praha</p> -> {"Lokalita": "Praha"}."""
    info = {}
    for p in tree.css(".post-info p"):
        label, _, value = clean_spaces(p.text()).partition(":")
        if value.strip():
            info.setdefault(label, value.strip())
    return info


class Inzeruj(CrawlSource):
    id = SOURCE_ID
    label = "Inzeruj.cz – osobní auta"
    kind: SourceKind = "classifieds"
    nationwide = True
    max_pages = 20  # ~4 today
    refresh_after_days = None  # private ads rarely change

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return f"{BASE}/categories/auto/osobni-auta" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("div.card-wrap[data-pid]"):
            link = card.css_first("a[href]")
            path = (link.attributes.get("href") or "") if link is not None else ""
            pid = card.attributes.get("data-pid") or ""
            if not AD_PATH.match(path) or not pid.isdigit():
                continue
            url = BASE + path
            place = node_value(card.css_first(".card-town"))
            snippet = card.css_first(".card-text")
            car = ParsedCar(
                external_id=pid,
                title=clean_title(node_value(card.css_first(".card-title"))),
                url=url,
                description=node_text(snippet) if snippet is not None else "",
                place_text=place,
                location=places.resolve(place),
            )
            set_price(car, node_value(card.css_first(".card-price")))
            add_facts(car, category_make(url))
            image = card.css_first(".card-img img")
            if image is not None and (src := image.attributes.get("src")):
                car.images = [urljoin(BASE, src)]
            leads.append(Lead(pid, url, url, car))
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first("h1#post-title")))
        text = tree.css_first("#post-content > div")
        if not title or text is None:
            return None
        info = post_info(tree)
        place = info.get("Lokalita")
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text),
            place_text=place,
            location=places.resolve(place),
            seller_name=info.get("Jméno"),
        )
        set_price(car, info.get("Cena"))
        add_facts(car, category_make(lead.url))
        photos = (li.attributes.get("data-link") for li in tree.css("li[data-link]"))
        car.images = list(dict.fromkeys(urljoin(BASE, p) for p in photos if p))[:20]
        return car


SOURCE = Inzeruj()
