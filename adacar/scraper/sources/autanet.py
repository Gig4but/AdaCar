"""autanet.cz: dealers' cars (~25 000 passenger cars, no private ads), HTML only.

robots.txt disallows every URL with "/?" (the search form's filters), so each make's own
listing is walked (`/osobni-vozidla/<make>?page=<n>`, ~20 cars per page, newest first,
the site's default order) and stops at the first page with no new car. The whole-country
listing without a make shows no results, only tips.

A card has nearly everything the filters use: title, price, year, mileage, fuel, gearbox
and region. The detail page adds VIN, power, engine, body, colour, VAT, text, equipment,
photos and the dealer's address. Dealers' cars stay listed until sold, so a car unseen for
a while is checked again: a sold car's page is gone (404).
"""

import re
from functools import partial
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.text import card_date, clean_spaces, node_text, slugify
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "autanet"
BASE = "https://www.autanet.cz"
# Make slugs of the site's make filter ("ostatni": the rest).
MAKES = (
    "abarth", "aixam", "alfa-romeo", "alpina", "alpine", "aston-martin", "audi", "austin", "baic",
    "bentley", "bmw", "buick", "byd", "cadillac", "chery", "chevrolet", "chrysler", "citroen",
    "cupra", "dacia", "daihatsu", "dodge", "dongfeng", "ds-automobiles", "ferrari", "fiat",
    "ford", "forthing", "foton", "genesis", "gmc", "great-wall", "honda", "hummer", "hyundai",
    "infiniti", "isuzu", "jac", "jaecoo", "jaguar", "jeep", "kaipan", "kgm", "kia", "ktm", "lada",
    "lamborghini", "lancia", "land-rover", "leapmotor", "lexus", "ligier", "lincoln", "lotus",
    "lynk-co", "mahindra", "maserati", "maxus", "mazda", "mclaren", "mercedes-benz", "mg", "mini",
    "mitsubishi", "moskvic", "nio", "nissan", "omoda", "opel", "ostatni", "peugeot", "pontiac",
    "porsche", "ram", "renault", "rolls-royce", "rover", "saab", "seat", "skoda", "smart",
    "ssangyong", "subaru", "suzuki", "tata", "tesla", "toyota", "trabant", "volkswagen", "volvo",
    "voyah",
)  # fmt: skip
SLUG_MAKES = {"ds-automobiles": "ds", "ostatni": None}  # slugs that are not a make code
AD_PATH = re.compile(r"^/detail/[a-z0-9-]+-(\d+)$")
FUELS = {"benzin": "petrol", "nafta": "diesel", "cng": "cng", "lpg": "lpg",
         "elektro": "electric", "ethanol": "other"}  # fmt: skip
BODIES = {"cuv": "suv", "suv": "suv", "hatchback": "hatchback", "kabriolet": "cabrio",
          "kombi": "combi", "kupe": "coupe", "liftback": "liftback", "limuzina": "sedan",
          "sedan": "sedan", "mpv": "mpv", "minibus": "van", "van": "van", "pick up": "pickup",
          "terenni": "offroad", "ostatni": "other"}  # fmt: skip
_INT = re.compile(r"\d[\d\s]*")
VIN = re.compile(r"(?=.*\d)[A-Z0-9]{11,17}")  # not "NEUVEDENO" (not given)


def _int(text: str | None) -> int | None:
    m = _INT.search(clean_spaces(text or ""))
    return int(re.sub(r"\D", "", m[0])) if m else None


def slug_make(slug: str) -> str | None:
    if slug in SLUG_MAKES:
        return SLUG_MAKES[slug]
    return make_code(slug) or slug  # makes outside the vocabulary keep the site's name


def fuel(value: str | None, title: str) -> str | None:
    folded = slugify(value or "").replace("-", " ")
    if folded.startswith("hybrid"):
        return "plugin_hybrid" if cars.fuel_in_text(title) == "plugin_hybrid" else "hybrid"
    return FUELS.get(folded) if folded else None


def gearbox(value: str | None) -> str | None:
    """ "automatická", "manuální", "poloautomatická", "neuvedeno"."""
    folded = slugify(value or "")
    if "aut" in folded:
        return "automatic"
    return "manual" if folded.startswith("manual") else None


def body(value: str | None) -> str | None:
    folded = slugify(value or "").replace("-", " ")
    return BODIES.get(folded, "other") if folded else None


def make_and_model(main: str) -> tuple[str | None, str | None]:
    """ "Škoda Superb" -> ("skoda", "Superb"); "Land Rover Range Rover" -> ("land-rover", …)."""
    words = main.split()
    for n in (3, 2, 1):  # the longest make name first: "Alfa Romeo", "Lynk & Co"
        if len(words) >= n and (slug := slugify(" ".join(words[:n]))) in MAKES:
            return slug_make(slug), " ".join(words[n:]) or None
    return make_code(main), " ".join(words[1:]) or None


def _photo(src: str | None) -> str | None:
    if not src or "no-picture" in src:
        return None
    return urljoin(BASE, src)


class Autanet(CrawlSource):
    id = SOURCE_ID
    label = "Autanet.cz"
    newest_first = True
    nationwide = True
    # The largest make (Škoda) has ~270 pages: a first run reads each make to its end once.
    max_pages = 300
    recheck_after_days = 14

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        wanted = set(query.makes)
        return [
            (None, partial(self.make_url, slug))
            for slug in MAKES
            if not wanted or slug_make(slug) is None or slug_make(slug) in wanted
        ]

    @staticmethod
    def make_url(slug: str, page: int) -> str:
        return f"{BASE}/osobni-vozidla/{slug}" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("article.basic-3"):
            link = card.css_first(".basic-3__title a")
            m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
            if link is None or m is None:
                continue
            main = node_value(card.css_first(".basic-3__title-main")) or ""
            kind = node_value(card.css_first(".basic-3__title-type"))
            title = clean_spaces(f"{main} {kind or ''}")
            specs = {
                node_value(s.css_first(".basic-3__spec-label")): node_value(
                    s.css_first(".basic-3__spec-value")
                )
                for s in card.css(".basic-3__spec")
            }
            make, model = make_and_model(main)
            region = node_value(card.css_first(".basic-3__locality"))
            badge = (
                (node_value(card.css_first(".vehicle-new-badge")) or "")
                .removeprefix("Přidáno")
                .strip()
            )
            url = urljoin(BASE, m[0])
            car = ParsedCar(
                external_id=m[1],
                title=title,
                url=url,
                make=make,
                model=model,
                year=_int(specs.get("Rok výroby")),
                mileage_km=_int(specs.get("Tachometr")),
                price=price(node_value(card.css_first(".basic-3__price"))),
                fuel=fuel(specs.get("Palivo"), title),
                gearbox=gearbox(specs.get("Převodovka")),
                seller_kind="dealer",
                place_text=region,
                location=places.resolve(region) if region else None,
                posted_at=card_date(badge),
                equipment=[h.text(strip=True) for h in card.css(".basic-3__highlight")],
            )
            image = card.css_first(".basic-3__image img")
            if image is not None and (photo := _photo(image.attributes.get("src"))):
                car.images = [photo]
            leads.append(Lead(m[1], url, url, car))
        next_page = re.compile(rf"[?&]page={page + 1}(?![0-9])")
        has_next = any(next_page.search(a.attributes.get("href") or "") for a in tree.css("a"))
        return ListingPage(leads, has_next)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        tree = LexborHTMLParser(html)
        main = node_value(tree.css_first(".detail__vehicle-title-main"))
        if not main:
            return None
        kind = node_value(tree.css_first(".detail__vehicle-title-type"))
        title = clean_spaces(f"{main} {kind or ''}")
        info = {
            clean_spaces(item.text(deep=False)): node_value(item.css_first("strong"))
            for item in tree.css(".detail__info-item")
        }
        table: dict[str, str | None] = {}
        for row in tree.css(".detail__columns tr"):
            th, td = row.css_first("th"), row.css_first("td")
            if th is not None and td is not None:
                table.setdefault(clean_spaces(th.text()), node_value(td))
        description, equipment = "", []
        for more in tree.css(".detail__more-content"):
            items = more.css("li")
            if items:
                equipment = [t for li in items if (t := clean_spaces(li.text()))]
            elif not description:
                description = node_text(more)
        make, model = make_and_model(main)
        car = ParsedCar(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=description,
            make=make,
            model=model,
            year=_int(info.get("Rok výroby")),
            mileage_km=_int(table.get("Tachometr") or info.get("Tachometr")),
            price=price(node_value(tree.css_first(".detail__price"))),
            vat_deductible=_yes(table.get("Odpočet DPH")),
            fuel=fuel(table.get("Druh paliva") or info.get("Palivo"), title),
            gearbox=gearbox(info.get("Převodovka")),
            body=body(table.get("Karoserie") or info.get("Karoserie")),
            power_kw=_int(info.get("Výkon")),
            engine_cc=_int(table.get("Objem")),
            color=table.get("Barva"),
            doors=_int(table.get("Počet dveří")),
            seats=_int(table.get("Počet míst")),
            first_owner=_yes(table.get("První majitel")),
            origin_country=table.get("Země původu"),
            vin=vin if VIN.fullmatch(vin := (table.get("VIN") or "").upper()) else None,
            seller_kind="dealer",
            equipment=equipment,
            images=[
                p
                for s in tree.css("[data-gallery-source]")
                if (p := _photo(s.attributes.get("data-src")))
            ],
        )
        dealer = tree.css_first(".dealer:not(.dealer--mobile)") or tree.css_first(".dealer")
        if dealer is not None:
            car.seller_name = node_value(dealer.css_first("h3"))
            set_place(car, dealer.css_first("p"), places)
        return car


def _yes(value: str | None) -> bool | None:
    return {"ano": True, "ne": False}.get((value or "").strip().lower())


def price(text: str | None) -> int | None:
    found = cars.parse_price(text)
    return found[0] if found and found[1] == "CZK" else None


def set_place(car: ParsedCar, address: LexborNode | None, places: PlaceResolver) -> None:
    """ "Družstevní 3894/28, Znojmo<br>Jihomoravský kraj"; small places add the district:
    "Zátor 256, Zátor, okres Bruntál"."""
    if address is None:
        return
    lines = [x for p in address.text(separator="\n").split("\n") if (x := clean_spaces(p))]
    if not lines:
        return
    parts = [p.strip() for p in lines[0].split(",") if p.strip()]
    region = lines[1] if len(lines) > 1 else None
    street, town = (parts[0], parts[1]) if len(parts) > 1 else (None, parts[0])
    district = next((p[6:] for p in parts[2:] if p.startswith("okres ")), None)
    car.place_text = town
    car.location = places.resolve(town, street=street, region=region, district=district)
    if car.location is None and region:
        car.location = places.resolve(region)


SOURCE = Autanet()
