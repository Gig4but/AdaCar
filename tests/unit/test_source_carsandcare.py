from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import carsandcare

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "carsandcare"
SOURCE = carsandcare.SOURCE
AD = "https://www.carsandcare.cz/detail/skoda-octavia-28981"


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


def test_sitemap_lists_every_car_once(places):
    page = SOURCE.parse_listing(read("sitemap.xml"), 1, places)
    assert not page.has_next and len(page.leads) == 189  # the /en/ pages are left out
    lead = page.leads[0]
    assert (lead.external_id, lead.url, lead.fetch_url, lead.card) == ("28981", AD, AD, None)
    assert len({x.external_id for x in page.leads}) == 189
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.kind == "leasing"
    assert url(1) == "https://www.carsandcare.cz/dynamic-sitemap.xml"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("28981", AD), places)
    assert car is not None and car.title == "Škoda Octavia 1,5 TSI DSG Ambition Plus"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2023, 83655, 405000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.engine_cc) == (
        "petrol", "automatic", 110, 1498,
    )  # fmt: skip
    assert (car.doors, car.seats, car.first_owner, car.service_book) == (5, 5, True, True)
    assert car.vin == "TMBAR8NX5RY020757" and car.vat_deductible is True
    assert car.seller_kind == "dealer" and car.seller_name == "Cars & Care (Business Lease)"
    assert car.place_text == "Praha 5"
    assert car.location is not None and car.location.municipality == "Praha"
    assert len(car.images) == 20 and car.images[0].endswith("/232282/skoda-octavia_232282.jpg")


def test_detail_ford(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("28863", "u"), places)
    assert car is not None and (car.make, car.model, car.price) == ("ford", "Kuga", 480000)
    assert (car.fuel, car.gearbox) == ("diesel", "automatic")  # "2,0 EcoBlue AWD AT8"
    assert (car.power_kw, car.vin) == (140, "WF0FXXWPMFNU68285")
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_sitemap_and_cars():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(carsandcare.SITEMAP) and rules.allowed(AD)
    assert not rules.allowed("https://www.carsandcare.cz/nabidka-vozu-pro-klienty")
