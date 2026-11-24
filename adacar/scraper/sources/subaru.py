"""subaru.cz/vozy-prodej: the used cars of Subaru's Czech dealers (~35 cars, nearly all
Subarus).

Server-rendered PHP pages; robots.txt allows them. One nationwide listing, newest first
(`/vozy-prodej/ojete/?page=<n>`, 12 cars per page), small enough to be read whole on every
run; cars it no longer lists expire then. A sold car's page answers 200 with "Vůz v databázi
... neexistuje".

A card gives the title (model, engine, often the power), year, mileage, fuel, gearbox, price
and labels such as "ODPOČET DPH". The detail adds the dealer with its address, body, power,
engine size, colour, seats, doors, country of origin, service book, first owner, the dealer's
text and photos; no VIN.
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import (
    CrawlSource,
    Lead,
    Listing,
    ListingPage,
    has_page_link,
    node_value,
)
from adacar.scraper.sources.toyota import fuel

SOURCE_ID = "subaru"
BASE = "https://www.subaru.cz"
LISTING = BASE + "/vozy-prodej/ojete/"
AD_HREF = re.compile(r"detail\.php\?detail=((?:[a-z0-9]+-)+?)(\d+)$")
GONE = "s tímto pořadovým číslem neexistuje"


def _km(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits and int(digits) else None


def gearbox(text: str | None) -> str | None:
    """ "automatická", "manuální", "lineartronic" (Subaru's CVT)."""
    name = fold(text or "")
    if name.startswith("autom") or "lineartronic" in name or "cvt" in name:
        return "automatic"
    return "manual" if name.startswith("manu") else None


def _yes(text: str | None) -> bool | None:
    return {"ano": True, "ne": False}.get(fold(text or ""))


def _model(title: str, slug: str) -> str | None:
    """ "Subaru Outback 2,5 CVT ..." with slug "subaru-outback-" -> "Outback": as many words
    after the make as the slug names."""
    count = len(slug.strip("-").split("-")) - 1
    return " ".join(title.split()[1 : 1 + count]) or None


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first("a.odkaz_detail[href]")
    m = AD_HREF.search(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    url = urljoin(BASE + "/vozy-prodej/", m[0])
    title = clean_spaces(node_value(box.css_first(".auto_nadpis")) or "")
    info: dict[str, str] = {}
    for item in box.css("span.rok"):
        label = node_value(item.css_first(".legenda"))
        value = clean_spaces(item.text(deep=False)) or node_value(item.css_first(".cena_vetsi"))
        if label and value:
            info[label] = value
    labels = {fold(node_value(t) or "") for t in box.css(".label_cross")}
    car = ParsedCar(
        external_id=m[2],
        title=title or "Auto",
        url=url,
        make=make_code(title),
        model=_model(title, m[1]),
        year=_number(info.get("Rok výroby")),
        mileage_km=_km(info.get("Najeto")),
        price=_number(info.get("Cena")),
        vat_deductible=True if "odpocet dph" in labels else None,
        fuel=fuel(cars.fuel_in_text(info.get("Palivo")), title),
        gearbox=gearbox(info.get("Převodovka")),
        power_kw=cars.power_in_text(title),
        condition="used",
        seller_kind="dealer",
    )
    image = box.css_first(".zaznam_profoto img")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [urljoin(BASE, src)]
    return Lead(m[2], url, url, car)


class Subaru(CrawlSource):
    id = SOURCE_ID
    label = "Subaru ojeté vozy"
    kind: SourceKind = "dealer"
    nationwide = True
    max_pages = 20

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css("div.zaznam") if (lead := parse_card(box))]
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def gone(self, html: str) -> bool:
        return GONE in html

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        title = clean_spaces(node_value(tree.css_first("h1.detail")) or "")
        if not title:
            return None
        info: dict[str, str] = {}
        note = ""
        after_note_label = False
        for row in tree.css("table.detail tr"):
            cells = row.css("td")
            if len(cells) == 2 and (label := node_value(cells[0])):
                info[label.rstrip(":").strip()] = node_value(cells[1]) or ""
            elif after_note_label and len(cells) == 1:
                note = node_text(cells[0])
            after_note_label = fold(clean_spaces(row.text())) == "poznamky"
        slug = m[1] if (m := AD_HREF.search(lead.url)) else ""
        origin = info.get("země původu")
        text = tree.css_first(".vybava-complete")
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description="\n\n".join(t for t in (node_text(text) if text else "", note) if t),
            make=make_code(title),
            model=_model(title, slug) if slug else None,
            year=_number(info.get("rok výroby")),
            mileage_km=_km(info.get("najeto")),
            price=_number(node_value(tree.css_first(".detail_cena"))),
            vat_deductible=True if _yes(info.get("odpočet DPH")) else None,
            fuel=fuel(cars.fuel_in_text(info.get("palivo")), title),
            gearbox=gearbox(info.get("převodovka")),
            body=cars.body_in_text(info.get("karoserie"))
            or ("other" if info.get("karoserie") else None),
            condition="used",
            power_kw=cars.power_in_text(info.get("výkon"), title),
            engine_cc=_number(info.get("objem")),
            color=info.get("barva") or None,
            doors=_number(info.get("počet dveří")),
            seats=_number(info.get("počet míst")),
            first_owner=_yes(info.get("1. majitel")),
            service_book=_yes(info.get("servisní knížka")),
            origin_country="Česká republika" if origin in ("ČR", "CZ") else origin or None,
            seller_kind="dealer",
        )
        photos = (a.attributes.get("href") for a in tree.css("#detail_pravak a.fancybox"))
        car.images = list(dict.fromkeys(urljoin(BASE, p) for p in photos if p))
        for p in tree.css("#detail_pravak p"):
            if p.css_first(".prodejce") is None:
                continue
            car.seller_name = node_value(p.css_first("strong"))
            # "Skladová 7, Plzeň, PSČ 32600", the line after the dealer's name.
            lines = [clean_spaces(x) for x in p.text(separator="\n").split("\n")]
            address = next((x for x in lines if "PSČ" in x), "")
            street, _, rest = address.partition(", ")
            town, _, psc = rest.rpartition(", PSČ ")
            if town:
                car.place_text = town
                car.location = places.resolve(town, street=street, postal_code=psc)
        return car


SOURCE = Subaru()
