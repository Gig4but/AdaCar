"""autoingarant.cz: AUTO IN Garant, the used cars of the AUTO IN group (Ford, Hyundai and other
makes; ~400 cars at its branches across the country, from Pardubice to Ostrava and Liberec, and
some of partner dealers).

Server-rendered HTML. The listing (`/vsechny-vozy?page=<n>`, 9 cars per page, ~44 pages, in the
site's "recommended" order) is read whole on every run; cars it no longer lists expire then.
Pages past the end answer an empty listing whose page links run on, so the listing ends where a
page has no link to the next one. robots.txt disallows only /admin/ and /includes/.

A card gives the title, price, mileage, power, fuel, date of manufacture, engine size, gearbox,
a photo and whether the VAT is deductible. The detail page adds body, colour, VIN, drive,
service book, whether the car was bought in Czechia, the seller's name (AUTO IN or a partner),
the seller's note, equipment and all photos. Neither tells the branch the car stands at (the
note sometimes does, in words), so the cars have no location.
"""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, has_page_link, node_value

SOURCE_ID = "autoingarant"
BASE = "https://autoingarant.cz"
LISTING = BASE + "/vsechny-vozy"
AD_URL = re.compile(r"^https://autoingarant\.cz/vsechny-vozy/([a-z0-9-]+)$")
MADE = re.compile(r"^(?:\d{1,2}/)?((?:19|20)\d{2})$")
SELLER = "AUTO IN"
DRIVES = {"4x4": "4x4", "predni": "front", "zadni": "rear"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def model_name(title: str) -> str | None:
    """ "Ford Kuga Titanium 1.5" -> "Kuga", "BMW Řada 4 440i" -> "Řada 4"."""
    words = title.replace(",", " ").split()
    if len(words) < 2 or not make_code(words[0]):
        return None
    if fold(words[1]) == "rada" and len(words) > 2:
        return f"{words[1]} {words[2]}"
    return words[1]


def set_param(car: ParsedCar, text: str) -> None:
    """One of the unlabelled parameters: "172 364 km", "1 498 ccm", "1/2016", "benzin"."""
    folded = fold(text)
    if m := MADE.match(text):
        car.year = int(m[1])
    elif folded.endswith(" km"):
        car.mileage_km = _number(text)
    elif folded.endswith(" ccm"):
        cc = _number(text)
        car.engine_cc = cc if cc and cc >= 500 else None
    elif folded.endswith(" kw"):
        car.power_kw = _number(text)
    elif folded.startswith(("automat", "manual")):
        car.gearbox = "automatic" if folded.startswith("automat") else "manual"
    elif fuel := cars.fuel_in_text(text):
        # The fuel field calls a plug-in a hybrid; the title often says which.
        plugin = fuel == "hybrid" and cars.fuel_in_text(car.title) == "plugin_hybrid"
        car.fuel = "plugin_hybrid" if plugin else fuel


def condition(text: str | None) -> str | None:
    """ "OJETÝ VŮZ" -> "used"."""
    folded = fold(text or "")
    if "predvadec" in folded:
        return "demo"
    if "ojet" in folded:
        return "used"
    return "new" if "novy" in folded else None


def labels(node: LexborNode) -> list[str]:
    return [fold(t) for x in node.css(".car-category-label") if (t := node_value(x))]


def set_price(car: ParsedCar, node: LexborNode | None) -> None:
    """ "Cena (s DPH): 269 000 Kč"."""
    price = cars.parse_price((node.text() if node is not None else "").split(":")[-1])
    if price and price[1] == "CZK":
        car.price = price[0]


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first("h2 a[href]")
    m = AD_URL.match(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    title = clean_spaces(link.text())
    car = ParsedCar(
        external_id=m[1],
        title=title,
        url=m[0],
        make=make_code(title),
        model=model_name(title),
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    set_price(car, box.css_first(".prices .with-vat"))
    car.vat_deductible = True if "moznost odpoctu dph" in labels(box) else None
    for item in box.css(".parameters [title]"):
        if text := node_value(item):
            set_param(car, text)
    image = box.css_first(".car-photo img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [BASE + src if src.startswith("/") else src]
    return Lead(m[1], m[0], m[0], car)


def table(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Karoserie" -> "SUV", "VIN" -> "WF0A…", ..."""
    found: dict[str, str] = {}
    for row in tree.css(".suplimentary-parameters .grid-x"):
        cells = row.css(".cell")
        if len(cells) >= 2 and (label := node_value(cells[0])) and (value := node_value(cells[1])):
            found.setdefault(label, value)
    return found


class AutoInGarant(CrawlSource):
    id = SOURCE_ID
    label = "AUTO IN Garant"
    kind: SourceKind = "dealer"
    default_enabled = False  # its cars have no place, so distance and region filters miss them
    nationwide = True
    max_pages = 100

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [
            lead for box in tree.css("article.car-item-horizontal") if (lead := parse_card(box))
        ]
        return ListingPage(leads, bool(leads) and has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        heading = tree.css_first("h1.car-title")
        title = clean_spaces(node_value(heading) or "")
        if heading is None or heading.parent is None or not title:
            return None
        info = table(tree)
        tags = labels(heading.parent)  # not those of the other cars the page offers
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            make=make_code(title),
            model=model_name(title),
            condition=next((c for t in tags if (c := condition(t))), "used"),
            vat_deductible=True if "moznost odpoctu dph" in tags else None,
            color=info.get("Barva"),
            vin=(info.get("VIN") or "").upper() or None,
            drive=DRIVES.get(fold(info.get("Pohon") or "")),
            service_book={"ano": True, "ne": False}.get(fold(info.get("Servisní knížka") or "")),
            first_owner={"ano": True, "ne": False}.get(fold(info.get("První majitel") or "")),
            origin_country="Česká republika"
            if fold(info.get("Koupeno v ČR") or "") == "ano"
            else None,
            seller_kind="dealer",
            seller_name=node_value(tree.css_first(".prodejce strong")) or SELLER,
        )
        if body := info.get("Karoserie"):
            car.body = cars.body_in_text(body) or "other"
        params = tree.css_first(".parameters")  # the car's; other cars' cards follow
        for item in params.css(".cell") if params is not None else []:
            if text := node_value(item):
                set_param(car, text)
        set_price(car, tree.css_first(".prices .with-vat"))
        note = tree.css_first("#prodejce-poznamka-text")
        car.description = node_text(note) if note is not None else ""
        car.equipment = list(
            dict.fromkeys(t for li in tree.css(".vybava li") if (t := node_value(li)))
        )
        photos = (a.attributes.get("href") or "" for a in tree.css("a.gallery-item[href]"))
        car.images = list(dict.fromkeys(BASE + p for p in photos if p.startswith("/content/")))
        return car


SOURCE = AutoInGarant()
