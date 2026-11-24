from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autoinzerce

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autoinzerce"
SOURCE = autoinzerce.SOURCE


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
    assert page.has_next and len(page.leads) == 11
    lead = page.leads[0]
    assert lead.external_id == "35145"
    assert lead.url == lead.fetch_url == "https://www.auto-inzerce.cz/detail.php?id=35145"
    car = lead.card
    assert car is not None and car.title == "Prodám Honda Jazz 1.3i 57kW rok 2004"
    assert (car.make, car.year, car.mileage_km, car.price) == ("honda", 2004, 197700, 23500)
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("petrol", "manual", "hatchback", 57)
    assert car.description.startswith("Prodám Honda Jazz 1.3i 57kW rok 2004, TK do 9/2026")
    assert car.images == [
        "https://www.auto-inzerce.cz/sp-admin/small_imagens/1786608734_6a7d7c5e011a7.jpeg"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["34862"].title == "BMW 530D Xdrive Sport Line, po prvním majiteli, rok 20"
    assert cards["37030"].fuel == "lpg"  # "LPG + benzín"
    assert cards["35340"].year == 2012  # the form says 1900: the text tells
    # A link whose name part holds spaces: "&name=seat leon cupra 290".
    assert page.leads[-1].url == "https://www.auto-inzerce.cz/detail.php?id=1035"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 7, places)
    assert not page.has_next and len(page.leads) == 9
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=["Plzeňský kraj"]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.auto-inzerce.cz/vyhledavani.php?load=auto"
    assert url(3) == "https://www.auto-inzerce.cz/vyhledavani.php?load=auto&page=3"


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[0]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "35145"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "honda", "Jazz", 2004, 197700, 23500,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == (
        "petrol",
        "manual",
        "hatchback",
        "front",
    )
    assert (car.power_kw, car.engine_cc, car.doors, car.seats) == (57, 1246, 5, 5)
    assert car.condition == "used" and car.vin == "JHMGD57704S200663"
    assert car.description.endswith("dva klíče, cena 23500,-Kč.")
    assert car.place_text == "Jihomoravský kraj"
    assert car.location is not None and car.location.kraj == "Jihomoravský kraj"
    assert len(car.images) == 12 and "/sp-admin/medium_imagens/" in car.images[0]
    assert "not_offer" not in car.extra


def test_detail_sedan(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("1331", "u"), places)
    assert car is not None and car.title == "Škoda Octavia II Facelift 2.0 TDI"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2008, 277000, 139000,
    )  # fmt: skip
    assert (car.fuel, car.body, car.power_kw, car.vin) == (
        "diesel",
        "sedan",
        103,
        "TMBBE61Z59C002686",
    )
    assert car.description.startswith("Škoda Octavia II Facelift r.v.12/2008 103kw\nKód motoru")


def test_trailer_is_no_car_and_gone_page(places):
    car = SOURCE.parse_detail(read("detail_trailer.html"), Lead("36024", "u"), places)
    assert car is not None and car.extra["not_offer"] == "not_car"  # "Kategorie: Nákladní"
    assert car.make is None and car.model is None and car.price == 2900
    assert SOURCE.gone(read("detail_gone.html")) and not SOURCE.gone(read("detail_1.html"))
    assert SOURCE.parse_detail(read("detail_gone.html"), Lead("1", "u"), places) is None


def test_robots_txt_allows_what_is_read(places):
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    leads = SOURCE.parse_listing(read("listing.html"), 1, places).leads
    urls = [SOURCE.listing_page(1), SOURCE.listing_page(2)]
    urls += [u for lead in leads for u in (lead.url, lead.fetch_url) if u]
    assert leads and all(rules.allowed(u) for u in urls)
