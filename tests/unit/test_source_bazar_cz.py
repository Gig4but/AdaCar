from pathlib import Path

import pytest

from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.sources import bazar_cz

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "bazar_cz"


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
    page = bazar_cz.SOURCE.parse_listing(read("listing.html"), 1, places)
    assert page.has_next and len(page.leads) == 23
    first = page.leads[0]
    assert first.external_id == "14257138"
    assert first.url == first.fetch_url
    assert first.url.startswith("https://www.bazar.cz/xad14257138-")
    car = first.card
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km) == (
        "volkswagen",
        "Multivan",
        2004,
        230000,
    )
    assert (car.price, car.fuel, car.gearbox, car.power_kw) == (229900, "diesel", "manual", 128)
    assert car.seller_kind == "private" and car.place_text == "Teplice" and car.images
    vague = page.leads[1].card  # "Prodej osobního vozu": only the year in the form
    assert vague is not None and (vague.make, vague.model, vague.year) == (None, None, 2020)
    praha = next(x.card for x in page.leads if x.card and x.card.place_text == "Praha")
    assert praha.location is not None and praha.location.kraj == "Hlavní město Praha"


def test_next_page(places):
    assert bazar_cz.BazarCz.listing_page(1) == "https://www.bazar.cz/osobni-auta/"
    assert bazar_cz.BazarCz.listing_page(3) == "https://www.bazar.cz/osobni-auta/3/"
    page = bazar_cz.SOURCE.parse_listing(read("listing_2.html"), 2, places)
    assert page.has_next and len(page.leads) == 23
    last = bazar_cz.SOURCE.parse_listing(read("listing_2.html"), 300, places)
    assert not last.has_next  # no link to page 301


def test_detail(places):
    lead = bazar_cz.SOURCE.parse_listing(read("listing.html"), 1, places).leads[0]
    car = bazar_cz.SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None
    assert car.title == "Volkswagen Multivan T5 2.5 TDI 128 kW – 7 míst"
    assert (car.make, car.model, car.year, car.mileage_km) == (
        "volkswagen", "T5 Multivan", 2004, 230000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "diesel", "manual", "combi", 128, 2461,
    )  # fmt: skip
    assert car.price == 229900 and car.vin == "WV2ZZZ7HZ4H025959" and car.color == "modrá"
    assert car.place_text == "Bílina, Teplice" and car.seller_name == "novakj"
    assert car.posted_at is not None and car.posted_at.date().isoformat() == "2026-11-17"
    assert "ESP" in car.equipment and len(car.images) == 13
    assert car.description.startswith("Prodám Volkswagen Multivan T5")


def test_detail_reads_the_text_where_the_form_is_empty(places):
    lead = bazar_cz.SOURCE.parse_listing(read("listing.html"), 1, places).leads[1]
    car = bazar_cz.SOURCE.parse_detail(read("detail_2.html"), lead, places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km) == ("skoda", "Scala", 2020, 60000)
    assert (car.fuel, car.gearbox, car.price) == ("petrol", "automatic", 329000)


def test_robots_allow_the_pages_used():
    robots = RobotsRules.parse(read("robots.txt"), "adacar")
    assert robots.allowed(bazar_cz.BazarCz.listing_page(1))
    assert robots.allowed(bazar_cz.BazarCz.listing_page(2))
    assert robots.allowed("https://www.bazar.cz/xad14257138-volkswagen-multivan-t5-2-5-tdi.aspx")
