from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import automaton

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "automaton"
SOURCE = automaton.SOURCE


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
    assert page.has_next and len(page.leads) == 10
    lead = page.leads[0]
    assert lead.external_id == "6519"
    assert (
        lead.url
        == lead.fetch_url
        == ("https://www.automaton.cz/nabidka-vozidel/mercedes-benz-tridy-e-6519")
    )
    car = lead.card
    assert car is not None and car.title == "Mercedes-Benz Třídy E 220d Avantgarde Distro LED Taž"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "mercedes-benz", "Třídy E", 2025, 27000, 1019000,
    )  # fmt: skip
    assert (car.fuel, car.power_kw, car.vat_deductible) == ("diesel", 145, True)
    assert car.seller_kind == "dealer" and car.seller_name == "Automaton"
    assert car.place_text == "Praha" and car.location is not None
    assert car.location.municipality == "Praha" and car.images == []  # signed for an hour only
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["6515"].fuel == "electric" and cards["6515"].power_kw == 300
    assert cards["6509"].vat_deductible is None


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 40, places)
    assert not page.has_next and len(page.leads) == 6
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["394"].fuel == "hybrid" and cards["72"].model == "Řada 5"
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.automaton.cz/nabidka-vozidel"
    assert url(3) == "https://www.automaton.cz/nabidka-vozidel?page=3"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("6511", "u"), places)
    assert car is not None and car.external_id == "6511"
    assert car.title == "Škoda Kodiaq TSI 4x4 Style Matrix Paměť Kam"
    assert car.drive == "4x4"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Kodiaq", 2023, 55200, 779000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "petrol", "automatic", "suv", 140, 1984,
    )  # fmt: skip
    assert (car.color, car.doors, car.seats, car.vat_deductible) == ("černá", 5, 5, True)
    assert car.vin is None and car.images == []
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.description.startswith("Možnost odpočtu DPH. Nabídka na dovoz")
    assert car.description.endswith("Nehavarováno, bez poškození.")
    assert "pohon 4x4" in car.equipment and len(car.equipment) == 46
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_the_listing_and_details():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed("https://www.automaton.cz/nabidka-vozidel/skoda-kodiaq-6511")
    assert not rules.allowed("https://www.automaton.cz/api/cars")
