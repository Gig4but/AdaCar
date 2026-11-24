from datetime import datetime
from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import caraukce

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "caraukce"
SOURCE = caraukce.SOURCE


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
    assert page.has_next and len(page.leads) == 24
    assert page.leads[0].external_id == "58657"
    lead = next(x for x in page.leads if x.external_id == "58655")
    assert lead.url == lead.fetch_url == "https://www.caraukce.cz/item/58655"
    car = lead.card
    assert car is not None and car.title == "TOYOTA COROLLA TS 1.8 HYBRID"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "toyota", "COROLLA", 2023, 43669, 430000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.seller_kind) == ("hybrid", "automatic", "dealer")
    assert car.description == "Soutěž, konec 24. 11. 2026 14:00"
    assert car.expires_at == datetime(2026, 11, 24, 13, 0)  # 14:00 CET
    assert car.images == ["https://www.caraukce.cz/data/images/58655/preview/image00001.jpg"]


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 5, places)
    assert not page.has_next and len(page.leads) == 24
    card = page.leads[0].card
    assert card is not None and card.description == "Dražba, konec 30. 11. 2026 13:00"
    arteon = next(x.card for x in page.leads if x.external_id == "58908")
    assert arteon is not None and arteon.fuel == "hybrid"
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.kind == "auction"
    assert url(1) == "https://www.caraukce.cz/vozidla?druh=osobni"
    assert url(3) == "https://www.caraukce.cz/vozidla?druh=osobni&strana=3"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("58657", "u"), places)
    assert car is not None and car.title == "AUDI A6 AVANT"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "audi", "A6", 2018, 119593, 549000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.engine_cc) == (
        "diesel", "automatic", 200, 2967,
    )  # fmt: skip
    assert (car.color, car.seats, car.service_book, car.vat_deductible) == ("Černá", 5, True, True)
    assert car.vin == "WAUZZZ4G7JN096042" and "not_offer" not in car.extra
    assert car.seller_name == "Car Aukce, spol. s.r.o."
    bike = read("detail_1.html").replace("OSOBNÍ AUTOMOBIL", "MOTOCYKL")
    other = SOURCE.parse_detail(bike, Lead("58657", "u"), places)
    assert other is not None and other.extra["not_offer"] == "not_car"
    lines = car.description.splitlines()
    assert lines[:2] == [
        "Elektronická soutěž, konec 24. 11. 2026 14:00",
        "Orientační cena 549 000 Kč",
    ]
    assert "sada zimních pneu" in car.description and "Prohlídky: " in car.description
    auction = read("detail_1.html").replace(">Orientační cena<", ">Vyvolávací cena<")
    bid = SOURCE.parse_detail(auction, Lead("58657", "u"), places)
    assert bid is not None and "\nVyvolávací cena 549 000 Kč\n" in bid.description
    assert car.expires_at == datetime(2026, 11, 24, 13, 0)
    assert len(car.equipment) == 77 and "tažné zařízení" in car.equipment
    assert car.images == ["https://www.caraukce.cz/data/images/58657/preview/image00001.jpg"]


def test_detail_petrol(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("57442", "u"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "fiat", "500", 2009, 134398, 79000,
    )  # fmt: skip
    assert (car.fuel, car.power_kw, car.vin) == ("petrol", 73, "ZFA31200000362759")
    assert car.vat_deductible is None and car.service_book is None  # "Neuvedeno"
    assert car.expires_at == datetime(2026, 12, 11, 9, 0)
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None
