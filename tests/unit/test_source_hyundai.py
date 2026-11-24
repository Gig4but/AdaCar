from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import hyundai

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "hyundai"
SOURCE = hyundai.SOURCE


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def places(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        yield PlaceResolver(s)
    engine.dispose()


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing.html"), 1, places)
    assert page.has_next and len(page.leads) == 8  # a banner takes the ninth place
    lead = page.leads[0]
    assert lead.external_id == "1C4RDJDG6LC192844"
    assert (
        lead.url
        == lead.fetch_url
        == ("https://www.hyundai-promise.cz/dodge/durango/1c4rdjdg6lc192844")
    )
    car = lead.card
    assert car is not None and car.title == "Dodge Durango 3,6 Pentastar 216kW GT Plus"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "dodge", "Durango", 2020, 112569, 649900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.vat_deductible) == (
        "petrol", "automatic", 216, None,
    )  # fmt: skip
    assert car.seller_kind == "dealer" and car.place_text == "Jihomoravský kraj"
    assert car.location is not None and car.location.kraj == "Jihomoravský kraj"
    assert car.images[0].startswith("https://www.hyundai-promise.cz/data/hpromiseCar/")
    cards = {x.external_id: x.card for x in page.leads if x.card}
    jeep = page.leads[1]
    assert jeep.url == "https://www.hyundai-promise.cz/jeep/grand%20cherokee/1c4rjflt9jc119520"
    assert jeep.card is not None and jeep.card.model == "Grand Cherokee"
    assert jeep.card.fuel == "lpg" and jeep.card.location is not None
    assert jeep.card.location.municipality == "Praha"  # "Hl.m. Praha"
    assert cards["1G1FF1R74L0122226"].vat_deductible is True  # a price without VAT is shown
    assert cards["3C3CFFGE9DT604580"].fuel == "electric"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 151, places)
    assert not page.has_next and len(page.leads) == 8
    # A page past the end: no cars, and its "next" link goes back to page 2.
    assert not SOURCE.parse_listing(read("listing.html"), 999, places).has_next
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.hyundai-promise.cz/ojete-vozy"
    assert url(3) == "https://www.hyundai-promise.cz/ojete-vozy?strana=3"
    assert SOURCE.kind == "dealer" and SOURCE.label == "Hyundai Promise"


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[0]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "1C4RDJDG6LC192844"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "dodge", "Durango", 2020, 112569, 649900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("petrol", "automatic", "suv", "4x4")
    assert (car.power_kw, car.engine_cc, car.doors, car.seats) == (216, 3604, 5, 6)
    assert car.vin == "1C4RDJDG6LC192844" and car.color == "Modrá metalíza"
    assert car.vat_deductible is None and car.service_book is True
    assert car.origin_country == "Spojené státy americké"
    assert car.description.startswith("Vozidlo v minulosti havarované")
    assert car.seller_kind == "dealer" and car.seller_name == "PE Motors s.r.o."
    assert car.place_text == "Znojmo"
    assert len(car.equipment) == 66 and "Apple CarPlay" in car.equipment
    assert len(car.images) == 26 and car.images[0].startswith("https://www.hyundai-promise.cz/")


def test_detail_without_vin(places):
    lead = Lead("I2003600020112026", "u")
    car = SOURCE.parse_detail(read("detail_2.html"), lead, places)
    assert car is not None and car.title == "Hyundai i20 1,0 TGDI 74kW MT COMFORT"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "hyundai", "i20", 2025, 11708, 459000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("petrol", "manual", "hatchback", None)
    assert (car.power_kw, car.engine_cc, car.vat_deductible) == (74, 998, True)
    assert car.vin is None and car.description == ""
    assert car.seller_name == "Louda Auto a.s. (Praha)"
    assert car.place_text == "Praha 14 - Hloubětín"
    assert car.location is not None and car.location.municipality == "Praha"
    assert len(car.images) == 6
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_listing_and_cars():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_page(2))
    assert fetcher.allowed("https://www.hyundai-promise.cz/dodge/durango/1c4rdjdg6lc192844")
