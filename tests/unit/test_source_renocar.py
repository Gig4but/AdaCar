from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import renocar

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "renocar"
SOURCE = renocar.SOURCE
CAR = "https://www.renocar.cz/bmw/vuz/"


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


def test_sitemap_listing(places):
    page = SOURCE.parse_listing(read("sitemap.xml"), 1, places)
    # BMW and MINI cars; the motorcycles' pages and other pages are left out.
    assert not page.has_next and len(page.leads) == 532
    lead = page.leads[0]
    assert lead.external_id == "39830423" and lead.card is None
    assert lead.url == lead.fetch_url == CAR + "bmw-m850i-xdrive-gran-coupe-39830423"
    urls = {x.url for x in page.leads}
    assert "https://www.renocar.cz/mini/vuz/mini-mini-countryman-jcw-all4-new-42026267" in urls
    assert not any("/motorrad/" in u for u in urls)
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.max_pages == 1
    assert url(1) == "https://www.renocar.cz/Sitemap.xml"


def test_used_car(places):
    lead = Lead("41955058", CAR + "bmw-x1-sdrive20d-41955058")
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.title == "BMW X1 sDrive20d"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "bmw", "X1", 2025, 20634, 998000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.condition, car.power_kw, car.engine_cc) == (
        "diesel", "automatic", "used", 120, 1995,
    )  # fmt: skip
    assert (car.color, car.doors, car.seats, car.vat_deductible) == (
        "Metalický lak Fire Red", 5, 5, True,
    )  # fmt: skip
    assert car.seller_kind == "dealer" and car.seller_name == "Renocar"
    assert car.location is None and car.vin is None  # no branch, only a short VIN
    assert car.description.startswith("Premium Selection: 24 měsíční záruka")
    assert len(car.equipment) == 57 and car.equipment[1] == "ABS PRO"
    assert len(car.images) == 16
    assert (
        car.images[0] == "https://www.renocar.cz/helios_resources/images/41955058_00_43478752.JPG"
    )


def test_new_car_and_series_model(places):
    new = SOURCE.parse_detail(read("detail_2.html"), Lead("43247659", "u"), places)
    assert new is not None and new.title == "BMW X7 xDrive40d"
    assert (new.condition, new.year, new.mileage_km, new.price) == ("new", None, None, 2649000)
    assert (new.fuel, new.gearbox, new.power_kw) == ("diesel", "automatic", 250)
    m850 = SOURCE.parse_detail(read("detail_3.html"), Lead("39830423", "u"), places)
    assert m850 is not None and (m850.model, m850.body, m850.fuel) == (
        "Řada 8 Gran Coupé", "coupe", "petrol",
    )  # fmt: skip
    assert (m850.condition, m850.mileage_km, m850.year) == ("used", 25799, None)
    assert m850.gearbox is None  # only "Manuál a servisní knížka" in the equipment
    assert SOURCE.parse_detail("<html><h1>x</h1></html>", Lead("1", "u"), places) is None


@pytest.mark.parametrize(
    ("title", "fuel"),
    [
        ("BMW 330e Touring", "plugin_hybrid"),
        ("BMW M340i xDrive", "petrol"),
        ("BMW i4 eDrive40", "electric"),
        ("BMW iX1 xDrive30", "electric"),
        ("MINI Cooper SE", "electric"),
        ("BMW X5 xDrive50e", "plugin_hybrid"),
        ("BMW M135 xDrive", None),
    ],
)
def test_fuel_from_engine_code(title, fuel):
    assert renocar.fuel(title) == fuel


def test_robots_allow_sitemap_and_cars():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(renocar.SITEMAP)
    assert rules.allowed(CAR + "bmw-x1-sdrive20d-41955058")
