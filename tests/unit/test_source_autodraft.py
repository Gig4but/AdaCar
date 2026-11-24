from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autodraft

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autodraft"
SOURCE = autodraft.SOURCE


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
    lead = page.leads[2]
    assert lead.external_id == "15971"
    assert lead.url == lead.fetch_url and lead.url.startswith(
        "https://www.autodraft.cz/detail/15971-skoda-kodiaq-style-2-0-tdi-147kw-4x4-dsg"
    )
    car = lead.card
    assert car is not None and car.title.startswith("Škoda Kodiaq Style 2.0 TDI 147kW 4x4 DSG /")
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Kodiaq", 2023, 95836, 657000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.drive, car.power_kw) == ("diesel", "automatic", "4x4", 147)
    assert car.seller_kind == "dealer" and car.seller_name == "Autodraft"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.images[0].startswith("https://www.autodraft.cz/images/details/1920_15971-")
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["15997"].fuel == "electric" and cards["15997"].make == "mercedes-benz"
    assert cards["16561"].fuel == "plugin_hybrid"  # "Hybrid", a "Plug-in hybrid" in the title
    assert cards["15956"].body == "combi"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 16, places)
    assert not page.has_next and [x.external_id for x in page.leads] == ["15917", "15039"]
    golf = page.leads[1].card
    assert golf is not None and golf.title.startswith("Volkswagen Golf 7 Variant Highline")
    assert (golf.make, golf.model) == ("volkswagen", "Golf")
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=["Plzeňský kraj"]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.autodraft.cz/auta.html"
    assert url(3) == "https://www.autodraft.cz/auta.html?gpage=3"


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[0]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "15945"
    assert car.title.startswith("Škoda Fabia 1.0 TSI 70kW / Navigace Amundsen")
    assert (car.make, car.model, car.year, car.price) == ("skoda", "Fabia", 2023, 339000)
    assert car.mileage_km == 113121  # the card's: the detail says "113 tis. km"
    assert (car.fuel, car.gearbox, car.power_kw) == ("petrol", "manual", 70)
    assert car.vin == "TMBEP6PJ6P4020999" and car.origin_country == "Německo"
    assert car.first_owner is True and car.vat_deductible is True
    assert car.color == "Černá perleťová metalíza Deep Black Pearl"
    assert car.description.startswith("Původní lak, nebouráno")
    assert len(car.equipment) == 39 and "Navigace AMUNDSEN" in car.equipment
    assert len(car.images) == 13
    assert car.images[0].endswith("/1920_15945-img-0714-jpg-dh4Uik.jpg")
    assert car.location is not None and car.location.municipality == "Praha"


def test_electric_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[1]
    car = SOURCE.parse_detail(read("detail_2.html"), lead, places)
    assert car is not None and car.external_id == "15997"
    assert (car.make, car.model, car.fuel, car.power_kw) == (
        "mercedes-benz",
        "EQB",
        "electric",
        140,
    )
    assert car.vin == "W1N9M0BB0PN028350" and car.mileage_km == 67299
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None
