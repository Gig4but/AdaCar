from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead, merge_card
from adacar.scraper.sources import arcars

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "arcars"
SOURCE = arcars.SOURCE
API = "https://www.ar-cars.cz/api/arcars/list"


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
    page = SOURCE.parse_listing(read("list_used_1.json"), 1, places)
    # 60 cars, 15 of them at the Bratislava showroom; and a Harley-Davidson.
    assert page.has_next and len(page.leads) == 44
    assert all(x.external_id != "867" for x in page.leads)
    lead = page.leads[0]
    assert lead.external_id == "5703"
    assert (
        lead.url == lead.fetch_url == "https://www.ar-cars.cz/auto/ford-transit-custom-ms-rt-7mist"
    )
    car = lead.card
    assert car is not None and car.title == "Ford Transit Custom MS-RT, 7míst!!!"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "ford", "Transit Custom", 2026, 1700, 1499000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.drive, car.body, car.power_kw, car.engine_cc) == (
        "diesel", "automatic", "4x4", "van", 125, 1996,
    )  # fmt: skip
    assert (car.vat_deductible, car.condition, car.vin, car.color) == (
        True, "used", "WF0SXXTA7SSG50677", "Červená",
    )  # fmt: skip
    assert car.seller_kind == "dealer" and car.seller_name == "AR CARS"
    assert car.place_text == "Ostrava"
    assert len(car.images) == 5 and car.images[0].startswith("https://www.ar-cars.cz/sites/")
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["746"].make == "jaguar" and cards["746"].body == "coupe"
    assert "5771" not in cards  # BMW M5 in Bratislava, priced in euros


def test_last_page_new_cars_and_listing_urls(places):
    last = SOURCE.parse_listing(read("list_used_3.json"), 3, places)
    assert not last.has_next and len(last.leads) == 35
    new = SOURCE.parse_listing(read("list_new_1.json"), 1, places)
    assert not new.has_next and len(new.leads) == 18
    ferrari = next(x.card for x in new.leads if x.external_id == "5648")
    assert ferrari is not None and (ferrari.condition, ferrari.price, ferrari.drive) == (
        "new", 13250000, "rear",
    )  # fmt: skip
    assert ferrari.vat_deductible is None and ferrari.body == "cabrio"
    empty = SOURCE.parse_listing('{"total": 0, "items": []}', 1, places)
    assert empty.none_listed and not empty.leads
    used, new_cars = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert used[0] is None and new_cars[0] is None and SOURCE.nationwide
    assert used[1](1) == (
        API + "?sort=mileage-asc&condition%5B%5D=CONDITION_USED&dostupnost=active&offset=0&limit=60"
    )
    assert new_cars[1](2).endswith(
        "condition%5B%5D=CONDITION_NEW&dostupnost=active&offset=60&limit=60"
    )


def test_detail(places):
    lead = SOURCE.parse_listing(read("list_used_1.json"), 1, places).leads[0]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.title == "Ford Transit Custom MS-RT, 7míst!!!"
    assert (car.seats, car.first_owner) == (7, True)
    assert car.description.startswith("Ostrava,\nJediná 7-místná verze na trhu.")
    assert len(car.images) == 27
    assert car.images[0] == (
        "https://www.ar-cars.cz/sites/default/files/omnetic/"
        "omnetic_2c2b7fe73a770ac51ca5f8ba7a369cfef85ebb31.jpg"
    )
    merged = merge_card(car, lead.card)
    assert (merged.price, merged.mileage_km, merged.vin) == (1499000, 1700, "WF0SXXTA7SSG50677")


def test_detail_new_car(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("5648", "u"), places)
    assert car is not None and car.title == "Ferrari 12Cilindri Spider"
    assert car.seats == 2 and car.first_owner is None
    assert "Front Radar with ACC" in car.description and len(car.images) == 26
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None
    assert SOURCE.parse_listing("<html>error</html>", 1, places).leads == []


def test_robots_allow_api_and_detail():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.api_url("CONDITION_USED", 2))
    assert rules.allowed("https://www.ar-cars.cz/auto/ferrari-12cilindri-spider")
