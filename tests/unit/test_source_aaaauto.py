from datetime import date
from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import aaaauto

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"
SOURCE = aaaauto.SOURCE


def read(name: str) -> str:
    return (SOURCES / "aaaauto" / name).read_text(encoding="utf-8")


@pytest.fixture
def places(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        yield PlaceResolver(s)
    engine.dispose()


def test_one_nationwide_listing_newest_first():
    listings = SOURCE.listings(ScrapeQuery(kraje=["Kraj Vysočina"]))
    assert len(listings) == 1 and listings[0][0] is None
    assert listings[0][1](3) == "https://www.aaaauto.cz/ojete-vozy/?sort=14&page=3"
    assert SOURCE.newest_first and SOURCE.nationwide and SOURCE.recheck_after_days


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing_p2.html"), 2, places)
    assert page.has_next and len(page.leads) == 35
    lead = page.leads[0]
    car = lead.card
    assert car is not None and lead.external_id == "34954743"
    assert (
        lead.url == lead.fetch_url == "https://www.aaaauto.cz/detail/skoda/octavia-scout/34954743"
    )
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia Scout", 2014, 306886, 180000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive, car.power_kw) == (
        "diesel", "automatic", "combi", "4x4", 135,
    )  # fmt: skip
    assert car.seller_kind == "dealer" and car.seller_name == "AAA Auto"
    assert car.place_text == "Praha 8 - Dolní Chabry"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.vin and car.images[0].startswith("https://")
    assert car.posted_at and (date.today() - car.posted_at.date()).days == 56  # days in stock
    kolin = [x.card for x in page.leads if x.card and x.card.place_text == "Kolín"]
    assert all(c.location and c.location.kraj == "Středočeský kraj" for c in kolin)


def test_detail(places):
    lead = Lead("21873925", "https://www.aaaauto.cz/detail/audi/a6/21873925")
    car = SOURCE.parse_detail(read("detail_21873925.html"), lead, places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "audi", "A6", 2020, 122676, 660000,
    )  # fmt: skip
    assert car.vat_deductible is True and car.vin == "WAUZZZF27LN048240"
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("diesel", "automatic", "sedan", "4x4")
    assert (car.power_kw, car.engine_cc, car.doors, car.seats) == (210, 2967, 4, 5)
    assert car.first_owner is True and car.service_book is True
    assert car.origin_country == "Česká republika"
    assert car.location is not None and car.location.municipality == "Praha"
    assert len(car.equipment) > 30 and "tempomat" in car.equipment
    assert "Audi A6" in car.description and len(car.images) > 10


def test_branches_from_the_detail_page():
    branches = aaaauto.live_branches(aaaauto.ng_state(read("detail_21873925.html")))
    assert branches["2"][0] == "Praha 8 - Dolní Chabry"
    assert branches["17"][1:] == ("Kosmonosy", "29301")
    assert all(crm in branches for crm in aaaauto.BRANCHES if crm != "356")


def test_gone():
    assert SOURCE.gone(read("listing_p2.html"))  # where a sold car's page redirects
    assert not SOURCE.gone(read("detail_21873925.html"))


def test_robots_allow_listing_and_detail():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed("https://www.aaaauto.cz/detail/audi/a6/21873925")
