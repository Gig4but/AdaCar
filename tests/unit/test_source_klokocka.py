from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import klokocka

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "klokocka"
SOURCE = klokocka.SOURCE


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
    lead = page.leads[1]
    assert lead.external_id == "4414"
    assert (
        lead.url
        == lead.fetch_url
        == ("https://autoklokocka.cz/nabidka-vozu/vuz/ojete-skoda-scala-1-0-tsi-selection-2025-2")
    )
    car = lead.card
    assert car is not None and car.title == "Škoda Scala 1,0 TSI Selection"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Scala", 2025, 7535, 419900,
    )  # fmt: skip
    assert (car.fuel, car.condition, car.vat_deductible) == ("petrol", "used", True)
    assert car.seller_kind == "dealer" and car.seller_name == "Autosalon Klokočka"
    assert car.images == ["https://autoklokocka.cz/getFile/id:1966995/type:small/211O36909.jpg"]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["4421"].condition == "demo" and cards["4421"].fuel == "electric"
    assert cards["4418"].mileage_km is None  # "--- km"
    assert sum(c.vat_deductible is None for c in cards.values()) == 1
    foot = '<li class="flex-grow">2025</li>'
    blank = read("listing.html").replace(foot, foot + '<li class="flex-grow">---</li>', 1)
    scala = SOURCE.parse_listing(blank, 1, places).leads[1].card
    assert scala is not None and scala.fuel == "petrol"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 58, places)
    assert not page.has_next and [x.external_id for x in page.leads] == ["708"]
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://autoklokocka.cz/nabidka-vozu?stav[]=ojete"
    assert url(3) == "https://autoklokocka.cz/nabidka-vozu?stav[]=ojete&page=3"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("4411", "u"), places)
    assert car is not None and car.external_id == "4411"
    assert car.title == "Volkswagen Touran 2,0 TDI Comfortline DSG 7.míst"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volkswagen", "Touran", 2025, 35218, 849900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("diesel", "automatic", "mpv", 110)
    assert (car.color, car.doors, car.drive) == ("modrá metalíza", 5, None)
    assert car.vat_deductible is True
    assert car.vin is None  # the site shows none
    assert car.seller_name == "Autosalon Klokočka" and car.place_text == "Praha 6 - Řepy"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.description.startswith("První majitel;")
    assert "8x airbag" in car.equipment and len(car.equipment) == 94
    assert len(car.images) == 43 and car.images[0].endswith("/id:1968328/type:lightbox/Foto_vozu")


def test_detail_of_a_demonstrator(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("4418", "u"), places)
    assert car is not None
    assert (car.model, car.year, car.mileage_km, car.price) == ("ID.Polo", 2026, 0, 699000)
    assert (car.fuel, car.body, car.power_kw, car.color) == ("electric", "hatchback", 85, "bílá")
    assert car.description == "Více Info u prodejců VW.\nVůz k odběru od 03/2027."
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_the_listing_and_details():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed(klokocka.BASE + "/nabidka-vozu/vuz/ojete-volkswagen-id-4-77kwh-pro-2022")
    assert not rules.allowed(klokocka.BASE + "/ajax/x")
