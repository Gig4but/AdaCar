from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import lenner

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "lenner"
SOURCE = lenner.SOURCE


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
    # The first 15 cars, as a browser gets them first: more load on.
    page = SOURCE.parse_listing(read("listing.html"), 1, places)
    assert page.has_next and len(page.leads) == 15
    lead = page.leads[-1]
    assert lead.external_id == "64084"
    assert lead.url == lead.fetch_url == "https://www.lennermotors.cz/ojete-vozy/hyundai/i30/64084"
    car = lead.card
    assert car is not None and car.title == "Hyundai i30 1.5 T-GDI/117 Smart Plus Navi"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "hyundai", "i30", 2023, 23483, 429000,
    )  # fmt: skip
    assert (car.fuel, car.power_kw, car.vat_deductible) == ("petrol", 117, True)
    assert car.seller_kind == "dealer" and car.seller_name == "Lenner Motors"
    assert car.images and car.images[0].startswith(
        "https://www.lennermotors.cz/sites/default/files/styles/car_teaser/public/"
    )
    assert page.leads[0].card and page.leads[0].card.vat_deductible is None


def test_whole_listing_in_one_page(places):
    page = SOURCE.parse_listing(read("listing_all.html"), 1, places)
    assert not page.has_next and len(page.leads) == 332
    assert len({x.external_id for x in page.leads}) == 332
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["64118"].model == "Santa Fe" and cards["64118"].fuel == "hybrid"
    models = {x: cards[x].model for x in ("63905", "63856", "64092", "62061", "64043")}
    assert models == {
        "63905": "C-HR",
        "63856": "CX-9",
        "64092": "Range Rover Velar",
        "62061": "Discovery",
        "64043": "Stelvio",
    }
    assert cards["61681"].fuel == "plugin_hybrid" and cards["63973"].fuel == "plugin_hybrid"
    assert cards["63762"].fuel == "hybrid" and cards["63684"].fuel == "hybrid"
    mild = [x for x in cards.values() if "MHEV" in x.title]
    assert mild and {x.fuel for x in mild} == {"petrol", "diesel"}
    assert lenner.fuel("benzín", "Ford Puma 1.0 EcoBoost Hybrid/92kW") == "petrol"
    assert lenner.fuel("benzín", "Kia Niro 1.6 GDi PHEV") == "plugin_hybrid"
    assert lenner.fuel("nafta", "Mercedes-Benz C 300de PHEV") == "plugin_hybrid"
    title = "Škoda Octavia 2.0 TDI/110kW Style"
    assert lenner.model_name(title, "skoda", "octavia-combi") == "Octavia"
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.lennermotors.cz/ojete-vozy?page=24"  # cars 1 to 375
    assert url(2) == "https://www.lennermotors.cz/ojete-vozy?page=49"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("64084", "u"), places)
    assert car is not None and car.external_id == "64084"
    assert car.title == "Hyundai i30 1.5 T-GDI/117 Smart Plus Navi"
    info = lenner.parameters(LexborHTMLParser(read("detail_1.html")))
    assert info["Uvedení do provozu"] == "8 / 2023" and info["Platnost STK"] == "202708"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "hyundai", "i30", 2023, 23483, 429000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "petrol", "automatic", "combi", 117, 1482,
    )  # fmt: skip
    assert car.vin == "TMAH381DGPJ142426" and car.color == "bílá" and car.vat_deductible
    assert car.seller_name == "Lenner Motors" and car.place_text == "Praha 4 Braník"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.description.startswith("Vůz v tovární záruce do 07/2028.")
    assert "Android Auto" in car.equipment and len(car.equipment) == 72
    assert len(car.images) == 36 and car.images[0].endswith("/IMG_20261118_103402.jpg")


def test_detail_manual_without_vat(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("64094", "u"), places)
    assert car is not None and car.title == "Peugeot 308 1.6 HDI/82kW Active"
    assert (car.make, car.model, car.year, car.mileage_km) == ("peugeot", "308", 2012, 107713)
    assert (car.fuel, car.gearbox, car.body, car.vat_deductible) == (
        "diesel", "manual", "hatchback", None,
    )  # fmt: skip
    assert car.vin == "VF34C9HR8BY142870" and len(car.images) == 23
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_the_listing_and_details():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(1))
    assert rules.allowed("https://www.lennermotors.cz/ojete-vozy/hyundai/i30/64084")
    assert not rules.allowed("https://www.lennermotors.cz/search/")
