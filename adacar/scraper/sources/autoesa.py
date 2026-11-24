"""autoesa.cz: Auto ESA, a used-car dealer with three sites in Prague and eleven regional
branches (~5 600 cars).

Server-rendered HTML; robots.txt only disallows the XML feed. One nationwide listing
(`/vsechna-auta?stranka=<n>`, 19 cars per page, ~300 pages) in the site's own "relevance"
order, so the whole of it is read on every run; cars it no longer lists expire then. Neither
the listing nor the detail page says at which branch a car stands (the dealer brings any car
to the branch the buyer picks), so cars have no location.

A card gives the make and model, year, engine, power, fuel, mileage, a photo, a few tags
(VAT deductible, bought new in Czechia, service book) and only the price on credit ("Akční
cena", not taken: paid in cash the car costs more). The detail page adds the cash price
("Cena v hotovosti"), gearbox, body, colour, doors, seats, drive, equipment and all photos;
it has no VIN and no text.
"""

import json
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.toyota import model_name

SOURCE_ID = "autoesa"
BASE = "https://www.autoesa.cz"
LISTING = BASE + "/vsechna-auta"
# /skoda/superb/kombi/nafta/305447398: make, model, body, fuel, id
AD_PATH = re.compile(r"^/[a-z0-9-]+/[a-z0-9-]+/([a-z0-9-]+)/[a-z0-9-]+/(\d+)$")
VIEW_ITEM = re.compile(r"var dataexp_view_item = (\{.*?\});", re.S)
SELLER = "Auto ESA"
CZECHIA = "Česká republika"


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def photo(src: str | None) -> str | None:
    """Absolute, without the cache-busting query ("...-1.jpg?1794416593")."""
    return urljoin(BASE, src.split("?")[0]) if src and "no-photo" not in src else None


def set_tags(car: ParsedCar, tags: list[str]) -> None:
    """The card's tags and the detail's "Ostatní" list ("-DPH", "servis.kniha", ...)."""
    folded = {fold(t) for t in tags}
    if folded & {"-dph", "odpocet dph"}:
        car.vat_deductible = True
    if "koupeno nove v cr" in folded:
        car.origin_country = CZECHIA
    if folded & {"servisni kniha", "servis.kniha"}:
        car.service_book = True


def parse_card(box: LexborNode) -> Lead | None:
    m = AD_PATH.match(box.attributes.get("href") or "")
    heading = box.css_first("h2.car_item__title")
    if m is None or heading is None:
        return None
    url = BASE + m[0]
    name = clean_spaces(heading.text(deep=False))
    icons = {
        (icon.attributes.get("class") or "").split()[-1]: node_value(icon)
        for icon in box.css(".car_item__icon")
    }
    make = make_code(name)
    car = ParsedCar(
        external_id=m[2],
        # the photo's alt text is the whole name ("Škoda Superb III 2.0 TDi Style")
        title=clean_spaces((box.css_first("img[alt]") or heading).attributes.get("alt") or name),
        url=url,
        make=make,
        model=model_name(name, make),
        year=_number(node_value(heading.css_first("span"))),
        mileage_km=_number(icons.get("icon_range")),
        fuel=cars.fuel_in_text(icons.get("icon_fuel")),
        power_kw=cars.power_in_text(icons.get("icon_power")),
        body=cars.body_in_text(m[1]),
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    tags = box.css(".car_image_tag, .car_item__tag")
    set_tags(car, [t for tag in tags if (t := node_value(tag))])
    image = box.css_first(".car_item__image img")
    if image is not None and (src := photo(image.attributes.get("src"))):
        car.images = [src]
    return Lead(m[2], url, url, car)


def params(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Převodovka" -> "automat/7", "Barva" -> "šedá metalíza", ..."""
    found: dict[str, str] = {}
    for item in tree.css("#zakladni_udaje li"):
        label, value = item.css_first("strong"), item.css_first("span")
        if label is not None and value is not None and (text := node_value(value)):
            found.setdefault(clean_spaces(label.text()), text)
    return found


class AutoEsa(CrawlSource):
    id = SOURCE_ID
    label = "Auto ESA"
    kind: SourceKind = "dealer"
    default_enabled = False  # its cars have no place, so distance and region filters miss them
    nationwide = True
    max_pages = 400  # ~300 pages

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"?stranka={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("a.car_item[href]") if (lead := parse_card(box))]
        next_href = f"/vsechna-auta?stranka={page + 1}"
        has_next = any(a.attributes.get("href") == next_href for a in tree.css("a[href]"))
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        heading = node_value(tree.css_first(".car_detail2__h1 h1"))
        m = VIEW_ITEM.search(html)
        if not heading or m is None:
            return None
        item = json.loads(m[1])
        info = params(tree)
        make = make_code(item.get("make"))
        drive = fold(info.get("Pohon") or "").replace("×", "x")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(heading),
            url=lead.url,
            make=make,
            model=clean_spaces(item.get("type") or "") or None,
            year=_number(str(item.get("manufature_year") or "")),
            mileage_km=_number(str(item.get("mileage") or "")),
            price=_number(str(item.get("price_cash") or "")),
            fuel=cars.fuel_in_text(info.get("Palivo")),
            gearbox=cars.gearbox_in_text(info.get("Převodovka")),
            body=cars.body_in_text(info.get("Karosérie"))
            or ("other" if info.get("Karosérie") else None),
            drive="4x4" if drive == "4x4" else None,
            condition="used",
            power_kw=cars.power_in_text(info.get("Výkon")),
            color=info.get("Barva"),
            doors=_number(info.get("Počet dveří")),
            seats=_number(info.get("Počet míst")),
            seller_kind="dealer",
            seller_name=SELLER,
            equipment=list(
                dict.fromkeys(
                    text
                    for part in ("interier", "exterier", "bezpecnost")
                    for li in tree.css(f"#{part} li")
                    if (text := node_value(li))
                )
            ),
        )
        others = [t for li in tree.css("#dalsi_informace li") if (t := node_value(li))]
        set_tags(car, others)
        gallery = tree.css(".initCarDetailOneImage img")
        photos = (photo(img.attributes.get("src")) for img in gallery)
        car.images = list(dict.fromkeys(p for p in photos if p))
        return car


SOURCE = AutoEsa()
