"""autoaukce.cz: AutoAukce, online car auctions where companies and private persons sell their
own cars (a handful running at a time, often classics and imports). Anyone registered may bid;
most sales need the seller's approval of the highest bid ("Prodej podléhá schválení").

Server-rendered HTML behind Cloudflare (no challenge for us); robots.txt is empty. The running
auctions are all on the home page ("Aktuální aukce", no paging while they are this few); its
cards link only to a sign-in box, so the car's page is built from the card's id:
`/detail/<id>-<slug>` (any slug works; a gone auction answers 404). The listing is read whole
on every run.

The price is the current price: the starting bid until someone bids, then the highest bid;
the buyer also pays the portal's fee. A card gives the make and model, the version, year,
mileage, fuel, power, gearbox, price, the end of the auction and a photo. The detail adds the
seller's text, body, drive, colour, where the car is, the seller, photos and the VIN (in a
link to a history check; the table hides it).
"""

import re
from datetime import UTC, datetime

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text, slugify
from adacar.normalize.vocab import make_code, make_end
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.auction import (
    auction_time,
    czech_time,
    model_after_make,
    not_a_car,
    utc_to_prague,
)

SOURCE_ID = "autoaukce"
BASE = "https://www.autoaukce.cz"
CARD_ID = re.compile(r"^snippet-auctionCard-(\d+)-")
COMPANY = re.compile(r"\b(?:s\.\s?r\.\s?o|a\.\s?s|spol|v\.\s?o\.\s?s|k\.\s?s)\b\.?", re.IGNORECASE)
APPROVAL = "Prodej podléhá schválení"
DRIVES = {"4x4": "4x4", "zadni": "rear", "predni": "front"}
VIN_PARAM = re.compile(r"[?&]vin=([A-Za-z0-9]{17})")


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _utc(stamp: str | None) -> datetime | None:
    """ "2026-11-27T11:15:00+00:00" as naive UTC."""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).astimezone(UTC).replace(tzinfo=None)
    except ValueError:
        return None


def gearbox(text: str | None) -> str | None:
    name = fold(text or "")
    if "autom" in name:
        return "automatic"
    return "manual" if "manu" in name else None


def _name(heading: LexborNode | None) -> tuple[str, str]:
    """ "Tesla Model Y <small>SR LFP DPH</small>" -> ("Tesla Model Y", "SR LFP DPH")."""
    if heading is None:
        return "", ""
    small = heading.css_first("small")
    version = node_value(small) or ""
    name = clean_spaces(heading.text(deep=False) or "") or (node_value(heading) or "")
    return name, version


def _model(name: str) -> str | None:
    """ "Tesla Model Y" -> "Model Y", "BMW Řada 3" -> "Řada 3": the make, then the model."""
    end = make_end(name)
    if end is not None and make_code(name.partition(" ")[0]):
        return name[end:].strip(" -") or None
    return model_after_make(name)


def parse_card(box: LexborNode) -> Lead | None:
    m = CARD_ID.match(box.attributes.get("id") or "")
    name, version = _name(box.css_first(".auction-card__title"))
    if m is None or not name:
        return None
    external_id = m[1]
    url = f"{BASE}/detail/{external_id}-{slugify(name)}"
    meta = [node_value(span) or "" for span in box.css(".auction-card__meta span")]
    end = box.css_first(".countdown")
    expires = _utc(end.attributes.get("data-value") if end is not None else None)
    badges = [node_value(b) or "" for b in box.css(".auction-card__badge")]
    title = clean_spaces(f"{name} {version}")
    car = ParsedCar(
        external_id=external_id,
        title=title,
        url=url,
        make=make_code(name),
        model=_model(name),
        year=next((int(x) for x in meta if re.fullmatch(r"(19|20)\d\d", x)), None),
        mileage_km=next((_number(x) for x in meta if x.endswith("km")), None),
        price=_number(node_value(box.css_first(".text-primary"))),
        fuel=cars.fuel_in_text(*meta, version),
        gearbox=next((g for x in meta if (g := gearbox(x))), None),
        power_kw=cars.power_in_text(*meta),
        expires_at=expires,
    )
    car.description = _headline(expires, None, APPROVAL in badges)
    image = box.css_first("img[src]")
    if image is not None and (src := image.attributes.get("src")):
        car.images = [src]
    return Lead(external_id, url, url, car)


def _headline(end: datetime | None, place: str | None, approval: bool) -> str:
    """ "Aukce do 27. 11. 2026 12:15, Praha (prodej podléhá schválení)"; `end` in UTC."""
    when = f"Aukce do {czech_time(utc_to_prague(end))}" if end else "Aukce"
    text = ", ".join(x for x in (when, place) if x)
    return text + (" (prodej podléhá schválení)" if approval else "")


def parse_detail(html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
    tree = LexborHTMLParser(html)
    name, version = _name(tree.css_first("h1"))
    facts = {
        node_value(row.css_first("th")): node_value(row.css_first("td"))
        for row in tree.css("tr")
        if row.css_first("th") is not None and row.css_first("td") is not None
    }
    if not name or "Značka" not in facts:
        return None
    text_node = tree.css_first("#auctionDescription")
    text = node_text(text_node) if text_node is not None else ""
    end_node = tree.css_first(".auction-end-formatted")
    end = auction_time(node_value(end_node))
    labels = (n for n in tree.css("div") if (n.text(deep=False) or "").strip() == "Aktuální cena")
    price_box = next((n.parent for n in labels), None)
    seller = node_value(tree.css_first(".h6.d-block"))
    # The table hides the VIN, the link to a history check has it.
    check = tree.css_first("a[href*='vin=']")
    vin = VIN_PARAM.search(check.attributes.get("href") or "") if check is not None else None
    if vin and set(vin[1].upper()) == {"X"}:
        vin = None
    place = facts.get("Umístění vozidla")
    car = ParsedCar(
        external_id=lead.external_id,
        title=clean_spaces(f"{name} {version}"),
        url=lead.url,
        make=make_code(facts.get("Značka")) or make_code(name),
        model=facts.get("Model") or _model(name),
        year=_number(facts.get("Rok výroby")),
        mileage_km=_number(facts.get("Najeto")),
        price=lead.card.price if lead.card else None,
        vat_deductible=True if "možnost odpočtu DPH" in html else None,
        fuel=cars.fuel_in_text(facts.get("Palivo"), version),
        gearbox=gearbox(facts.get("Převodovka")),
        body=cars.body_in_text(facts.get("Karosérie"), version),
        drive=DRIVES.get(fold(facts.get("Pohon") or "")),
        power_kw=cars.power_in_text(facts.get("Výkon")),
        engine_cc=_number(facts.get("Objem")) or None,
        color=facts.get("Barva"),
        vin=vin[1].upper() if vin else None,
        seller_kind="dealer" if seller and COMPANY.search(seller) else None,
        seller_name=seller,
        expires_at=end,
    )
    if price_box is not None:
        amounts = re.findall(r"\d{1,3}(?:\s\d{3})+|\d+", price_box.text(separator=" "))
        car.price = _number(amounts[0]) if amounts else car.price
    if place:
        car.place_text = place
        car.location = places.resolve(place)
    bid = tree.css_first("#snippet--bid")
    approval = bid is not None and APPROVAL in bid.text()
    car.description = "\n".join(x for x in (_headline(end, place, approval), text) if x)
    photos = (a.attributes.get("href") for a in tree.css("a.glightbox[href]"))
    car.images = list(dict.fromkeys(p for p in photos if p))
    return car


class AutoAukce(CrawlSource):
    id = SOURCE_ID
    label = "AutoAukce"
    kind: SourceKind = "auction"
    nationwide = True
    max_pages = 1  # the running auctions fit on the home page

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.listing_page)]

    @staticmethod
    def listing_page(page: int) -> str:
        return BASE + "/"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        section = tree.css_first("#snippet--auctions")
        boxes = section.css(".auction-card[id]") if section is not None else []
        leads = list({lead.external_id: lead for b in boxes if (lead := parse_card(b))}.values())
        return ListingPage(leads, has_next=False)

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        return not (lead.card and not_a_car(lead.card.title))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        return parse_detail(html, lead, places)


SOURCE = AutoAukce()
