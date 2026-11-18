from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import carscz

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "carscz"
SOURCE = carscz.SOURCE
VOLVO = (
    "https://www.cars.cz/inzerce/osobni-auto-O/Volvo/V90/V90-2-0-D4-INCR-Kombi-177883766716140.html"
)
FORD = "https://www.cars.cz/inzerce/osobni-auto-O/Ford/Fiesta/Fiesta-1-3-Kombi-15015825105205.html"


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


def test_listing_url_carries_the_query_compressed():
    url = SOURCE.listing_url_of(3)
    p = parse_qs(urlsplit(url).query)["p"][0]
    assert url.startswith("https://www.cars.cz/inzerce/osobni-auto-O/?p=789c")
    assert carscz.decode(p) == "A_N=N&ctype=O&page=3&max=50&o=price"
    assert carscz.decode("not hex") == ""


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing_price_1.html"), 1, places)
    assert page.has_next and len(page.leads) == 50  # the promoted cards on top are not counted
    volvo = page.leads[0]
    car = volvo.card
    assert car is not None
    assert volvo.external_id == "177883766716140" and volvo.url == volvo.fetch_url == VOLVO
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volvo", "V90", 2020, 56950, None,
    )  # fmt: skip  # "Dohodou": priced cars follow
    assert (car.fuel, car.body, car.engine_cc, car.power_kw) == ("diesel", "combi", 1969, 140)
    assert car.images[0].endswith("/m177883766716140.jpg")
    gle = next(x.card for x in page.leads if x.external_id == "1792573671253177")
    assert gle is not None and gle.make == "mercedes-benz" and gle.vat_deductible is True
    last = page.leads[-1].card
    assert last is not None
    assert (last.make, last.model, last.year, last.price) == ("peugeot", "307", 2004, 9900)


def test_last_page(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 664, places)
    assert not page.has_next and page.leads


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_dealer.html"), Lead("177883766716140", VOLVO), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volvo", "V90", 2020, 56950, None,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "diesel", "automatic", "combi", 140, 1969,
    )  # fmt: skip
    assert car.vin == "YV1PWA8VDL1140323" and car.color == "šedá"
    assert (car.doors, car.seats, car.first_owner) == (5, 5, True)
    assert (car.seller_kind, car.seller_name, car.place_text) == (
        "dealer", "Motortec s.r.o.", "Brno - Ivanovice",
    )  # fmt: skip
    assert car.location is not None and car.location.municipality == "Brno"
    assert car.description.startswith("Koupeno jako referenční vozidlo")
    assert "Tažné zařízení" in car.equipment and "Apple CarPlay" in car.equipment
    assert car.images and car.images[0].startswith("https://img.cars.cz/")


def test_detail_with_price_and_no_text(places):
    car = SOURCE.parse_detail(read("detail_no_text.html"), Lead("15015825105205", FORD), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "ford", "Fiesta", 1998, 179000, 10000,
    )  # fmt: skip
    assert car.vat_deductible is True and car.crashed is False
    assert car.power_kw is None  # "0 kW"
    assert car.description == "" and car.place_text == "Teplice"


def test_robots_allow_the_urls_used():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_of(1)) and rules.allowed(SOURCE.listing_url_of(2))
    assert rules.allowed(VOLVO)
    assert not rules.allowed("https://www.cars.cz/vyhledat_auta")


@pytest.mark.parametrize(
    ("make", "model", "code", "name"),
    [
        ("Mercedes-Benz", "GLE", "mercedes-benz", "GLE"),
        ("Renault", None, "renault", None),
        ("Renault", "Grand-Espace", "renault", "Grand Espace"),
    ],
)
def test_make_and_model(make, model, code, name):
    assert carscz.make_and_model(make, model) == (code, name)
