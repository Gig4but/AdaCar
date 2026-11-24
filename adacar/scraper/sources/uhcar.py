"""uhcar.cz: UH CAR, a dealer in Staré Město (Uherské Hradiště), Zlín and Hodonín (~125 used
cars of many makes, many Suzuki; some sold on commission for clients).

WordPress, server-rendered HTML; robots.txt disallows only /wp-admin/. The used-car listing
(`/prodej-aut/autobazar/?_paged=<n>`, FacetWP, 12 cars per page, its pager state in
`FWP_JSON`) is read whole on every run (~11 pages); cars it no longer lists expire then. Used
vans are listed apart, together with new ones (/prodej-aut/dodavky-skladem/), and not read.

A card gives make and model, version, year, mileage, gearbox, fuel, engine size, power and
the price (labelled "Cena", "Cena s DPH", "Cena bez DPH", "Akční cena"...). The detail adds
body, colour, seats, first owner, service book, equipment, all photos and the seller's text,
which names the branch the car stands at ("Vůz se nachází v UH CAR-Zlín").
"""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.toyota import model_name

SOURCE_ID = "uhcar"
BASE = "https://uhcar.cz"
LISTING = BASE + "/prodej-aut/autobazar/"
AD_URL = re.compile(r"^https://(?:www\.)?uhcar\.cz/ojeta-auta/[a-z0-9-]+-id-(\d+)/$")
PAGER = re.compile(r'"pager":\{"page":(\d+),[^}]*"total_rows":(\d+),[^}]*"total_pages":(\d+)')
AMOUNT = re.compile(r"^(?P<label>\D*?)\s*(?P<amount>\d[\d\s]*)\s*Kč$")
BRANCH = re.compile(r"UH CAR\s*-\s*(?P<town>[^\W\d_][^,\-–*\d]*?)\s*(?:[,\-–*]|$)", re.M)
# The branches' postal codes, to tell "Staré Město" from the other towns of that name.
BRANCH_PSC = {"Staré Město": "68603", "Zlín": "76302", "Hodonín": "69501"}
SELLER = "UH CAR"
VAT = 1.21
INSTALMENT = re.compile(r"(?i)splátk\w*[^\d\n]{0,40}?(\d[\d . ]*)\s*Kč")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def is_instalment(price: int | None, text: str) -> bool:
    return price is not None and any(_number(m[1]) == price for m in INSTALMENT.finditer(text))


def gearbox(text: str | None) -> str | None:
    """ "Manuální", "Automatická"."""
    return cars.gearbox_in_text(f"{text or ''} převodovka")


def body(text: str | None) -> str | None:
    """ "hatchback", "SUV", "terénní vozidlo"."""
    if text and fold(text).startswith("teren"):
        return "suv"
    return cars.body_in_text(text) or ("other" if text else None)


def set_price(car: ParsedCar, box: LexborNode | None) -> None:
    """The price the buyer pays: "Akční cena" over "Původní cena", VAT added to "Cena bez
    DPH"; a price said to be with or without VAT is a dealer's, the VAT deductible."""
    if box is None:
        return
    found = None
    previous = ""
    for line in box.text(separator="\n").split("\n"):
        line = clean_spaces(line)
        m = AMOUNT.match(line)
        if m is None:
            previous = line or previous
            continue
        label = fold(m["label"] or previous)
        if not label.startswith(("puvodni", "cenikova", "usetrite")):
            found = (label, int(re.sub(r"\D", "", m["amount"])))
        previous = ""
    if found is None or found[1] < cars.MIN_PRICE:
        return
    label, amount = found
    car.price = round(amount * VAT) if "bez dph" in label else amount
    if "dph" in label:
        car.vat_deductible = True


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first("a.car[href]")
    m = AD_URL.match(link.attributes.get("href") or "") if link is not None else None
    if m is None:
        return None
    url = m[0].replace("://www.", "://")
    name = node_value(box.css_first(".car__title")) or ""
    version = node_value(box.css_first(".car__sub-title")) or ""
    car = ParsedCar(
        external_id=m[1],
        title=clean_spaces(f"{name} {version}") or "Auto",
        url=url,
        make=make_code(name),
        condition="used",
        seller_kind="dealer",
        seller_name=SELLER,
    )
    car.model = model_name(name, car.make)
    for icon in box.css(".car__icon"):
        image = icon.css_first("img")
        kind = image.attributes.get("alt") if image is not None else None
        text = node_value(icon.css_first(".car__icon-text"))
        if kind == "Rok výroby":
            car.year = _number(text)
        elif kind == "Najeto":
            car.mileage_km = _number(text)
        elif kind == "Převodovka":
            car.gearbox = gearbox(text)
        elif kind == "Palivo":
            car.fuel = cars.fuel_in_text(text)
        elif kind == "Objem":
            car.engine_cc = _number((text or "").replace("cm³", ""))
        elif kind == "Výkon":
            car.power_kw = cars.power_in_text(text)
        elif kind == "Nový vůz":
            car.condition = "new"
    set_price(car, box.css_first(".car__content-price-section"))
    image = box.css_first(".car__thumbnail img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [src]
    return Lead(m[1], url, url, car)


class UhCar(CrawlSource):
    id = SOURCE_ID
    label = "UH CAR"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 40

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_url_at)]

    @staticmethod
    def listing_url_at(page: int) -> str:
        return LISTING + (f"?_paged={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("article.car__container") if (lead := parse_card(box))]
        pager = PAGER.search(html)
        return ListingPage(
            leads,
            has_next=pager is not None and int(pager[1]) < int(pager[3]),
            none_listed=pager is not None and pager[2] == "0",
        )

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        name = node_value(tree.css_first("h1[data-car-title]"))
        if not name:
            return None
        version = node_value(tree.css_first("h1[data-car-title] + div"))
        info = {
            label: value
            for tr in tree.css("table tr")
            if (label := (node_value(tr.css_first("th")) or "").rstrip(":"))
            and (value := node_value(tr.css_first("td")))
        }
        tags = {fold(t) for li in tree.css("h1 ~ ul.list-none li") if (t := node_value(li))}
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {version or ''}"),
            url=lead.url,
            make=make_code(name),
            year=_number(info.get("Rok výroby")),
            mileage_km=_number(info.get("Najeto")),
            fuel=cars.fuel_in_text(info.get("Palivo")),
            gearbox=gearbox(info.get("Převodovka")),
            body=body(info.get("Karoserie")),
            condition="used",
            power_kw=cars.power_in_text(info.get("Motor a výkon")),
            color=info.get("Barva"),
            seats=_number(info.get("Počet míst")),
            first_owner=True if "prvni majitel" in tags else None,
            service_book=True if "servisni knizka" in tags else None,
            vat_deductible=True if "moznost odpoctu dph" in tags else None,
            seller_kind="dealer",
            seller_name=SELLER,
            equipment=[t for li in tree.css("ul.checklist li") if (t := node_value(li))],
        )
        car.model = model_name(name, car.make)
        if lead.card is not None and lead.card.condition == "new":
            car.condition = "new"
        set_price(car, tree.css_first('div[class*="lg:sticky"]'))
        text = tree.css_first("div.leading-relaxed")
        car.description = node_text(text) if text is not None else ""
        if is_instalment(car.price, car.description):
            car.price = None
            if lead.card is not None:
                lead.card.price = None
        if m := BRANCH.search(car.description):
            car.place_text = clean_spaces(m["town"])
            car.location = places.resolve(
                car.place_text, postal_code=BRANCH_PSC.get(car.place_text)
            )
        photos = (a.attributes.get("href") for a in tree.css("a.owl-gallery-link[href]"))
        car.images = list(dict.fromkeys(p for p in photos if p))
        return car


SOURCE = UhCar()
