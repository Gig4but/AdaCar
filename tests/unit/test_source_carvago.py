from pathlib import Path
from typing import cast

from adacar.core import ScrapeQuery
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import ListingPage
from adacar.scraper.sources import carvago

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "carvago"
SOURCE = carvago.SOURCE


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def listing(html: str, page: int = 1) -> ListingPage:
    return SOURCE.parse_listing(html, page, cast(PlaceResolver, None))  # cards need no places


def test_listing_cards():
    page = listing(read("listing.html"))
    assert page.has_next and not page.none_listed and len(page.leads) == 100
    lead = page.leads[3]
    assert lead.external_id == "88911129" and lead.fetch_url is None
    assert lead.url == "https://carvago.com/cs/auto/88911129/skoda-fabia-1-0-tsi-ambition-81-kw"
    car = lead.card
    assert car is not None and car.title == "Skoda Fabia 1.0 TSI Ambition 81 kW"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Fabia", 2020, 150839, 228490,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.condition) == (
        "petrol", "manual", "combi", 81, "used",
    )  # fmt: skip
    assert car.vat_deductible is True and car.seller_kind == "dealer"
    assert car.place_text == "Polsko" and car.location is None
    assert len(car.images) == 19 and car.images[0].startswith("https://storage.")
    first = page.leads[0].card
    assert first is not None and first.vat_deductible is None and first.place_text == "Německo"
    cards = [x.card for x in page.leads if x.card]
    assert {c.make for c in cards} == {"skoda"}
    assert max(c.price or 0 for c in cards) <= 300_000


def test_card_codes():
    cards = {x.external_id: x.card for x in listing(read("listing_all.html")).leads if x.card}
    volvo = cards["88917981"]
    assert (volvo.fuel, volvo.drive, volvo.body, volvo.color) == ("hybrid", "4x4", "combi", "Šedá")
    assert cards["83651072"].fuel == "electric" and cards["83651072"].power_kw == 283
    assert cards["88942637"].condition == "demo" and cards["88942547"].condition == "new"
    assert cards["88942600"].make == "mercedes-benz" and cards["88942600"].body == "van"
    assert cards["88913833"].mileage_km is None  # a new car: 0 km
    assert cards["88942727"].gearbox == "automatic" and cards["88942727"].vat_deductible is None


def test_last_page_and_listing_urls():
    page = listing(read("listing_last.html"), 10)
    assert not page.has_next and len(page.leads) == 37
    page = listing(read("listing_empty.html"), 11)
    assert not page.has_next and not page.leads and not page.none_listed
    assert listing("<html></html>").leads == []
    assert not SOURCE.default_enabled and SOURCE.expire_after_days and not SOURCE.newest_first
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None
    assert url(1) == "https://carvago.com/cs/auta?limit=100"
    narrowed = SOURCE.listings(
        ScrapeQuery(kraje=[], makes=["skoda", "land-rover"], max_price=300000)
    )
    assert [u(2) for _, u in narrowed] == [
        "https://carvago.com/cs/auta/skoda?price-to=300000&limit=100&page=2",
        "https://carvago.com/cs/auta/land-rover?price-to=300000&limit=100&page=2",
    ]


def test_robots_txt_allows_listings():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.search_url("skoda", 300000, 2))


def test_a_missing_count_is_no_answer():
    page = '<script id="__NEXT_DATA__">{"props": {"pageProps": {"searchResults": {}}}}</script>'
    listing = SOURCE.parse_listing(page, 1, None)  # type: ignore[arg-type]
    assert not listing.none_listed and not listing.has_next
