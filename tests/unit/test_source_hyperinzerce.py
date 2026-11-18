from pathlib import Path

import pytest

from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.sources import hyperinzerce
from adacar.scraper.sources.hyperinzerce import Hyperinzerce

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "hyperinzerce"
TIGUAN = "37417260831069430427"


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
    page = hyperinzerce.SOURCE.parse_listing(read("listing.html"), 1, places)
    assert page.has_next and len(page.leads) == 20
    first = page.leads[0]
    assert first.external_id == "80173373372304634172"
    assert first.url == first.fetch_url
    assert first.url.startswith("https://autobazar.hyperinzerce.cz/auta/inzerat/8017337")
    car = first.card
    assert car is not None
    assert (car.make, car.model, car.mileage_km, car.price) == ("mazda", "CX-5", 240640, 119000)
    assert (car.fuel, car.gearbox) == ("diesel", "automatic") and car.images
    tiguan = next(x.card for x in page.leads if x.external_id == TIGUAN)
    assert tiguan is not None and (tiguan.year, tiguan.price) == (2018, 288000)
    assert tiguan.location is not None and tiguan.location.municipality == "Praha"
    nowhere = next(x.card for x in page.leads if x.card and x.card.place_text == "Celá ČR")
    assert nowhere.location is None
    assert not any(x.card and x.card.extra for x in page.leads)


def test_next_page(places):
    assert Hyperinzerce.listing_page(1) == "https://autobazar.hyperinzerce.cz/auta?sortBy=3"
    assert Hyperinzerce.listing_page(2) == "https://autobazar.hyperinzerce.cz/auta?sortBy=3&page=2"
    assert not hyperinzerce.SOURCE.parse_listing(read("listing.html"), 67, places).has_next


def test_detail(places):
    lead = next(
        x
        for x in hyperinzerce.SOURCE.parse_listing(read("listing.html"), 1, places).leads
        if x.external_id == TIGUAN
    )
    car = hyperinzerce.SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None
    assert car.title == "Volkswagen Tiguan Allspace 2.0 TDI (110 kW)"
    assert (car.make, car.model, car.year, car.mileage_km) == ("volkswagen", "Tiguan", 2018, 218000)
    assert (car.fuel, car.gearbox, car.body, car.engine_cc, car.power_kw) == (
        "diesel", "manual", "suv", 1968, 110,
    )  # fmt: skip
    assert (car.price, car.color, car.condition) == (288000, "bílá", "used")
    assert (car.seller_kind, car.seller_name) == ("private", "Martin Dvořák")
    assert car.vat_deductible is True and "Tempomat" in car.equipment
    assert car.posted_at is not None and car.posted_at.isoformat() == "2026-11-17T11:00:00"
    assert car.location is not None and car.location.kraj == "Hlavní město Praha"
    assert len(car.images) == 13 and "%2Foriginal%2F" in car.images[0]


def test_wanted_ads_are_marked():
    car = hyperinzerce.ParsedCar("1", "Škoda Octavia")
    hyperinzerce.mark_wanted(
        car, "https://autobazar.hyperinzerce.cz/auta/inzerat/1-octavia-poptavka-praha"
    )
    assert car.extra["not_offer"] == "wanted"


def test_robots_allow_the_pages_used():
    robots = RobotsRules.parse(read("robots.txt"), "adacar")
    assert robots.allowed(Hyperinzerce.listing_page(1))
    assert robots.allowed(Hyperinzerce.listing_page(5))
    assert robots.allowed(
        f"https://autobazar.hyperinzerce.cz/auta/inzerat/{TIGUAN}-vw-nabidka-praha"
    )
