from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import automodul

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "automodul"
SOURCE = automodul.SOURCE


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
    assert page.has_next and len(page.leads) == 100
    lead = page.leads[0]
    assert lead.external_id == "3787392"
    assert (
        lead.url
        == lead.fetch_url
        == "https://www.automodul.cz/bmw-rada-5-530e-xdrive-touring-c3787392"
    )
    car = lead.card
    assert car is not None and car.title == "BMW Řada 5 530e xDrive Touring"
    assert (car.make, car.year, car.mileage_km, car.price) == ("bmw", 2026, 6, 1850000)
    assert (car.fuel, car.body, car.engine_cc, car.power_kw) == ("petrol", "combi", 1998, 220)
    assert car.place_text == "Liberec"
    assert car.images == [
        "https://sta.automodul.cz/data/auta_fotky/fotky/7469/2/9/3/3787392-1-1791537434-velky.jpeg"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["3781564"].model == "X3" and cards["3781564"].body == "suv"
    old = cards["1972243"]  # "Cena dohodou", no power given ("0 kW")
    assert old.price is None and old.power_kw is None and old.year == 1984
    assert cards["3459457"].body == "offroad" and cards["3459457"].mileage_km is None
    assert cards["1971816"].images == []  # "Bez fotky"


def test_card_place_is_a_district(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 325, places)
    card = next(x.card for x in page.leads if x.card and x.card.place_text == "Hlavní město Praha")
    assert card.location is not None and card.location.kraj == "Hlavní město Praha"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 325, places)
    assert not page.has_next and len(page.leads) == 51  # its "Další" link is greyed out
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=["Plzeňský kraj"]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.automodul.cz/osobni/?order=dt_created__ASC&pocet_inz=100"
    assert url(3) == (
        "https://www.automodul.cz/osobni/?order=dt_created__ASC&pocet_inz=100&stranka=3"
    )
    assert not SOURCE.default_enabled


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[1]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "3809064"
    assert car.title == "BMW Řada 2 218i Gran Coupé"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "bmw", "Řada 2", 2023, 74000, 569000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.condition) == (
        "petrol",
        "automatic",
        "coupe",
        "used",
    )
    assert (car.engine_cc, car.power_kw, car.color) == (1499, 100, "modrá metalíza")
    assert car.vin == "WBA11AK0X07M66043" and car.drive is None
    assert car.seller_kind == "dealer" and car.seller_name == "CarTec Liberec, s.r.o."
    assert car.place_text == "Liberec 11"
    assert "autorizovaného dealera BMW" in car.description
    assert len(car.equipment) == 18 and "vyhřívaný volant" in car.equipment
    assert car.images == []  # no photos: only the site's placeholder


def test_detail_with_address(places):
    lead = SOURCE.parse_listing(read("listing_last.html"), 325, places).leads[49]
    car = SOURCE.parse_detail(read("detail_2.html"), lead, places)
    assert car is not None and car.external_id == "3822880"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Kodiaq", 2024, 64017, 930000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("diesel", "automatic", "suv", "4x4")
    assert car.vin is None  # "neuveden"
    assert car.seller_name == "Mototechna Group s.r.o" and car.place_text == "Praha"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.location.address_text == "Kolbenova 809/31, Praha"
    assert len(car.images) == 26
    assert car.images[0].endswith("/3822880-1-1795507883-velky.jpeg")
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_what_is_read(places):
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    leads = SOURCE.parse_listing(read("listing.html"), 1, places).leads
    urls = [SOURCE.listing_page(1), SOURCE.listing_page(2)]
    urls += [u for lead in leads for u in (lead.url, lead.fetch_url) if u]
    assert leads and all(rules.allowed(u) for u in urls)
    assert not rules.allowed("https://www.automodul.cz/skoda-octavia-c123?pocet_inz=100")


def test_card_models():
    assert automodul.card_model("BMW Řada 2 218d Active Tourer", "bmw") == "Řada 2"
    assert automodul.card_model("Peugeot 5008 1.5 BlueHDi", "peugeot") == "5008"
    assert automodul.card_model("Mercedes-Benz Třída C 220d", "mercedes-benz") == "Třída C"
    assert automodul.card_model("VW Golf 1.4 TSI", "volkswagen") == "Golf"


def test_ads_that_sell_no_car(places):
    leads = SOURCE.parse_listing(read("listing.html"), 1, places).leads
    cards = {lead.external_id: lead.card for lead in leads}
    assert cards["3587751"].extra == {"not_offer": "not_car"}
    assert cards["3809657"].extra == {}
    assert SOURCE.expire_after_days == 3


def test_placeholder_mileage(places):
    leads = SOURCE.parse_listing(read("listing.html"), 1, places).leads
    cards = {x.external_id: x.card for x in leads}
    assert cards["3809655"].mileage_km is None


def test_great_wall_models():
    assert automodul.card_model("Great Wall Haval H6 2.0", "gwm") == "Haval"
    assert automodul.model_of("Great Wall Haval H6", "gwm") == "Haval H6"


def test_make_words_after_the_make():
    assert automodul.card_model("Great Wall Motors Steed 5", "gwm") == "Steed"
    assert automodul.card_model("Mercedes-AMG GT 63 S", "mercedes-benz") == "AMG"
