from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autonord

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autonord"
SOURCE = autonord.SOURCE
OCTAVIA = "011f966b-5b10-458b-a6c1-b33aa46c06f5"
TIGUAN = "74278b19-da88-426d-aada-ea8260bfa104"


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
    assert page.has_next and len(page.leads) == 24
    lead = page.leads[0]
    assert lead.external_id == OCTAVIA
    assert (
        lead.url
        == lead.fetch_url
        == (
            "https://www.autonord.cz/vozy/skoda/octavia/"
            "octavia-2-0-tdi-110kw-dsg-top-2024-id-011f966b5b10458ba6c1b33aa46c06f5"
        )
    )
    car = lead.card
    assert car is not None and car.title == "Škoda Octavia 2.0 TDI 110kW DSG Top Selection Combi"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2024, 21859, 666000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("diesel", "automatic", "combi", 110)
    assert (car.color, car.condition, car.place_text) == ("bílá", "used", "Děčín")
    assert car.seller_kind == "dealer" and car.seller_name == "Auto Nord"
    assert car.images and car.images[0].endswith("/TMBJJ0NX5SY036956/05de0fa85cb95604.jpg")
    cards = [x.card for x in page.leads if x.card]
    fabia = next(c for c in cards if c.external_id.startswith("0ed46fe9"))
    assert fabia.condition == "demo" and fabia.place_text == "Jablonec nad Nisou"
    assert {c.place_text for c in cards} >= {"Česká Lípa", "Terezín", "Roudnice nad Labem"}
    assert cards[1].make == "seat" and cards[1].fuel == "petrol"  # "TSI", the fuel field empty


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 23, places)
    assert not page.has_next and len(page.leads) == 22
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.autonord.cz/vozy?stav=used"
    assert url(3) == "https://www.autonord.cz/vozy?stav=used&page=3"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead(OCTAVIA, "u"), places)
    assert car is not None and car.external_id == OCTAVIA
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2024, 21859, 666000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "diesel", "automatic", "combi", 110, 2000,
    )  # fmt: skip
    assert car.vin == "TMBJJ0NX5SY036956" and car.drive is None and car.vat_deductible
    assert (car.first_owner, car.service_book, car.origin_country) == (
        True, True, "Česká republika",
    )  # fmt: skip
    assert car.seller_name == "Auto Nord" and car.place_text == "Děčín"
    assert car.description.startswith("Head-up displej, 4vál. vznětový turbomotor")
    assert "Střešní nosič" in car.equipment and len(car.equipment) == 70
    assert len(car.images) == 35


def test_detail_awd_without_vat(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead(TIGUAN, "u"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.fuel, car.drive) == (
        "volkswagen", "Tiguan", 2016, "diesel", "4x4",
    )  # fmt: skip
    assert car.vin == "WVGZZZ5NZHW828951" and car.engine_cc == 1968
    assert car.vat_deductible is None and car.first_owner is None and car.origin_country is None
    assert car.place_text == "Česká Lípa"
    assert SOURCE.parse_detail(read("detail_2.html"), Lead(OCTAVIA, "u"), places) is None
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_fuel_from_the_version_when_the_site_gives_none():
    assert autonord.variant_fuel("1.4 TSI iV DSG") == "plugin_hybrid"
    assert autonord.variant_fuel("1.4 TSI GTE DSG") == "plugin_hybrid"
    assert autonord.variant_fuel("1.5 TSI 110 kW") == "petrol"
    assert autonord.variant_fuel("2.0 TDI 110 kW") == "diesel"


def test_robots_allow_the_listing_and_details():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed("https://www.autonord.cz/vozy/vw/tiguan/x-id-74278b19")
    assert not rules.allowed("https://www.autonord.cz/api/vehicles")
