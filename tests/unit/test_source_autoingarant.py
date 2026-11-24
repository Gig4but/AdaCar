from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autoingarant

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autoingarant"
SOURCE = autoingarant.SOURCE
KUGA = "ford-kuga-titanium-15-ecoboost-4x4-134kw-at-servkn-cr-tazne"


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
    assert page.has_next and len(page.leads) == 9
    lead = page.leads[0]
    assert lead.external_id == KUGA
    assert lead.url == lead.fetch_url == f"https://autoingarant.cz/vsechny-vozy/{KUGA}"
    car = lead.card
    assert car is not None
    assert car.title == "Ford Kuga Titanium 1.5 EcoBoost 4x4 134kw A/T, Serv.kn., ČR, Tažné"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "ford", "Kuga", 2016, 172364, 269000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw) == ("petrol", "automatic", 134)
    assert car.engine_cc == 1498 and car.vat_deductible is None and car.condition == "used"
    assert car.seller_kind == "dealer" and car.seller_name == "AUTO IN"
    assert car.images == [
        "https://autoingarant.cz/content/gal_cars/c/7/thumbnails/"
        "c7832f1110745b4c9b155106f4ae3f13-image(300x_).jpg"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    smax = cards["ford-s-max-110kw7-mist-manual-tazne-servis"]
    assert (smax.model, smax.fuel, smax.gearbox, smax.vat_deductible) == (
        "S-MAX", "diesel", "manual", True,
    )  # fmt: skip
    assert cards["bmw-rada-4-440i240kw-xdrive-servis"].model == "Řada 4"
    phev = cards["ford-transit-custom-kombi-trend-340-l1h1-25duratec-171kw232k-phev"]
    assert phev.fuel == "plugin_hybrid"  # "hybrid" in the fuel field


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 44, places)
    assert not page.has_next and len(page.leads) == 8
    electric = [x.card for x in page.leads if x.card and x.card.fuel == "electric"]
    assert len(electric) == 2 and all(car.engine_cc is None for car in electric)
    # Past the end: no cars, though the page links run on.
    assert not SOURCE.parse_listing("<html><a href='?page=46'>46</a></html>", 45, places).has_next
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://autoingarant.cz/vsechny-vozy"
    assert url(3) == "https://autoingarant.cz/vsechny-vozy?page=3"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead(KUGA, "u"), places)
    assert car is not None and car.external_id == KUGA
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "ford", "Kuga", 2016, 172364, 269000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("petrol", "automatic", "suv", "4x4")
    assert (car.power_kw, car.engine_cc, car.color) == (134, 1498, "Černá Tmavá Metalíza")
    assert car.vin == "WF0AXXWPMAGY05925" and car.vat_deductible is None
    assert car.service_book is True and car.origin_country == "Česká republika"
    assert car.seller_name == "AUTO IN s.r.o." and car.location is None
    assert car.description.startswith("č.123 CEBIA")
    assert "Android Auto" in car.equipment and len(car.equipment) == 50
    assert len(car.images) == 24 and car.images[0].endswith("/c7832f1110745b4c9b155106f4ae3f13.jpg")


def test_detail_of_a_partner_dealer(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("x", "u"), places)
    assert car is not None and car.title == "Ford Transit 2.0 EcoBlue chladící skříň"
    assert (car.year, car.mileage_km, car.price, car.power_kw) == (2019, 229600, 259000, 96)
    assert (car.body, car.drive, car.vat_deductible) == ("other", "front", True)
    assert car.vin == "WF0DXXTTGDKE17264" and car.seller_name == "Auto GARAGE"
    assert car.first_owner is True
    assert len(car.images) == 6
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_the_listing_and_details():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed(f"https://autoingarant.cz/vsechny-vozy/{KUGA}")
    assert not rules.allowed("https://autoingarant.cz/admin/")
