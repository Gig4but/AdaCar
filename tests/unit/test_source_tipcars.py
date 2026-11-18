from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import tipcars

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "tipcars"
SOURCE = tipcars.SOURCE
BMW = "https://www.tipcars.com/bmw-x5/suv/nafta/bmw-x5-40d-xdrive-tazne-masaz-55040028.html"


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


def test_robots_allows_the_pages_read():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    [(region, listing)] = SOURCE.listings(ScrapeQuery(kraje=["Kraj Vysočina"]))
    assert region is None
    assert listing(1) == "https://www.tipcars.com/ojete"
    assert listing(3) == "https://www.tipcars.com/ojete?str=3-20"
    assert rules.allowed(listing(1)) and rules.allowed(listing(3)) and rules.allowed(BMW)
    # The JSON API the pages call is not for robots.
    assert not rules.allowed("https://www.tipcars.com/cs/api/web/html/v1/offer/search/sales")


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing_1.html"), 1, places)
    assert page.has_next and len(page.leads) == 20
    lead = page.leads[0]
    car = lead.card
    assert car is not None
    assert lead.external_id == "47177425"
    assert lead.fetch_url == lead.url and lead.url.endswith("-47177425.html")
    assert car.title == "Volkswagen Golf TSi FULL SERVIS, ROZVODY"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volkswagen", "Golf", 2013, 137334, 208000,
    )  # fmt: skip
    assert (car.fuel, car.body, car.power_kw, car.engine_cc) == ("petrol", "hatchback", 90, 1395)
    assert car.seller_kind == "dealer" and car.seller_name == "AUTOBAZAR BALANTERO s.r.o."
    assert car.place_text == "Uničov"
    bmw = page.leads[1].card
    assert bmw is not None
    assert bmw.vat_deductible is True and bmw.gearbox == "automatic"
    assert bmw.location is not None and bmw.location.municipality == "Praha"
    assert bmw.images
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["55577703"].model == "Řada 6"  # "BMW Řada 6"
    assert cards["55974177"].engine_cc is None  # electric: "1"
    assert cards["55974177"].seller_name == "Direct auto"  # no town given


def test_next_page(places):
    page = SOURCE.parse_listing(read("listing_2.html"), 2, places)
    assert page.has_next and len(page.leads) == 20


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("55040028", BMW), places)
    assert car is not None
    assert car.title == "BMW X5 40d xDrive, Tažné, Masáž"
    assert (car.make, car.model, car.year, car.mileage_km) == ("bmw", "X5", 2026, 6354)
    assert car.price == 2569000 and car.vat_deductible is True
    assert (car.fuel, car.gearbox, car.body, car.condition) == (
        "diesel",
        "automatic",
        "suv",
        "used",
    )
    assert (car.power_kw, car.engine_cc, car.doors, car.seats) == (231, 2993, 5, 5)
    assert car.color == "zelená metalíza" and car.vin is None  # "Není k dispozici"
    assert car.seller_kind == "dealer" and car.seller_name == "MACH MOTORS s.r.o."
    assert car.location is not None and car.location.municipality == "Praha"
    assert "Mach Motors" in car.description
    assert "kožené čalounění" in car.equipment and len(car.images) == 12


def test_detail_without_seller(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("100171898", "u"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.price) == ("volvo", "XC40", 2021, 490000)
    assert car.seller_name is None and car.location is None
