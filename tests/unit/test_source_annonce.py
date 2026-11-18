from pathlib import Path

import pytest

from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import annonce
from adacar.scraper.sources.annonce import Annonce

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "annonce"


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


def lead(places, external_id: str) -> Lead:
    page = annonce.SOURCE.parse_listing(read("listing.html"), 1, places)
    return next(x for x in page.leads if x.external_id == external_id)


def test_is_slow_like_robots_asks():
    assert annonce.SOURCE.delay == 20.0
    assert "Crawl-delay: 20" in read("robots.txt")


def test_listing_cards(places):
    page = annonce.SOURCE.parse_listing(read("listing.html"), 1, places)
    assert page.has_next and len(page.leads) == 20
    golf = page.leads[0]  # a paid ad leads the page
    assert golf.external_id == "89790733"
    assert golf.url == "https://www.annonce.cz/inzerat/vw-golf-wr6-89790733-wewd75.html"
    car = golf.card
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km) == ("volkswagen", "Golf", 1993, 210000)
    assert car.price is None  # "Cena dohodou"
    assert car.posted_at is not None and car.posted_at.date().isoformat() == "2026-11-11"
    octavia = lead(places, "89879915").card
    assert octavia is not None
    assert (octavia.make, octavia.year, octavia.price) == ("skoda", 2018, 190000)
    assert octavia.location is not None and octavia.location.kraj == "Hlavní město Praha"


def test_next_page(places):
    assert Annonce.listing_page(1) == "https://www.annonce.cz/osobni-auta.html"
    assert Annonce.listing_page(2) == "https://www.annonce.cz/osobni-auta.html?page=2"
    page = annonce.SOURCE.parse_listing(read("listing_2.html"), 2, places)
    assert page.has_next and len(page.leads) == 20
    assert not annonce.SOURCE.parse_listing(read("listing_2.html"), 9, places).has_next


def test_dealer_detail(places):
    car = annonce.SOURCE.parse_detail(read("detail_1.html"), lead(places, "89879915"), places)
    assert car is not None
    assert car.title == "Škoda Octavia III, 1.6 TDi, kombi, nafta"
    assert (car.make, car.model, car.year, car.mileage_km) == ("skoda", "Octavia", 2018, 204778)
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("diesel", "manual", "combi", 85)
    assert (car.price, car.vat_deductible, car.condition) == (190000, False, "used")
    assert (car.seller_kind, car.seller_name) == ("dealer", "Auto ESA")
    assert car.location is not None and car.location.precision == "address"
    assert car.posted_at is not None and car.expires_at is not None
    assert car.posted_at.isoformat() == "2026-11-17T14:46:43"
    assert len(car.images) == 3 and car.description.startswith("Škoda Octavia III")


def test_private_detail(places):
    car = annonce.SOURCE.parse_detail(read("detail_2.html"), lead(places, "89744387"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km) == ("hyundai", "Getz", 2005, 142200)
    assert (car.price, car.engine_cc, car.crashed) == (12000, 1086, False)
    assert car.seller_kind == "private" and car.seller_name is None
    assert car.place_text == "Litvínov" and car.images


def test_wanted_ads_are_marked():
    car = annonce.ParsedCar("1", "Škoda Octavia")
    annonce.mark_wanted(car, "Poptávka")
    assert car.extra["not_offer"] == "wanted"


def test_robots_allow_the_pages_used():
    robots = RobotsRules.parse(read("robots.txt"), "adacar")
    assert robots.allowed(Annonce.listing_page(1)) and robots.allowed(Annonce.listing_page(4))
    assert robots.allowed(
        "https://www.annonce.cz/inzerat/skoda-octavia-iii-1-6-89879915-w3j4ad.html"
    )
