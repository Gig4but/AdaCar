from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autanet

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autanet"
SOURCE = autanet.SOURCE


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
    page = SOURCE.parse_listing(read("listing_skoda.html"), 1, places)
    assert page.has_next and len(page.leads) == 22
    lead = page.leads[3]
    car = lead.card
    assert car is not None and lead.external_id == "157538"
    assert (
        lead.url == lead.fetch_url == "https://www.autanet.cz/detail/skoda-superb-style-plus-157538"
    )
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Superb", 2019, 172191, 370000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.seller_kind) == ("diesel", "manual", "dealer")
    assert car.place_text == "Zlínský kraj"
    assert car.images[0].startswith("https://www.autanet.cz/img-vehicle-webp/")
    assert "Adapt. tempomat" in car.equipment
    new = page.leads[0].card
    assert (
        new is not None and new.posted_at is not None and new.images == []
    )  # "Přidáno dnes", no photo yet


def test_last_page(places):
    page = SOURCE.parse_listing(read("listing_lada_last.html"), 1, places)
    assert not page.has_next and page.leads
    assert all(lead.card and lead.card.make == "lada" for lead in page.leads)


def test_listing_urls_and_makes():
    assert SOURCE.make_url("skoda", 1) == "https://www.autanet.cz/osobni-vozidla/skoda"
    assert SOURCE.make_url("skoda", 3) == "https://www.autanet.cz/osobni-vozidla/skoda?page=3"
    urls = [url(1) for _, url in SOURCE.listings(ScrapeQuery(kraje=[], makes=["skoda", "ds"]))]
    assert urls == [
        "https://www.autanet.cz/osobni-vozidla/ds-automobiles",
        "https://www.autanet.cz/osobni-vozidla/ostatni",
        "https://www.autanet.cz/osobni-vozidla/skoda",
    ]
    assert len(SOURCE.listings(ScrapeQuery(kraje=[]))) == len(autanet.MAKES)


def test_detail(places):
    lead = Lead("115591", "https://www.autanet.cz/detail/x-115591")
    car = SOURCE.parse_detail(read("detail_photos.html"), lead, places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Superb", 2021, 81651, 478000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "diesel", "automatic", "combi", 147, 1968,
    )  # fmt: skip
    assert car.vat_deductible is True and car.first_owner is True
    assert car.vin is None  # "NEUVEDENO"
    assert (car.seller_name, car.place_text) == ("STOP CARS s.r.o.", "Znojmo")
    assert car.description.startswith("Vozidlo registrované") and len(car.equipment) > 50
    assert len(car.images) == 44 and car.images[0].startswith("https://www.autanet.cz/img-vehicle/")


def test_detail_with_district(places):
    car = SOURCE.parse_detail(read("detail_district.html"), Lead("159642", "u"), places)
    assert car is not None
    assert car.vin == "TMBAR7NP9P7064161" and car.body == "liftback"
    assert car.color == "černá metalíza" and (car.doors, car.seats) == (5, 5)
    assert car.place_text == "Zátor" and car.seller_name == "ZAJJM s.r.o. - AUTA S GARANCÍ"
    assert car.images == []


def test_robots_allow_the_urls_used():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.make_url("skoda", 1))
    assert rules.allowed(SOURCE.make_url("skoda", 2))
    assert rules.allowed("https://www.autanet.cz/detail/skoda-superb-style-plus-157538")
    assert not rules.allowed("https://www.autanet.cz/osobni-vozidla/skoda/?order=5")


@pytest.mark.parametrize(
    ("main", "make", "model"),
    [
        ("Land Rover Range Rover Sport", "land-rover", "Range Rover Sport"),
        ("Alfa Romeo Giulia", "alfa-romeo", "Giulia"),
        ("DS Automobiles DS7", "ds", "DS7"),
        ("Jaecoo 7", "jaecoo", "7"),
    ],
)
def test_make_and_model(main, make, model):
    assert autanet.make_and_model(main) == (make, model)
