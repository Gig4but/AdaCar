from datetime import datetime
from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import sportovnivozy

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "sportovnivozy"
SOURCE = sportovnivozy.SOURCE


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
    assert page.has_next and len(page.leads) == 30
    lead = page.leads[0]
    assert lead.external_id == "261734"
    assert lead.url == lead.fetch_url == "https://www.sportovnivozy.cz/261734-bmw-8-gran-coupe-2021"
    car = lead.card
    assert car is not None and car.title == "BMW 8 Gran Coupe"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "bmw", "8", 2021, 167000, 985000,
    )  # fmt: skip
    assert (car.engine_cc, car.power_kw) == (2993, 250)
    assert car.images == [
        "https://www.sportovnivozy.cz/auta/261734/hlavni_m320.jpg?time=1795513503"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    electric = cards["261733"]  # no engine size; "NÁŠ TIP" next to the price
    assert (electric.model, electric.engine_cc, electric.price) == ("ID.3", None, 650000)
    assert cards["261730"].make == "mercedes-benz" and cards["261730"].model == "C"
    no_year = cards["261710"]  # "270 000 Km" only
    assert (no_year.year, no_year.mileage_km, no_year.power_kw) == (None, 270000, None)


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 248, places)
    assert not page.has_next and len(page.leads) == 21
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert SOURCE.expire_after_days == 3
    assert url(1) == "https://www.sportovnivozy.cz/novinky"
    assert url(3) == "https://www.sportovnivozy.cz/index.php?akce=novinky&strana=3"


def test_detail_private(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[0]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "261734"
    assert car.title == "BMW 8 Gran Coupe"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "bmw", "8", 2021, 167000, 985000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.drive, car.body) == ("diesel", "automatic", "4x4", None)
    assert (car.power_kw, car.engine_cc, car.color) == (250, 2993, "Zelená")
    assert car.vin == "WBAGW01020CG32475"  # from the link to a history check
    assert car.vat_deductible is True  # "Možnost odpočtu"
    assert car.posted_at == datetime(2026, 11, 24)
    assert car.seller_kind == "private" and car.seller_name == "Tomáš Dvořák"
    assert car.place_text == "Frenštát p. Radhoštěm"
    assert car.description.startswith("Nabízím k prodeji 840d\nR.v 2021")
    assert len(car.images) == 17 and car.images[0].endswith("/auta/261734/hlavni_v.jpg")
    assert not SOURCE.gone(read("detail_1.html"))


def test_detail_dealer(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[1]
    car = SOURCE.parse_detail(read("detail_2.html"), lead, places)
    assert car is not None and car.external_id == "261733"
    assert car.title == "Volkswagen ID.3 Pro (62 kWh), SoH 92%"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volkswagen", "ID.3", 2024, 27343, 650000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == (
        "electric",
        "automatic",
        "hatchback",
        150,
    )
    assert car.vat_deductible is True and car.vin is None
    assert car.seller_kind == "dealer" and car.seller_name == "Mototechna Group s.r.o."
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.location.address_text == "Kolbenova 809/31, Praha"
    assert len(car.equipment) == 14 and car.equipment[0] == "hlídání jízdního pruhu"
    assert len(car.images) == 19


def test_sold_car_is_gone(places):
    assert SOURCE.gone(read("detail_archived.html"))
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_what_is_read(places):
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    leads = SOURCE.parse_listing(read("listing.html"), 1, places).leads
    urls = [SOURCE.listing_page(1), SOURCE.listing_page(2)]
    urls += [u for lead in leads for u in (lead.url, lead.fetch_url) if u]
    assert leads and all(rules.allowed(u) for u in urls)
    assert not rules.allowed("https://www.sportovnivozy.cz/novinky?razeni=cena")


def test_ads_marked_sold(places):
    leads = SOURCE.parse_listing(read("listing_last.html"), 2, places).leads
    sold = {lead.external_id for lead in leads if lead.card and lead.card.extra.get("not_offer")}
    assert sold == {"108173", "86067", "92832"}
    for title in ("BMW M3 E46 SOLD!", "Porsche 911 Carrera, prodáno v ČR"):
        car = sportovnivozy.ParsedCar("1", title)
        sportovnivozy.mark_sold(car)
        assert bool(car.extra) == title.endswith("!")


class Places:
    def resolve(self, text, street=None, postal_code=None, region=None, district=None):
        return (text, street, postal_code, region)


@pytest.mark.parametrize(
    ("kraj", "address", "place"),
    [
        (
            "Praha",
            "Freyova 980/10,<br>Praha 9, 190 00, Vysočany",
            ("Praha 9", "Freyova 980/10", "190 00", "Praha"),
        ),
        ("Zlínský", "Hřivínův Újezd 199", ("Hřivínův Újezd", None, None, "Zlínský")),
    ],
)
def test_seller_address(kraj, address, place):
    html = (
        '<table><tr><td class="vt">Prodávající: <h3>X</h3> Kraj: '
        f"{kraj}\n<strong>{address}</strong></td></tr></table>"
    )
    car = sportovnivozy.ParsedCar("1", "t")
    SOURCE.add_seller(car, sportovnivozy.LexborHTMLParser(html), Places())  # type: ignore[arg-type]
    assert car.location == place


def test_equipment_between_stars(places):
    car = SOURCE.parse_detail(read("detail_archived.html"), sportovnivozy.Lead("1", "u"), places)
    assert car is not None and car.extra.get("not_offer") == "sold"
    assert car is not None and len(car.equipment) > 5
    assert all("*" not in item for item in car.equipment)


def test_great_wall_model():
    assert sportovnivozy.model_name("Great Wall Haval H6", "gwm") == "Haval"


def test_series_model():
    assert sportovnivozy.model_name("Mercedes-Benz Třída C 63 AMG", "mercedes-benz") == "Třída C"


def test_make_words_after_the_make():
    assert sportovnivozy.model_name("GWM Great Wall Motors Steed", "gwm") == "Steed"
    assert sportovnivozy.model_name("Mercedes-AMG GT 63 S", "mercedes-benz") == "AMG"
