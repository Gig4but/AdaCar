"""autosalon.csobleasing.cz: ČSOB Leasing Autosalon, where the leasing company of the ČSOB
bank sells cars returned from leases (~110 "zánovní vozy", mostly Škoda, 2–5 years old). The
cars stand at Autosalon ČSOB Leasing, Kostelecká 910, 196 00 Praha 9-Čakovice.

Server-rendered HTML (Kentico). The listing (`/zanovni-vozy`) renders only its first 12 cars;
the rest come from a POST to `/api/`, which robots.txt forbids. The site's sitemap
(`/sitemap.xml`) names every car's page (`/zanovni-vozy/<id>`), so it is the listing, read
whole on every run: a car it no longer names is gone.

The detail page gives make, model, version, body, the date it went into service, mileage,
fuel, engine size, power, gearbox, drive, colour, the price with and without VAT, equipment
and photos (no VIN).
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.arval import fuel
from adacar.scraper.sources.toyota import gearbox

SOURCE_ID = "csobleasing"
BASE = "https://autosalon.csobleasing.cz"
SITEMAP = BASE + "/sitemap.xml"
AD_URL = re.compile(r"<loc>\s*(https://autosalon\.csobleasing\.cz/zanovni-vozy/(\d+))\s*</loc>")
TOWN_PSC = re.compile(r"^(.+?)\s+(\d{3}\s?\d{2})$")
PHOTO = re.compile(r"url\('([^']+)'\)")
SELLER = "ČSOB Leasing"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def params(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Palivo" -> "benzín", "Objem" -> "999 cm³", ... from the full specification."""
    found: dict[str, str] = {}
    for item in tree.css(".as-techspec__params > div"):
        label = node_value(item.css_first(".as-techspec__param-label"))
        value = node_value(item.css_first(".as-techspec__param-value"))
        if label and value:
            found.setdefault(label, value)
    return found


def _location(car: ParsedCar, tree: LexborHTMLParser, places: PlaceResolver) -> None:
    """ "Kostelecká ul.910", "Praha 9 Čakovice 196 00"."""
    lines = [node_value(p) or "" for p in tree.css(".as-dealer__branch p")]
    town = TOWN_PSC.match(lines[-1]) if lines else None
    if town:
        car.place_text = town[1]
        street = lines[-2].replace("ul.", "") if len(lines) > 1 else None
        # The resolver reads a city part written "Praha 9-Čakovice".
        name = re.sub(r"^(Praha \d+) (?=\D)", r"\1-", town[1])
        car.location = places.resolve(name, street=street, postal_code=town[2])


class CsobLeasing(CrawlSource):
    id = SOURCE_ID
    label = "ČSOB Leasing Autosalon"
    kind: SourceKind = "leasing"
    nationwide = True
    max_pages = 1

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, lambda page: SITEMAP)]

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        leads = [Lead(m[2], m[1], m[1]) for m in AD_URL.finditer(html)]
        return ListingPage(leads, has_next=False)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        side = tree.css_first(".as-item-detail__sidebar")
        if side is None:
            return None
        info = params(tree)
        brand = info.get("Značka") or node_value(side.css_first(".as-item-detail__brand"))
        model = info.get("Model") or node_value(side.css_first(".as-item-detail__model"))
        version = node_value(side.css_first(".as-item-detail__tag")) or ""
        body = info.get("Karoserie") or node_value(side.css_first(".as-item-detail__bodytype"))
        price = _number(node_value(tree.css_first(".as-price__value")))
        net = _number(node_value(tree.css_first(".as-price__novat")))
        since = re.search(r"(19|20)\d\d", info.get("V provozu od") or "")
        photos = (
            PHOTO.search(b.attributes.get("style") or "") for b in tree.css(".as-gallery__thumb")
        )
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{brand or ''} {version or model or ''}") or "Auto",
            url=lead.url,
            make=make_code(brand),
            model=model,
            year=int(since[0]) if since else None,
            mileage_km=_number(info.get("Tachometr")),
            price=price,
            # A lower price without VAT, as Das WeltAuto's, says the VAT is deductible.
            vat_deductible=True if price and net and net < price else None,
            fuel=fuel(info.get("Palivo"), version),
            gearbox=gearbox(info.get("Převodovka")),
            body=cars.body_in_text(body) or ("other" if body else None),
            drive="4x4" if info.get("Pohon") == "4x4" else None,
            condition="used",
            power_kw=cars.power_in_text(info.get("Výkon")),
            engine_cc=_number(info.get("Objem")),
            color=info.get("Barva"),
            seller_kind="dealer",
            seller_name=SELLER,
            equipment=list(
                dict.fromkeys(
                    text for li in tree.css(".as-equipment__item") if (text := node_value(li))
                )
            ),
            images=list(dict.fromkeys(urljoin(BASE, m[1]) for m in photos if m)),
        )
        _location(car, tree, places)
        return car


SOURCE = CsobLeasing()
