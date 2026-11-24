from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import andyauta

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "andyauta"
SOURCE = andyauta.SOURCE


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
    assert lead.external_id == "3653"
    assert (
        lead.url
        == lead.fetch_url
        == (
            "https://www.andy-auta.cz/auto/"
            "v260546-mercedes-benz-tridy-v-2-0-300d-174kw-xl-avantgarde-8mist-zaruka-az-5-3653"
        )
    )
    car = lead.card
    assert car is not None
    assert car.title == "Mercedes-Benz Třídy V 2,0 300D 174kW XL Avantgarde 8míst Záruka až 5"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "mercedes-benz", "Třídy V", 2025, 29941, 1698000,
    )  # fmt: skip
    assert (car.fuel, car.vat_deductible, car.place_text) == ("diesel", True, "Tábor")
    assert car.seller_kind == "dealer" and car.seller_name == "Andy-auta"
    assert len(car.images) == 4 and car.images[0].startswith(
        "https://www.andy-auta.cz/photos/V260546/mercedes-benz-tridy-v-300d-174kw"
    )
    towns = [x.card.place_text for x in page.leads if x.card]
    assert towns.count("České Budějovice") == 9 and towns.count("Tábor") == 15


def test_cars_of_every_stock_series_but_not_sold_ones(places):
    html = read("listing.html").replace('href="/auto/v260546-', 'href="/auto/z260546-')
    assert SOURCE.parse_listing(html, 1, places).leads[0].external_id == "3653"
    last = read("listing_last.html")
    live = last.replace('<span class="prodano-text">PRODÁNO</span>', "")
    page = SOURCE.parse_listing(live, 15, places)
    assert [x.external_id for x in page.leads if x.url.startswith(andyauta.BASE + "/auto/z")] == [
        "3852", "2752",
    ]  # fmt: skip
    assert len(page.leads) == 8 and not page.none_listed


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 15, places)
    assert not page.has_next and not page.leads  # not the cars it recommends, nor sold ones
    assert page.none_listed
    assert not SOURCE.parse_listing(read("listing.html"), 1, places).none_listed
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.andy-auta.cz/auta"
    assert url(3) == "https://www.andy-auta.cz/auta?page=3"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("2248", "u"), places)
    assert car is not None and car.external_id == "2248"
    assert car.title == "BMW X5 3,0 d xDrive 210 kW AT8 Záruka 3 roky v ceně vozu"
    assert car.drive == "4x4"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "bmw", "X5", 2020, 96709, 878000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "diesel", "automatic", "suv", 210, 2993,
    )  # fmt: skip
    assert car.vin == "WBAJU810X09E44634" and (car.doors, car.seats) == (5, 5)
    assert car.color == "tmavě šedá metalíza" and car.vat_deductible is True
    assert car.seller_name == "Andy-auta" and car.place_text == "Tábor"
    assert car.description.startswith("Hledáte vůz, který je synonymem pro suverenitu")
    assert car.description.endswith("Země původu: Lucembursko")
    assert "aut. převodovka" in car.equipment and len(car.equipment) == 84
    assert len(car.images) == 30 and "/photos/V250139/" in car.images[0]


def test_detail_at_the_other_branch(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("3872", "u"), places)
    assert car is not None
    assert (car.model, car.year, car.mileage_km, car.power_kw) == ("Kodiaq", 2024, 55897, 142)
    assert car.drive == "4x4"
    assert car.vin == "TMBLN9PS6RT019500" and car.place_text == "České Budějovice"
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_a_sold_cars_page_says_it_is_gone():
    assert SOURCE.gone(read("detail_sold.html"))
    assert not SOURCE.gone(read("detail_1.html")) and not SOURCE.gone(read("detail_2.html"))
    sold_elsewhere = read("detail_1.html").replace(
        "</body>",
        '<div class="prodano-value"><span class="prodano-text">PRODÁNO</span></div></body>',
    )
    assert not SOURCE.gone(sold_elsewhere)


def test_robots_allow_the_listing_and_details():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed("https://www.andy-auta.cz/auto/v250139-bmw-x5-2248")
    assert not rules.allowed("https://www.andy-auta.cz/_xml-feed/")
