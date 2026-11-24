from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import drivalia

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "drivalia"
SOURCE = drivalia.SOURCE
AD = "https://future.drivalia.cz/vozidla/663086-cs-volvo-xc90/"


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


def test_listing_keeps_cars_on_sale(places):
    page = SOURCE.parse_listing(read("listing.json"), 1, places)
    assert len(page.leads) == 85  # of 100: the sold and invoiced ones are left out
    assert not page.has_next and page.none_listed
    lead = page.leads[0]
    url = "https://future.drivalia.cz/vozidla/562220-cs-skoda-superb/"
    assert (lead.external_id, lead.url, lead.fetch_url) == ("562220", url, url)
    car = lead.card
    assert car is not None and car.title == "Škoda Superb 2.0 TDI DSG Style Combi"
    assert (car.make, car.model, car.price) == ("skoda", "Superb", 499000)
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("diesel", "automatic", "combi", "front")
    assert (car.color, car.doors, car.seats, car.first_owner, car.service_book) == (
        "béžová", 5, 5, True, True,
    )  # fmt: skip
    assert car.seller_name == "Future Drivalia" and car.place_text == "Vestec"
    assert "adaptivní tempomat" in car.equipment
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert (cards["621815"].make, cards["621815"].fuel) == ("bmw", "electric")
    assert "663086" in cards  # reserved or available


def test_pages_of_sold_cars_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_sold.json"), 1, places)
    assert page.leads == [] and page.none_listed  # an answer, not a broken page
    assert SOURCE.parse_listing("[]", 1, places).none_listed  # past the end
    listings = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert len(listings) == drivalia.PAGES and {r for r, _ in listings} == {None}
    assert SOURCE.nationwide and SOURCE.kind == "leasing"
    assert listings[2][1](1) == (
        "https://future.drivalia.cz/wp-json/wc/store/v1/products?lang=cs&orderby=date"
        "&order=desc&per_page=100&page=3&_fields=id%2Cname%2Cpermalink%2Cshort_description"
        "%2Cprices%2Cattributes"
    )


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("663086", AD), places)
    assert car is not None and car.title == "Volvo XC90 2.0 B5 AUT AWD Plus Bright"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volvo", "XC90", 2024, 31234, 1199000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("petrol", "automatic", "suv", "4x4")
    assert (car.power_kw, car.engine_cc, car.seats, car.color) == (184, 1969, 7, "stříbrná")
    assert car.vin == "YV1LFM1V0S1312498" and car.vat_deductible is None
    assert car.seller_name == "Future Drivalia" and car.place_text == "Vestec"
    assert len(car.equipment) == 59 and len(car.images) == 34
    assert car.images[1].startswith("https://crpapi.drivalia.cz/cdn/272377/large")


def test_sold_car_page(places):
    assert SOURCE.parse_detail(read("detail_sold.html"), Lead("574820", "u"), places) is None
    assert SOURCE.gone(read("detail_sold.html")) and not SOURCE.gone(read("detail_1.html"))


def test_robots_allow_store_api_and_cars():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.api_page(1)) and rules.allowed(AD)
