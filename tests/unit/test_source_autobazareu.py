from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import KRAJ_SLUGS, Lead
from adacar.scraper.sources import autobazareu

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autobazareu"
SOURCE = autobazareu.SOURCE


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
    region = autobazareu.REGIONS["Kraj Vysočina"]
    assert rules.allowed(SOURCE.listing_url(region, 1))
    assert rules.allowed(SOURCE.listing_url(region, 50))
    assert not rules.allowed(SOURCE.listing_url(region, 51))
    assert rules.allowed(autobazareu.DETAIL.format(sef="skoda-kodiaq", id="AmneaoJ5p8o"))


def test_one_listing_per_region():
    query = ScrapeQuery(kraje=["Kraj Vysočina", "Hlavní město Praha"])
    (vysocina, first), (praha, _) = SOURCE.listings(query)
    assert (vysocina, praha) == ("Kraj Vysočina", "Hlavní město Praha")
    base = "https://www.autobazar.eu/cs/vysledky/osobne-vozidla/"
    assert first(1) == base + "?location=200000290&sortBy=dateDesc"
    assert first(2) == base + "?location=200000290&sortBy=dateDesc&page=2"
    assert set(autobazareu.REGIONS) == set(KRAJ_SLUGS)


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing_vysocina.html"), 1, places)
    assert page.has_next and len(page.leads) == 20
    lead = page.leads[0]
    car = lead.card
    assert car is not None
    assert lead.external_id == "Ae1cTKgLvNv"
    assert (
        lead.url
        == lead.fetch_url
        == ("https://www.autobazar.eu/cs/detail/bmw-rad-8-30-xdrive-m-packet/Ae1cTKgLvNv/")
    )
    assert (car.make, car.year, car.mileage_km, car.price) == ("bmw", 2023, 71000, 1789000)
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("diesel", "automatic", "coupe", "4x4")
    assert car.power_kw == 250 and car.vin == "WBABC61030CM71105"
    assert car.seller_kind == "dealer" and car.seller_name == "AUTOHUPRA, s. r. o."
    assert car.images and car.equipment


def test_price_in_euro_is_not_a_czk_price(places):
    page = SOURCE.parse_listing(read("listing_vysocina.html"), 1, places)
    cards = {lead.external_id: lead.card for lead in page.leads if lead.card}
    euro = cards["Am4Xf3qARtJ"]
    assert euro.price is None and euro.extra["price_eur"] == 6200
    # Placed only in the region: the seller's town tells where.
    assert euro.place_text == "Jihlava"
    assert euro.location is not None and euro.location.municipality == "Jihlava"
    private = cards["AeZH-fwhUaU"]
    assert private.seller_kind == "private" and private.place_text == "Čechtín"


def test_next_page_and_last_page(places):
    html = read("listing_vysocina_2.html")
    page = SOURCE.parse_listing(html, 2, places)
    assert page.has_next and len(page.leads) == 20
    # 469 cars in the region: page 24 is the last.
    assert not SOURCE.parse_listing(html, 24, places).has_next


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("AmneaoJ5p8o", "u"), places)
    assert car is not None
    assert car.title == "Škoda Kodiaq TSI iV 150kW DSG Exclusive Selection"
    assert (car.make, car.model, car.year, car.mileage_km) == ("skoda", "Kodiaq", 2024, 55597)
    assert car.price is None and car.extra["price_eur"] == 35800
    assert (car.fuel, car.gearbox, car.body, car.drive) == (
        "plugin_hybrid",
        "automatic",
        "suv",
        "front",
    )
    assert (car.power_kw, car.engine_cc, car.doors, car.seats) == (150, 1498, 5, 5)
    assert car.vin == "TMBJW9PS7ST016311" and car.condition == "used"
    assert car.vat_deductible and car.first_owner and car.service_book and car.crashed is False
    assert car.seller_name == "KARIREAL a.s." and car.place_text == "Třinec"
    assert "tovární záruka" in car.description
    assert car.posted_at is not None and car.posted_at.year == 2026
    assert len(car.images) == 5
