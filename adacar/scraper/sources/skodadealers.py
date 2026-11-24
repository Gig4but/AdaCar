"""<dealer>.skoda-auto.cz/ojete-vozy: the used cars of Škoda's Czech dealers (Škoda Plus and
the trade-ins they sell, ~11 500 cars at 113 dealers), each on the dealer's own microsite. The
microsites run on one platform; their robots.txt allows everything.

Škoda's central used-car search (skodaplus.cz/list) reads a GraphQL API that answers only
POST requests, and a microsite's listing loads its pages past the first by POST as well. So
each dealer's `sitemap.xml` is its listing: one request lists every used car the dealer offers
(`/ojete-vozy/<model>/<vin>/<id>`) with the day it last changed (so a car is read again on the
runs until that day is over), and a car the sitemap no longer lists is gone. Only the dealers
with a branch in the user's regions are read.

A sitemap gives no card: a car appears once its detail page (server-rendered HTML, ~0.5 MB)
is read. The detail has make, model, year, mileage, price (and a price without VAT when the
VAT is deductible), fuel, gearbox, power, engine size, colour, VIN, first owner, service
book, origin, the dealer's note, equipment, a few photos and the branch the car stands at.
"""

import re
from datetime import UTC, datetime, timedelta
from functools import partial

from selectolax.lexbor import LexborHTMLParser

from adacar.core import ParsedCar, ScrapeQuery, SourceKind
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import clean_spaces, fold, node_text
from adacar.normalize.vocab import make_code, make_end
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adacar.scraper.sources.bazos import MAKE_WORDS

SOURCE_ID = "skodadealers"
SITEMAP = "https://{host}.skoda-auto.cz/sitemap.xml"
CAR_URL = re.compile(
    r"<loc>(https://[a-z0-9.-]+\.skoda-auto\.cz/ojete-vozy/[^/<]+/[a-z0-9]+/(\d+))</loc>"
    r"\s*(?:<lastmod>([^<]+)</lastmod>)?"
)
# The region codes of Škoda's dealer list.
REGIONS = {"A": "Hlavní město Praha", "S": "Středočeský kraj", "C": "Jihočeský kraj",
           "P": "Plzeňský kraj", "K": "Karlovarský kraj", "U": "Ústecký kraj",
           "L": "Liberecký kraj", "H": "Královéhradecký kraj", "E": "Pardubický kraj",
           "J": "Kraj Vysočina", "B": "Jihomoravský kraj", "M": "Olomoucký kraj",
           "T": "Moravskoslezský kraj", "Z": "Zlínský kraj"}  # fmt: skip
# Dealer microsites with used cars -> the regions of their branches, from Škoda's dealer
# locator (www.skoda-auto.cz/apps/retailers/api/260/cs-CZ/DealersV2/GetDealers, and each
# dealer's GetDealerDetail?id=<globalId> for its "webUrl") in November 2026. The locator's
# 237 branches have 140 microsites; the other 27 have no used-car pages.
HOSTS = {
    "adiv": "T", "agrotec": "B", "amond": "S", "araver": "BZ", "arservis": "C", "astra": "L",
    "auto-cb": "P", "auto-in": "BH", "auto-styl": "A", "auto3tecky": "J", "autobrandys": "S",
    "autocentrala": "T", "autocentrum-jicin": "H", "autocentrum-prerov": "M",
    "autocentrum-ros": "B", "autocentrum-ta": "P", "autochlumec": "H", "autodruzstvo-fm": "T",
    "autodruzstvo-znojmo": "B", "autodum-vrana": "T", "autodum": "U", "autoklever": "T",
    "automechanika": "M", "autonord": "LU", "autonova": "B", "autoplusmost": "U", "autos": "B",
    "autoservisjindra": "C", "autospektrum2000": "K", "autospol-plus": "P", "autosport": "L",
    "autostop": "S", "autostyl": "H", "autotrend": "L", "autozitka": "S", "az-servis": "B",
    "barth": "EH", "bayer": "B", "beno": "S", "branka": "AH", "brejla": "AS", "cb-auto": "C",
    "cerveny": "K", "color-design": "T", "da": "AB", "dobe-car": "Z", "dobrovolny": "J",
    "drupol": "S", "dryml": "E", "dubina": "T", "elso": "A", "evzen-myslivec": "SU", "f3k": "J",
    "femat": "A", "frolik": "C", "gremos": "L", "havex": "AHS", "hegr": "M", "hlavacek": "EM",
    "horacke-autodruzstvo-trebic": "J", "horacke-autodruzstvo-vm": "J", "hybes": "E",
    "inpro": "S", "ivacar2000": "B", "jarov": "A", "je-ne": "B", "kapl": "C", "karireal": "T",
    "klokocka": "A", "kontakt-turnov": "L", "koutek": "L", "kralupy": "S", "kudrna": "T",
    "laureta": "S", "louda": "ABEJSU", "lukas": "TZ", "magnum": "B", "matrix": "H",
    "mencik": "AS", "mercia": "E", "myslivec": "U", "nedved": "P", "novotny": "C", "okim": "U",
    "olfin-car": "H", "opat": "A", "palace": "A", "paulus": "Z", "podbabska": "A", "poly": "AS",
    "porsche": "ABCHMPT", "pp-autocentrum": "M", "prerost-a-svorc": "A", "pvauto": "M",
    "racek": "J", "samohyl": "MZ", "schoner": "K", "sevcik": "C", "sidlo": "S", "smucler": "P",
    "srachta": "C", "stepanek": "A", "strz": "A", "suchy": "B", "toman": "T",
    "top-autosalon": "B", "tukas": "A", "ul": "U", "unicar": "T", "v-auto-zebrak": "S",
    "verold-benesov": "S", "vista": "B", "volf": "P",
}  # fmt: skip
ORIGINS = {"CZ": "Česká republika"}
VIN_LABEL = "Identifikační číslo vozidla (VIN)"


def _km(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) or None if digits else None  # an electric car's "0 cm³"


def _yes(text: str | None) -> bool | None:
    return {"ano": True, "ne": False}.get(fold(text or ""))


def modified(text: str | None) -> datetime | None:
    """A sitemap's "2026-11-19T01:00:00+01:00" (a day: every car's time is midnight) as the
    naive UTC time the store keeps, at the end of that day."""
    try:
        when = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    if when is None:
        return None
    when = when.astimezone(UTC).replace(tzinfo=None) if when.tzinfo else when
    return when + timedelta(days=1)


def model_name(name: str, make: str | None) -> str | None:
    """ "Škoda Octavia" -> "Octavia", "Alfa Romeo Giulia" -> "Giulia"."""
    end = make_end(name)
    words = name[end:].strip(" -,").split() if end is not None else []
    while words and fold(words[0]) in MAKE_WORDS:
        words.pop(0)
    return " ".join(words) or (None if end is not None else name or None)


def head_params(tree: LexborHTMLParser) -> dict[str, str]:
    """The values under the title, by their icon: "engine" -> "2.0 85kW", "date" -> "2024"."""
    found: dict[str, str] = {}
    for item in tree.css(".stockcar-head__param"):
        icon = item.css_first(".icon-svg")
        kind = (
            re.search(r"icon-svg--([a-z-]+)", icon.attributes.get("class") or "") if icon else None
        )
        if kind and (value := clean_spaces(item.text())):
            found.setdefault(kind[1], value)
    return found


def summary(tree: LexborHTMLParser) -> dict[str, str]:
    """The "Souhrn" table: "Barva" -> "stříbrná", "Původ" -> "CZ", ..."""
    found: dict[str, str] = {}
    for row in tree.css(".car-info table tr"):
        cells = row.css("td")
        if len(cells) == 2 and (label := clean_spaces(cells[0].text())):
            found.setdefault(label, clean_spaces(cells[1].text()))
    return found


def note(tree: LexborHTMLParser) -> str:
    """The text of the folding section "Poznámka" (the dealer's note)."""
    for title in tree.css(".accordeon__title-text"):
        label = title.parent.parent if title.parent else None
        if clean_spaces(title.text()) != "Poznámka" or label is None:
            continue
        body = label.next
        while body is not None and "accordeon__body" not in (body.attributes.get("class") or ""):
            body = body.next
        return node_text(body) if body is not None else ""
    return ""


def price(tree: LexborHTMLParser) -> tuple[int | None, bool | None]:
    """The price, and True when a price without VAT is shown next to it (deductible VAT)."""
    block = tree.css_first(".stockcar-head__prices")
    if block is None:
        return None, None
    prices = block.css(".stockcar-head__price")
    gross = next((p for p in prices if "--vat" not in (p.attributes.get("class") or "")), None)
    net = block.css_first(".stockcar-head__price--vat")
    return _number(node_value(gross)), True if net and "dph" in fold(net.text()) else None


def place(car: ParsedCar, branch: str | None, places: PlaceResolver) -> None:
    """ "Přerost a Švorc - auto, s.r.o., Praha 6, Veleslavínská 39": dealer, town, street."""
    parts = [p.strip() for p in (branch or "").split(",")]
    if len(parts) < 3:
        car.seller_name = clean_spaces(branch or "") or None
        return
    car.seller_name, car.place_text = ", ".join(parts[:-2]), parts[-2]
    # "Praha 10 - Strašnice": the resolver reads city parts written "Praha 10-Strašnice".
    car.location = places.resolve(re.sub(r"\s+-\s+", "-", parts[-2]), street=parts[-1])


def photos(tree: LexborHTMLParser, url: str) -> list[str]:
    """This car's photos at the top of its page (cards of other cars show theirs), without
    the image server's size options."""
    folder = "/" + url.rstrip("/").split("/")[-2] + "/"
    sources = (
        node.attributes.get("srcset") or node.attributes.get("src") or ""
        for node in tree.css(".stockcar-head source, .stockcar-head img")
    )
    found = (s.strip().split(" ")[0].split("?")[0] for src in sources for s in src.split(","))
    return list(dict.fromkeys(u for u in found if folder in u))


class SkodaDealers(CrawlSource):
    id = SOURCE_ID
    label = "Škoda Plus (prodejci Škoda)"
    kind: SourceKind = "dealer"
    max_pages = 1  # a sitemap lists all of a dealer's cars

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        """A dealer's sitemap once for each of its regions in scope: a region counts as read
        to the end only when every dealer with a branch there was."""
        return [
            (REGIONS[code], partial(self.sitemap_url, host))
            for host, codes in HOSTS.items()
            for code in codes
            if REGIONS[code] in query.kraje
        ]

    @staticmethod
    def sitemap_url(host: str, page: int) -> str:
        return SITEMAP.format(host=host)

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        leads = [Lead(m[2], m[1], m[1], modified=modified(m[3])) for m in CAR_URL.finditer(html)]
        # A dealer with no used car on offer still has a sitemap of its other pages.
        return ListingPage(leads, has_next=False, none_listed="<urlset" in html and not leads)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        name = node_value(tree.css_first(".stockcar-head__title"))
        if not name:
            return None
        head, info = head_params(tree), summary(tree)
        engine = head.get("engine") or ""
        gross, vat = price(tree)
        origin = info.get("Původ") or None
        state = info.get("Stav vozu")
        car = ParsedCar(
            external_id=lead.external_id,
            title=clean_spaces(f"{name} {engine}"),
            url=lead.url,
            description="\n\n".join(t for t in (note(tree), state and f"Stav vozu: {state}") if t),
            make=make_code(name),
            year=_number(head.get("date")),
            mileage_km=_km(head.get("speedometer")),
            price=gross,
            vat_deductible=vat,
            fuel=cars.fuel_in_text(head.get("gas-station")),
            gearbox=cars.gearbox_in_text(info.get("Převodovka"), head.get("transmission")),
            body=cars.body_in_text(name),  # only a model name like "Fabia Combi" tells
            drive="4x4" if "4x4" in fold(head.get("transmission") or "") else None,
            condition="demo" if _yes(info.get("Předváděcí vůz")) else "used",
            power_kw=cars.power_in_text(engine),
            engine_cc=_number(info.get("Objem motoru")),
            color=info.get("Barva") or None,
            first_owner=_yes(info.get("První majitel")),
            service_book=_yes(info.get("Servisní knížka")),
            origin_country=ORIGINS.get(origin, origin) if origin else None,
            vin=(info.get(VIN_LABEL) or "").upper() or None,
            seller_kind="dealer",
            equipment=[
                t for li in tree.css(".car-info .accordeon li") if (t := clean_spaces(li.text()))
            ],
            images=photos(tree, lead.url),
        )
        car.model = model_name(name, car.make)
        place(car, node_value(tree.css_first(".car-info__contact strong")), places)
        phone = tree.css_first('.car-info__contact a[href^="tel:"]')
        if phone is not None and (number := clean_spaces(phone.text())):
            car.contact["phone"] = number
        return car


SOURCE = SkodaDealers()
