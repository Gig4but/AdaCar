from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import csobleasing

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "csobleasing"
SOURCE = csobleasing.SOURCE
AD = "https://autosalon.csobleasing.cz/zanovni-vozy/130821"


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


def test_sitemap_lists_the_cars(places):
    page = SOURCE.parse_listing(read("sitemap.xml"), 1, places)
    assert not page.has_next and len(page.leads) == 114  # not the trucks and machines
    lead = page.leads[0]
    url = "https://autosalon.csobleasing.cz/zanovni-vozy/130151"
    assert (lead.external_id, lead.url, lead.fetch_url, lead.card) == ("130151", url, url, None)
    assert AD in {x.url for x in page.leads}
    [(region, listing)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.kind == "leasing"
    assert listing(1) == "https://autosalon.csobleasing.cz/sitemap.xml"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("130821", AD), places)
    assert car is not None and car.title == "Škoda Scala 1.0 TSI Style"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Scala", 2023, 113750, 295000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("petrol", "manual", "hatchback", None)
    assert (car.power_kw, car.engine_cc, car.vin) == (81, 999, None)
    assert car.vat_deductible is True  # 243 802 Kč without VAT
    assert car.seller_kind == "dealer" and car.seller_name == "ČSOB Leasing"
    assert car.place_text == "Praha 9 Čakovice"
    assert car.location is not None and car.location.municipality == "Praha"
    assert len(car.equipment) == 31 and "Isofix" in car.equipment
    assert len(car.images) == 15 and car.images[0].startswith(
        "https://autosalon.csobleasing.cz/getContentAsset/"
    )


def test_detail_peugeot(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("129227", "u"), places)
    assert car is not None and car.title == "PEUGEOT 308 SW 1.5 BlueHDi 130 S&S EAT8"
    assert (car.make, car.model, car.year, car.price) == ("peugeot", "308", 2020, 289000)
    assert (car.fuel, car.gearbox, car.body, car.color) == (
        "diesel", "automatic", "combi", "bílá Nacré",
    )  # fmt: skip
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_sitemap_and_cars():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(csobleasing.SITEMAP) and rules.allowed(AD)
    assert not rules.allowed("https://autosalon.csobleasing.cz/api/autosalon/cars/search")
