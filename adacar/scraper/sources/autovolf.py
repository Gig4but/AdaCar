"""autovolf.cz: AUTO VOLF, a Škoda, Volvo, Kia and Hyundai dealer in the Plzeň region (~230
used, demonstrator and one-year-old cars at Plzeň, Holýšov, Stříbro and Domažlice), HTML.

robots.txt disallows only /share/download. The listing (`/ojete-vozy/<n>?stavId=2`, 12 cars
per page, cheapest first; a page past the end shows the last one again) is read whole on
every run (~20 pages). Cars of the same price change places between requests, so one may
fall between two pages: a car expires only when unseen for a few days, not after one pass.
The listing also shows operating-lease offers of new cars (a monthly rate, no price): skipped.

A card gives the title (make, model, trim), engine, power, gearbox and drive, body, price
(with "vč DPH" when the VAT is deductible) and a photo. The detail adds first registration,
mileage, fuel, colour, engine size, first owner, service book, origin, VIN, the branch,
equipment and all photos.
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold
from adacar.normalize.vocab import MAKES, make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "autovolf"
BASE = "https://www.autovolf.cz"
LISTING = BASE + "/ojete-vozy"
AD_PATH = re.compile(r"^(?:https://www\.autovolf\.cz)?(/vuz-skladem/(\d+)/[^/?#]*)$")
REGISTERED = re.compile(r"^(?:\d{1,2}/)?((?:19|20)\d{2})$")
SELLER = "Auto Volf"
# Branch name on a detail page -> (town, street, postal code), from the contact page.
BRANCHES = {
    "Plzeň": ("Plzeň", "Folmavská 2828/6", "30100"),
    "Holýšov": ("Holýšov", "Jiráskova 646", "34562"),
    "Stříbro": ("Stříbro", "Plzeňská 1444", "34901"),
    "Domažlice": ("Domažlice", "28. října 287", "34401"),
}
CONDITIONS = {"predvadeci vuz": "demo", "ojety vuz": "used", "rocni vuz": "used"}
ORIGINS = {"cr": "Česká republika", "d": "Německo"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def model_name(title: str, make: str | None) -> str | None:
    """ "Škoda Kodiaq Selection" -> "Kodiaq"."""
    name = MAKES[make]["cs"] if make in MAKES else ""
    if name and fold(title).startswith(fold(name) + " "):
        words = title[len(name) :].split()
    else:
        words = title.split()[1:]
    return words[0] if words else None


def power(text: str | None) -> int | None:
    """ "81 kW", "142 kW 4x4", "184kW", or "184" alone."""
    if kw := cars.power_in_text(text):
        return kw
    m = re.fullmatch(r"\s*(\d{2,3})\s*", text or "")
    return int(m[1]) if m else None


def gearbox(text: str | None) -> str | None:
    """ "7°automatická/přední", "8° automat 4x4", "5°manuální/přední"."""
    folded = fold(text or "")
    if "automat" in folded:
        return "automatic"
    return "manual" if "manual" in folded else None


def drive(text: str | None) -> str | None:
    folded = fold(text or "")
    if "4x4" in folded:
        return "4x4"
    if "predni" in folded:
        return "front"
    return "rear" if "zadni" in folded else None


def yes(text: str | None) -> bool | None:
    folded = fold(text or "")
    return True if folded == "ano" else False if folded == "ne" else None


def parse_card(box: LexborNode) -> Lead | None:
    m = AD_PATH.match(box.attributes.get("href") or "")
    title = node_value(box.css_first("h2"))
    price = box.css_first("span.text-success")
    if m is None or not title or price is None:
        return None  # an operating-lease offer has a monthly rate instead
    url = BASE + m[1]
    make = make_code(title)
    line = clean_spaces((box.css_first("p") or box).text())
    specs = (re.split(r"\s*\|\s*", line) + [""] * 4)[:4]  # engine, power, gearbox, body
    car = ParsedCar(
        external_id=m[2],
        title=clean_spaces(f"{title} {specs[0]}"),
        url=url,
        make=make,
        model=model_name(title, make),
        power_kw=power(specs[1]),
        gearbox=gearbox(specs[2]),
        drive=drive(specs[2]),
        body=cars.body_in_text(specs[3]) or ("other" if specs[3] else None),
        fuel=cars.fuel_in_text(specs[0]),
        seller_kind="dealer",
        seller_name=SELLER,
    )
    found = cars.parse_price(node_value(price))
    if found and found[1] == "CZK":
        car.price = found[0]
    after = price.next
    while after is not None and after.tag != "small":
        after = after.next
    if after is not None and "dph" in fold(after.text()):
        car.vat_deductible = True
    tags = [fold(node_value(t) or "") for t in box.css(".car-list-tags .tag")]
    car.condition = next((CONDITIONS[t] for t in tags if t in CONDITIONS), "used")
    image = box.css_first("img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [urljoin(BASE, src)]
    return Lead(m[2], url, url, car)


def facts(tree: LexborHTMLParser) -> dict[str, str]:
    """ "Do provozu" -> "2017", "VIN" -> "TMB...", "Barva" -> "červená Corida", ..."""
    found: dict[str, str] = {}
    for box in tree.css("p.bg-faded"):
        label, value = node_value(box.css_first("small")), node_value(box.css_first("span.h4"))
        if label and value:
            found.setdefault(label, value)
    for row in tree.css("table tr"):
        label, value = node_value(row.css_first("th")), node_value(row.css_first("td"))
        if label and value:
            found.setdefault(label, value)
    return found


class AutoVolf(CrawlSource):
    id = SOURCE_ID
    label = "Auto Volf"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 40
    expire_after_days = 3

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"/{page}?stavId=2" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("a.card[href]") if (lead := parse_card(box))]
        next_href = f"/ojete-vozy/{page + 1}?stavId=2"
        has_next = any(a.attributes.get("href") == next_href for a in tree.css("a.page-link"))
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = node_value(tree.css_first("h1"))
        if not title:
            return None
        info = facts(tree)
        subtitle = tree.css_first("h1 + p")
        specs = re.split(r"\s*\|\s*", clean_spaces(subtitle.text())) if subtitle else []
        engine = specs[0] if specs else ""
        make = make_code(title)
        transmission = info.get("Pohon / převodovka")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{title} {engine}"),
            url=lead.url,
            make=make,
            model=model_name(title, make),
            mileage_km=_number(info.get("Tachometr")),
            fuel=cars.fuel_in_text(info.get("Palivo")),
            power_kw=power(info.get("Výkon")),
            gearbox=gearbox(transmission),
            drive=drive(transmission),
            body=cars.body_in_text(info.get("Karoserie"))
            or ("other" if info.get("Karoserie") else None),
            engine_cc=_number((info.get("Objem motoru") or "").partition("cm")[0]),
            color=info.get("Barva"),
            first_owner=yes(info.get("První majitel")),
            service_book=yes(info.get("Servisní knížka")),
            origin_country=ORIGINS.get(fold(info.get("Původ") or ""), info.get("Původ")),
            vin=(info.get("VIN") or "").upper() or None,
            seller_kind="dealer",
            seller_name=SELLER,
        )
        if m := REGISTERED.match(info.get("Do provozu") or ""):
            car.year = int(m[1])
        price = tree.css_first("span.h2.text-success")
        if price is not None:
            found = cars.parse_price(node_value(price))
            if found and found[1] == "CZK":
                car.price = found[0]
            box = price.parent
            car.vat_deductible = True if box and "bez dph" in fold(box.text()) else None
        tags = [node_value(t) or "" for t in tree.css("h1 ~ ul .tag")]
        car.condition = next((CONDITIONS[fold(t)] for t in tags if fold(t) in CONDITIONS), "used")
        notes = [t for t in tags if fold(t) not in CONDITIONS]
        if warranty := info.get("Záruka"):
            notes.append(f"Záruka: {warranty}")
        car.description = "\n".join(notes)
        car.equipment = list(
            dict.fromkeys(t for li in tree.css("#vybava li") if (t := node_value(li)))
        )
        photos = (a.attributes.get("href") or "" for a in tree.css("a.car-gallery[href]"))
        car.images = list(dict.fromkeys(urljoin(BASE, p) for p in photos if p))
        branch = node_value(tree.css_first("#kontakty .panel-title a"))
        if branch:
            car.place_text = branch
            town, street, psc = BRANCHES.get(branch, (branch, None, None))
            car.location = places.resolve(town, street=street, postal_code=psc)
        return car


SOURCE = AutoVolf()
