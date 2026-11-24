from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import arval

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "arval"
SOURCE = arval.SOURCE
AD = "https://www.arvalauto.cz/cz/prodej/ojeta-vozidla/skoda/kodiaq2-0tdi-147-style-4x4-at/92989/cena-s-dph/"


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
    assert page.has_next and len(page.leads) == 18
    lead = page.leads[0]
    assert lead.external_id == "92066"
    assert lead.url == lead.fetch_url
    assert lead.url.endswith("/nissan/x-trail-e-p-e-f-tekna-awd-at/92066/cena-s-dph/")
    car = lead.card
    assert car is not None and car.title == "Nissan X-Trail X-Trail e-P. e-F. TEKNA AWD AT"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "nissan", "X-Trail", 2025, 28771, 915000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw) == ("petrol", "automatic", 157)
    assert car.seller_kind == "dealer" and car.seller_name == "Arval"
    assert car.images == [
        "https://www.arvalauto.cz/image/92066-9069-right11-3525826-525-345-2-0.webp"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["92989"].price == 475000 and cards["92989"].fuel == "diesel"
    assert cards["92326"].fuel == "plugin_hybrid"  # "Benzín", but a "phev" in the URL


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 29, places)
    assert not page.has_next and len(page.leads) == 11
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.kind == "leasing"
    assert url(1) == "https://www.arvalauto.cz/nabidka-vozu/prodej/ojeta-vozidla/cena-s-dph/"
    assert url(3) == "https://www.arvalauto.cz/nabidka-vozu/prodej/ojeta-vozidla/cena-s-dph/?pg=3"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("92989", AD), places)
    assert car is not None and car.title == "Škoda Kodiaq Kodiaq2.0TDI 147 Style 4x4 AT"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Kodiaq", 2021, 141403, 475000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("diesel", "automatic", "suv", "4x4")
    assert (car.power_kw, car.engine_cc, car.color, car.doors) == (147, 1968, "bílá", 5)
    assert car.vin == "TMBLN7NS5M8519036" and car.vat_deductible is True
    assert car.seller_name == "Arval" and car.place_text == "Praha 5"
    assert car.location is not None and car.location.municipality == "Praha"
    assert "Tažné zařízení" in car.equipment and len(car.equipment) == 9
    assert len(car.images) == 10 and car.images[0].startswith("https://www.arvalauto.cz/image/")


def test_detail_elsewhere(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("93128", "u"), places)
    assert car is not None and (car.make, car.model, car.price) == ("volvo", "EX90", 1040000)
    assert (car.power_kw, car.vin, car.place_text) == (335, "YV1LFH5V8R1167691", "Vestec u Prahy")
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_listing_and_ads():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_page(2))
    assert rules.allowed(AD)
