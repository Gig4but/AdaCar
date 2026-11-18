import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import dasweltauto

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "dasweltauto"
SOURCE = dasweltauto.SOURCE


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
    page = SOURCE.parse_listing(read("listing.json"), 1, places)
    assert page.has_next and len(page.leads) == 100
    lead = page.leads[0]
    assert lead.external_id == "279100538"
    assert lead.url == "https://www.dasweltauto.cz/vehicle/279100538"
    assert (
        lead.fetch_url == "https://www.dasweltauto.cz/api/locales/cs_CZ/vehicles/279100538/detail"
    )
    car = lead.card
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volkswagen", "Multivan", 2026, 17500, 1597000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.drive, car.power_kw) == ("petrol", "automatic", "4x4", 130)
    assert car.vin == "WV2ZZZST7TH003851" and car.color == "červená - metalická"
    assert (car.seller_kind, car.seller_name, car.place_text) == (
        "dealer", "PIA CZ Auto Heller Ostrava", "Ostrava",
    )  # fmt: skip
    assert len(car.images) > 10 and car.images[0].startswith("https://")
    assert car.images[0].endswith("/1024.jpg")  # a bare image URL is refused
    makes = {lead.card.make for lead in page.leads if lead.card}
    assert {"skoda", "volkswagen", "audi", "hyundai"} <= makes


def test_card_location(places):
    item = json.loads(read("listing.json"))["resultListPage"]["content"][0]
    item["dealer"] = {"name": "X", "street": "Kolbenova 1", "postalCode": "190 00", "city": "Praha"}
    car = dasweltauto.parse_card(item, places)
    assert car.location is not None and car.location.municipality == "Praha"


def test_pages():
    page = SOURCE.parse_listing(json.dumps({"count": 250, "resultListPage": {}}), 3, None)  # type: ignore[arg-type]
    assert not page.has_next and not page.leads and not page.none_listed
    empty = SOURCE.parse_listing(json.dumps({"count": 0}), 1, None)  # type: ignore[arg-type]
    assert empty.none_listed
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=["Kraj Vysočina"]))
    params = parse_qs(urlsplit(url(4)).query)
    assert region is None and SOURCE.nationwide
    assert params == {
        "page": ["4"],
        "pageSize": ["100"],
        "sortBy": ["recent"],
        "sortAscending": ["true"],
    }


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.json"), Lead("276100071", "u"), places)
    assert car is not None and car.external_id == "276100071"
    assert car.title == "VW Tayron People 1,5 eTSI 110KW mHEV, DSG"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volkswagen", "Tayron", 2026, 15000, 899900,
    )  # fmt: skip
    assert car.vat_deductible is True  # a net price below the price
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("petrol", "automatic", "combi", "front")
    assert (car.power_kw, car.engine_cc, car.doors, car.seats) == (110, 1498, 5, 5)
    assert car.vin == "WVGZZZR47TW017012" and car.service_book is True
    assert car.seller_name == "Auto Nord Group - Česká Lípa" and car.place_text == "Česká Lípa"
    assert "financování" in car.description
    assert len(car.equipment) > 50 and len(car.images) > 10
    other = SOURCE.parse_detail(read("detail_2.json"), Lead("279100547", "u"), places)
    assert other is not None and other.fuel == "plugin_hybrid" and other.body == "suv"
    assert other.vat_deductible is None  # net price equal to the price: not told
    assert SOURCE.parse_detail("{}", Lead("1", "u"), places) is None


def test_robots_allow_the_api():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.search_url(2))
    assert rules.allowed(dasweltauto.DETAIL.format(id="276100071"))
    assert rules.allowed(dasweltauto.PAGE.format(id="276100071"))
    assert not rules.allowed("https://www.dasweltauto.cz/my-weltauto/favorites")
