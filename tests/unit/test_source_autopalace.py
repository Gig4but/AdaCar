from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autopalace

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autopalace"
SOURCE = autopalace.SOURCE
BASE = "https://www.autopalace.cz/skladove-vozy/"


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
    assert page.has_next and len(page.leads) == 12
    lead = page.leads[0]
    assert lead.external_id == "czto988022"
    assert (
        lead.url
        == lead.fetch_url
        == (
            BASE
            + "ojete-ford-grand-c-max-1-6-tdci-85kw-7-mist-stk-tazne-85-kw-modra-czto988022.html"
        )
    )
    car = lead.card
    assert car is not None
    assert car.title == "Ford Grand C-MAX 1,6 TDCI 85kW 7-míst STK Tažné 85 kW modrá"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "ford", "Grand C-MAX", 2014, 268200, 49900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.vat_deductible) == (
        "diesel", "manual", "mpv", 85, None,
    )  # fmt: skip
    assert car.seller_kind == "dealer" and car.seller_name == "Auto Palace"
    assert car.place_text == "Praha Spořilov"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.images == [
        "https://www.autopalace.cz/uploads/images/vehicles/stock/32224/"
        "700x525x100x1_7826929-1O23700.webp"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    mazda = cards["czto979931"]
    assert (mazda.make, mazda.model, mazda.place_text) == ("mazda", "6", "Brno")
    assert mazda.location is not None and mazda.location.municipality == "Brno"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 48, places)
    assert not page.has_next and len(page.leads) == 6
    sq8 = next(x.card for x in page.leads if x.external_id == "czto991974")
    assert sq8 is not None and (sq8.price, sq8.vat_deductible) == (1949900, True)
    assert "skoo50956" in {x.external_id for x in page.leads}  # another stock system's id
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.autopalace.cz/skladove-vozy/typy_ojete/"
    assert url(3) == "https://www.autopalace.cz/skladove-vozy/typy_ojete/3/"


def test_detail(places):
    card = SOURCE.parse_listing(read("listing.html"), 1, places).leads[0]
    car = SOURCE.parse_detail(read("detail_1.html"), card, places)
    assert car is not None and car.external_id == "czto988022"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "ford", "Grand C-MAX", 2014, 268200, 49900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.engine_cc, car.power_kw) == (
        "diesel", "manual", "mpv", 1560, 85,
    )  # fmt: skip
    assert (car.color, car.doors, car.service_book, car.vin) == (
        "modrá", 5, False, "WF0HXXWPBHES06031",
    )  # fmt: skip
    assert car.vat_deductible is None and car.condition == "used"
    assert car.place_text == "AutoPalace Spořilov"
    assert car.location is not None and car.location.address_text == "Na Chodovci 2457/1, Praha"
    assert car.description.startswith("Vozidlo možno vidět v Auto Palace Spořilov")
    assert len(car.equipment) == 20 and car.equipment[0] == "aut. klimatizace"
    assert len(car.images) == 21 and car.images[0].endswith("/32224/7826929-1O23700.webp")


def test_detail_brno_and_vat(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("czto979931", "u"), places)
    assert car is not None and (car.make, car.model, car.year, car.engine_cc) == (
        "mazda", "6", 2007, 1999,
    )  # fmt: skip
    assert car.vin == "JMZGY19F671467124" and car.service_book
    assert car.location is not None and car.location.municipality == "Brno"
    assert car.location.address_text == "Řípská 22, Brno"
    sq8 = SOURCE.parse_detail(read("detail_3.html"), Lead("czto991974", "u"), places)
    assert sq8 is not None and (sq8.price, sq8.vat_deductible, sq8.power_kw) == (
        1949900, True, 373,
    )  # fmt: skip
    assert sq8.vin == "WAUZZZF13SD012606"  # "VIN : WAUZ..."
    assert sq8.place_text == "AutoPalace Vysočany" and len(sq8.equipment) == 94
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_listing_and_detail():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(1))
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed(
        BASE + "ojete-mazda-6-2-0-i-aac-vyhrev-bose-nove-cr-108-kw-seda-czto979931.html"
    )
    assert not rules.allowed(BASE + "typy_ojete/?sortBy=price-DESC")
