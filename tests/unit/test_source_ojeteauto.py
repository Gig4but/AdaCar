from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import ojeteauto

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "ojeteauto"
SOURCE = ojeteauto.SOURCE
KODIAQ = "https://www.ojeteauto.cz/inzeraty/skoda-kodiaq-2-0-tdi-110kw-dsg-4x4-1229948/"


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


def test_robots_allows_the_pages_read():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    [(region, listing)] = SOURCE.listings(ScrapeQuery(kraje=["Kraj Vysočina"]))
    assert region is None
    assert listing(1) == "https://www.ojeteauto.cz/inzeraty/"
    assert listing(2) == "https://www.ojeteauto.cz/inzeraty/?paginator-page=2"
    assert rules.allowed(listing(1)) and rules.allowed(listing(2)) and rules.allowed(KODIAQ)
    assert not rules.allowed("https://www.ojeteauto.cz/inzeraty/?order=cena+ASC&do=changeOrder")


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing_1.html"), 1, places)
    assert page.has_next and len(page.leads) == 23  # 3 promoted, then the newest 20
    cards = {lead.external_id: lead for lead in page.leads}
    lead = cards["1229948"]
    car = lead.card
    assert car is not None
    assert lead.url == lead.fetch_url == KODIAQ
    assert car.title == "Škoda Kodiaq 2,0 TDI 110KW / DSG 4X4"
    assert (car.make, car.year, car.mileage_km, car.price) == ("skoda", 2021, 117000, 730000)
    assert (car.fuel, car.power_kw, car.gearbox) == ("diesel", 110, "automatic")
    assert car.images
    golf = cards["1229953"].card
    assert golf is not None and golf.fuel == "plugin_hybrid"  # "Golf plug-in hybrid", Palivo hybrid


def test_detail_of_an_agents_ad(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("1229948", KODIAQ), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km) == ("skoda", "Kodiaq", 2021, 117000)
    assert car.price == 730000 and car.body == "suv" and car.gearbox == "automatic"
    assert car.crashed is False and "panoramatické okno" in car.description
    # No address of the seller: the district from the text.
    assert car.place_text == "Liberec" and car.seller_kind is None
    assert len(car.images) == 7


def test_detail_of_a_dealer(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("1229744", "u"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km) == ("kia", "K4", 2026, 12516)
    assert car.price == 580000 and (car.fuel, car.body) == ("petrol", "hatchback")
    assert (car.power_kw, car.engine_cc, car.doors, car.seats) == (110, 1598, 5, 5)
    assert car.color == "černá" and car.first_owner and car.service_book
    assert car.origin_country == "Česká Republika"
    assert car.seller_kind == "dealer" and car.seller_name == "AAA Auto Praha"
    assert car.location is not None and car.location.municipality == "Praha"
    assert "navigace" in car.equipment
