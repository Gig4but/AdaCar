from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autovolf

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autovolf"
SOURCE = autovolf.SOURCE
BASE = "https://www.autovolf.cz/vuz-skladem/"


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
    # 12 cards, 2 of them operating-lease offers without a price.
    assert page.has_next and len(page.leads) == 10
    assert "8897" not in {x.external_id for x in page.leads}
    lead = page.leads[0]
    assert lead.external_id == "9404"
    assert lead.url == lead.fetch_url == BASE + "9404/skoda-octavia-ambition-1-6-tdi-77-kw"
    car = lead.card
    assert car is not None and car.title == "Škoda Octavia Ambition 1.6 TDi"
    assert (car.make, car.model, car.price, car.fuel, car.power_kw) == (
        "skoda", "Octavia", 154900, "diesel", 77,
    )  # fmt: skip
    assert (car.gearbox, car.drive, car.body, car.condition) == (
        "manual",
        "front",
        "combi",
        "used",
    )
    assert car.vat_deductible is None and car.year is None
    assert car.seller_kind == "dealer" and car.seller_name == "Auto Volf"
    assert car.images and car.images[0].startswith("https://www.autovolf.cz/share/cache/image/")


def test_vat_demo_and_last_page(places):
    page = SOURCE.parse_listing(read("listing_16.html"), 16, places)
    assert page.has_next and len(page.leads) == 10
    cards = {x.external_id: x.card for x in page.leads if x.card}
    kodiaq = cards["8039"]
    assert (kodiaq.price, kodiaq.vat_deductible, kodiaq.drive, kodiaq.power_kw) == (
        910000, True, "4x4", 142,
    )  # fmt: skip
    xc40 = cards["9102"]
    assert (xc40.condition, xc40.price, xc40.model) == ("demo", 929900, "XC40")
    assert cards["8254"].power_kw == 184  # "184kW"
    last = SOURCE.parse_listing(read("listing_last.html"), 20, places)
    assert not last.has_next and len(last.leads) == 2


def test_listing_urls():
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.expire_after_days
    assert url(1) == "https://www.autovolf.cz/ojete-vozy"
    assert url(3) == "https://www.autovolf.cz/ojete-vozy/3?stavId=2"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_3.html"), Lead("9406", BASE + "9406"), places)
    assert car is not None and car.external_id == "9406"
    assert car.title == "Volvo V60 D3"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volvo", "V60", 2018, 157500, 290000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.engine_cc, car.power_kw) == (
        "diesel", "manual", "combi", 1969, 110,
    )  # fmt: skip
    assert car.vin == "YV1FW79C1J2401771" and car.color == "bílá"
    assert (car.first_owner, car.service_book, car.origin_country) == (False, True, "Německo")
    assert car.vat_deductible is None and car.condition == "used"
    assert car.seller_name == "Auto Volf" and car.place_text == "Plzeň"
    assert car.location is not None and car.location.municipality == "Plzeň"
    assert car.location.address_text == "Folmavská 2828/6, Plzeň"
    assert car.description == "vyzkoušený ojetý vůz"
    assert len(car.equipment) == 32 and car.equipment[0] == 'Kola z lehké slitiny 17"'
    assert len(car.images) == 27 and "124428.jpg" in car.images[0]


def test_detail_with_vat(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("8039", "u"), places)
    assert car is not None and car.title == "Škoda Kodiaq Selection 2,0 TDI"
    assert (car.year, car.mileage_km, car.price, car.vat_deductible) == (
        2024, 19739, 910000, True,
    )  # fmt: skip
    assert (car.drive, car.gearbox, car.body, car.vin) == (
        "4x4",
        "automatic",
        "suv",
        "TMBLN7PS9RT013520",
    )
    assert car.origin_country == "Česká republika" and car.place_text == "Domažlice"
    assert "Záruka: 5 let/do 100 000 km" in car.description
    first = SOURCE.parse_detail(read("detail_1.html"), Lead("9316", "u"), places)
    assert first is not None and (first.year, first.mileage_km, first.engine_cc) == (
        2017, 108832, 999,
    )  # fmt: skip
    assert first.first_owner and first.place_text == "Holýšov"
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_listing_and_detail():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed(BASE + "9316/skoda-fabia-style-1-0-tsi-81-kw")
