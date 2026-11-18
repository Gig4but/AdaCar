from pathlib import Path

import pytest

from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.sources import inzeruj
from adacar.scraper.sources.inzeruj import Inzeruj

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "inzeruj"
MAZDA = "19387"


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


def test_reads_the_whole_category_every_run():
    assert not inzeruj.SOURCE.newest_first and inzeruj.SOURCE.expire_after_days is None


def test_listing_cards(places):
    page = inzeruj.SOURCE.parse_listing(read("listing.html"), 1, places)
    assert page.has_next and len(page.leads) == 24
    mazda = next(x for x in page.leads if x.external_id == MAZDA)
    assert mazda.url == mazda.fetch_url
    assert mazda.url.startswith("https://www.inzeruj.cz/inzerat/auto/osobni-auta/mazda/mazda-6-")
    car = mazda.card
    assert car is not None
    assert (car.make, car.price, car.power_kw) == ("mazda", 285000, 110)
    assert (car.fuel, car.gearbox, car.body) == ("diesel", "manual", "combi")
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.images[0].startswith("https://www.inzeruj.cz/uploads/")
    benz = next(x.card for x in page.leads if x.card and "mercedes-benz/" in (x.card.url or ""))
    assert benz.make == "mercedes-benz"


def test_last_page(places):
    assert Inzeruj.listing_page(2) == "https://www.inzeruj.cz/categories/auto/osobni-auta?page=2"
    page = inzeruj.SOURCE.parse_listing(read("listing_4.html"), 4, places)
    assert not page.has_next and len(page.leads) == 19


def test_detail(places):
    lead = next(
        x
        for x in inzeruj.SOURCE.parse_listing(read("listing.html"), 1, places).leads
        if x.external_id == MAZDA
    )
    car = inzeruj.SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None
    assert car.title == "Mazda 6 2.2 SKYACTIV-D 110 kW | Wagon (combi)| Manuál"
    assert (car.make, car.mileage_km, car.price, car.power_kw) == ("mazda", 231280, 285000, 110)
    assert car.seller_name == "Pavel Černý" and car.place_text == "Praha"
    assert car.description.startswith("Prodám Mazdu 6") and len(car.images) == 10


def test_robots_allow_the_pages_used():
    robots = RobotsRules.parse(read("robots.txt"), "adacar")
    assert robots.allowed(Inzeruj.listing_page(1)) and robots.allowed(Inzeruj.listing_page(3))
    assert robots.allowed("https://www.inzeruj.cz/inzerat/auto/osobni-auta/mazda/mazda-6.html")
