"""caraukce.cz: CarAukce, online auctions and tenders ("elektronická soutěž") of used cars from
companies' fleets, leasing firms and others (~150 vehicles at a time, ~120 of them passenger
cars). Private persons and companies may bid after a free registration.

Server-rendered HTML; robots.txt disallows only /api/. One nationwide listing of the passenger
cars on offer, the soonest ending first (`/vozidla?druh=osobni&strana=<n>`, 24 per page; a page
past the end repeats the last one), read to its end on every run. A car's page is
`/item/<id>`.

The price is the guide price ("Orientační cena", with VAT); bids may end below or above it.
A card gives the title, year, mileage, fuel, gearbox, the guide price, the end of the
bidding and a photo. The detail adds the vehicle's register data (VIN, make and model, first
registration, engine, power, colour, seats, owners, service book, deductible VAT), the
equipment, the seller's note on its state and when it can be seen. The seller is not named,
so the auctioneer stands for it.
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
from adacar.scraper.sources.auction import auction_time, model_after_make, not_a_car

SOURCE_ID = "caraukce"
BASE = "https://www.caraukce.cz"
LISTING = BASE + "/vozidla?druh=osobni"
AD_PATH = re.compile(r"^/item/(\d+)$")
SELLER = "Car Aukce, spol. s.r.o."
# The register's fuel codes ("NM": motor diesel, "BA": petrol).
FUELS = {"nm": "diesel", "ba": "petrol", "lpg": "lpg", "cng": "cng", "el": "electric"}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _yes(text: str | None) -> bool | None:
    return {"ano": True, "ne": False}.get(fold(text or ""))


def gearbox(text: str | None) -> str | None:
    """ "automatická", "manuální"."""
    name = fold(text or "")
    if name.startswith("autom"):
        return "automatic"
    return "manual" if name.startswith("manu") else None


def fuel(facts: dict[str, str]) -> str | None:
    if _yes(facts.get("Elektrické vozidlo")):
        return "electric"
    code = fold(facts.get("Palivo") or "")
    named = FUELS.get(code) or cars.fuel_in_text(code)
    if _yes(facts.get("Hybridní vozidlo")):
        return "hybrid"
    return named


def card_fuel(*texts: str | None) -> str | None:
    """ "Benzín + Elektro" -> "hybrid"; else as the text names it."""
    folded = fold(" ".join(t for t in texts if t))
    if "elektro" in folded and re.search(r"benzin|nafta|diesel", folded):
        return "hybrid"
    return cars.fuel_in_text(*texts)


def _headline(kind: str | None, end: str | None) -> str:
    """ "Elektronická soutěž, konec 24. 11. 2026 14:00"."""
    when = auction_time(end) and re.match(r"(\d+)\.(\d+)\.(\d{4})\s+(\d+:\d\d)", end or "")
    end_text = f"{int(when[1])}. {int(when[2])}. {when[3]} {when[4]}" if when else None
    return ", ".join(x for x in (kind, f"konec {end_text}" if end_text else None) if x)


def parse_card(box: LexborNode) -> Lead | None:
    link = box.css_first(".vehicle-card__title a[href]")
    m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
    if link is None or m is None:
        return None
    url = urljoin(BASE, m[0])
    title = node_value(link) or "Auto"
    params = [node_value(li) or "" for li in box.css(".vehicle-card__params li")]
    year = next((int(p) for p in params if re.fullmatch(r"(19|20)\d\d", p)), None)
    km = next((_number(p) for p in params if p.endswith("km")), None)
    end = node_value(box.css_first(".vehicle-card__auction strong"))
    kind = node_value(box.css_first(".vehicle-card__flag"))
    car = ParsedCar(
        external_id=m[1],
        title=title,
        url=url,
        description=_headline(kind, end),
        make=make_code(title),
        model=model_after_make(title),
        year=year,
        mileage_km=km,
        price=_number(node_value(box.css_first(".vehicle-card__price strong"))),
        fuel=card_fuel(title, *params),
        gearbox=next((g for p in params if (g := gearbox(p))), None),
        seller_kind="dealer",
        seller_name=SELLER,
        expires_at=auction_time(end),
    )
    image = box.css_first(".vehicle-card__image img[src]")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [urljoin(BASE, src)]
    return Lead(m[1], url, url, car)


def _facts(tree: LexborHTMLParser) -> dict[str, str]:
    facts = {}
    for key in tree.css(".description_key"):
        value = key.next
        while value is not None and value.tag != "div":
            value = value.next
        name = (node_value(key) or "").rstrip(":")
        if name and value is not None and (text := node_value(value)):
            facts[name] = text
    return facts


def _section(tree: LexborHTMLParser, heading: str) -> str:
    """The text under an `h3` heading ("Prohlídky", "Stav vozidla")."""
    for h3 in tree.css("h3"):
        if node_value(h3) == heading and h3.parent is not None:
            text = node_text(h3.parent)
            return text.removeprefix(heading).strip()
    return ""


def _model(name: str | None) -> str | None:
    words = clean_spaces(name or "").split()
    while len(words) > 1 and cars.body_in_text(words[-1]):
        words.pop()
    return " ".join(words) or None


def parse_detail(html: str, lead: Lead) -> ParsedCar | None:
    tree = LexborHTMLParser(html)
    facts = _facts(tree)
    title = node_value(tree.css_first(".order-md-1 h2"))
    if not title or not facts:
        return None
    header = {
        node_value(row.css_first("td")): row.css_first("th")
        for row in tree.css(".header table tr")
        if row.css_first("td") is not None and row.css_first("th") is not None
    }
    kind = node_value(header.get("Typ"))
    end = node_value(header.get("Konec"))
    price = node_value(tree.css_first(".vyvolavaci_cena_value"))
    price_label = node_value(tree.css_first(".vyvolavaci_cena_title")) or "Orientační cena"
    state = _section(tree, "Stav vozidla")
    viewing = _section(tree, "Prohlídky")
    prices = f"{price_label} {price}" if price else ""
    equipment = tree.css_first("#equipment")
    car = ParsedCar(
        external_id=lead.external_id,
        title=title,
        url=lead.url,
        description="\n".join(
            x
            for x in (
                _headline(kind, end),
                prices,
                state,
                f"Prohlídky: {viewing}" if viewing else "",
            )
            if x
        ),
        make=make_code(facts.get("Tovární značka")) or make_code(title),
        model=_model(facts.get("Obchodní označení")) or model_after_make(title),
        year=_number((facts.get("První registrace") or "")[:4]),
        mileage_km=_number(facts.get("Stav ukazatele km")),
        price=_number(price),
        vat_deductible=True if _yes(facts.get("Odpočet DPH")) else None,
        fuel=fuel(facts),
        gearbox=gearbox(facts.get("Převodovka")),
        body=cars.body_in_text(title),
        power_kw=_number(facts.get("Výkon")),
        engine_cc=_number(facts.get("Objem")),
        color=(facts.get("Barva") or "").capitalize() or None,
        seats=_number(facts.get("Počet míst")),
        service_book=_yes(facts.get("Servisní knížka")),
        vin=(facts.get("VIN") or "").replace(" ", "").upper() or None,
        seller_kind="dealer",
        seller_name=SELLER,
        expires_at=auction_time(end),
    )
    if equipment is not None:
        lines = equipment.text(separator="\n").split("\n")
        car.equipment = [line for raw in lines if (line := clean_spaces(raw))]
    kind = fold(facts.get("Druh vozidla") or "")
    if kind and not kind.startswith("osobni"):
        car.extra["not_offer"] = "not_car"
    photos = (a.attributes.get("href") for a in tree.css("a.swipebox[href]"))
    car.images = list(dict.fromkeys(urljoin(BASE, p) for p in photos if p))
    return car


class CarAukce(CrawlSource):
    id = SOURCE_ID
    label = "CarAukce"
    kind: SourceKind = "auction"
    default_enabled = False
    nationwide = True
    max_pages = 30  # 5 pages today

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return LISTING + (f"&strana={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = [lead for box in tree.css(".vehicle-card") if (lead := parse_card(box))]
        return ListingPage(leads, has_page_link(tree, "strana", page + 1))

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        return not (lead.card and not_a_car(lead.card.title))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        return parse_detail(html, lead)


SOURCE = CarAukce()
