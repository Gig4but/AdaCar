from pathlib import Path

import pytest

from adacar.core import ParsedCar, ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import machmotors

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "machmotors"
SOURCE = machmotors.SOURCE
BASE = "https://www.machmotors.cz/bmw/detail-vozu/"


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


def cards(places):
    page = SOURCE.parse_listing(read("listing.html"), 1, places)
    return page, {x.external_id.split("-")[0]: x for x in page.leads}


def test_listing_cards(places):
    page, leads = cards(places)
    assert not page.has_next and len(page.leads) == 178
    lead = page.leads[0]
    assert lead.external_id == "v002165-volvo-xc60-b5-awd-plus-bright"
    assert len({x.external_id for x in page.leads}) == 178
    assert lead.url == lead.fetch_url == BASE + "v002165-volvo-xc60-b5-awd-plus-bright"
    car = lead.card
    assert car is not None and car.title == "Volvo XC60 B5 AWD PLUS Bright"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volvo", "XC60", 2026, 6444, 1169000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.vat_deductible, car.condition) == (
        "petrol",
        "automatic",
        184,
        True,
        "used",
    )
    assert car.seller_kind == "dealer" and car.seller_name == "MACH MOTORS"
    assert car.images == [
        "https://www.machmotors.cz/bmw/media/thumbs/auta/thumb_370x220_dsc01569.jpg?v=1795001718"
    ]
    # Older ads have ids without the "v".
    bmw = leads["0838461"].card
    assert bmw is not None and (bmw.make, bmw.model, bmw.year, bmw.price) == (
        "bmw", "530d", 2021, 1099000,
    )  # fmt: skip
    new = leads["v002291"].card
    assert new is not None and (new.condition, new.year, new.mileage_km) == ("new", None, 50)
    velar = leads["v001944"].card
    assert velar is not None and (velar.make, velar.model) == ("land-rover", "Range Rover Velar")
    merc = leads["v001132"].card
    assert merc is not None and (merc.make, merc.model) == ("mercedes-benz", "E")
    manual, hybrid = leads["v002149"].card, leads["v001605"].card
    assert manual is not None and manual.gearbox == "manual"
    assert hybrid is not None and hybrid.fuel == "hybrid"


def test_listing_urls():
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.machmotors.cz/bmw/nabidka_vozu"
    assert SOURCE.kind == "dealer" and SOURCE.max_pages == 1


def test_detail(places):
    _, leads = cards(places)
    car = SOURCE.parse_detail(read("detail_1.html"), leads["v002246"], places)
    assert car is not None and car.external_id == leads["v002246"].external_id
    assert car.title == "Audi RS6 Exclusive"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "audi", "RS6", 2020, 41779, 2448400,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.engine_cc, car.power_kw) == (
        "petrol", "automatic", "combi", 3996, 441,
    )  # fmt: skip
    assert car.vat_deductible and car.condition == "used" and car.vin is None
    assert car.color == "Y5Q Sepang Blue Pearl - Audi Exclusive"
    assert car.seller_name == "MACH MOTORS" and car.place_text == "Praha"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.location.address_text == "Za opravnou 186/1, Praha"
    assert car.description.startswith("Stage 2, Výfuk Zajacovic")
    assert "Keramická ochrana GYEON" in car.description
    assert len(car.equipment) == 30 and car.equipment[0].startswith('22" litá kola')
    assert len(car.images) == 50
    assert car.images[0].endswith("thumb_2000x1000_dsc04013.jpg?v=1794492179")


def test_detail_packets_and_new_car(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("0838461", "u"), places)
    assert car is not None and (car.price, car.year, car.engine_cc) == (1099000, 2021, 2993)
    assert (car.fuel, car.body, car.power_kw) == ("diesel", "combi", 210)
    assert car.place_text == "České Budějovice"
    assert car.equipment[0] == "M sportovní paket" and len(car.equipment) == 41
    assert len(car.images) == 38
    new = SOURCE.parse_detail(read("detail_3.html"), Lead("v002291", "u"), places)
    assert new is not None and (new.condition, new.year, new.body) == ("new", None, "liftback")
    assert new.color == "Černá metalíza" and new.mileage_km == 50
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_prices():
    car = ParsedCar("1", "t")
    machmotors.set_price(car, "Nová cena: 1 900 000 Kč + DPH")
    assert (car.price, car.vat_deductible) == (2299000, True)
    car = ParsedCar("1", "t")
    machmotors.set_price(car, "Cena: 899 000 Kč")
    assert (car.price, car.vat_deductible) == (899000, None)


def test_robots_allows_listing_and_details():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(1))
    assert rules.allowed(BASE + "v002246-audi-rs6-exclusive")
