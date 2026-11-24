from datetime import datetime
from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import KRAJ_SLUGS, Lead
from adacar.scraper.sources import skodadealers

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "skodadealers"
SOURCE = skodadealers.SOURCE
OCTAVIA = "https://prerost-a-svorc.skoda-auto.cz/ojete-vozy/skoda-octavia/tmbag8nx4sm015407/473482"
TUCSON = "https://louda.skoda-auto.cz/ojete-vozy/hyundai-tucson/tmajc811dmj084917/433174"


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


def test_sitemap_listing(places):
    page = SOURCE.parse_listing(read("sitemap.xml"), 1, places)
    assert not page.has_next and not page.none_listed and len(page.leads) == 174
    lead = page.leads[0]
    assert lead.external_id == "463643" and lead.card is None
    url = "https://prerost-a-svorc.skoda-auto.cz/ojete-vozy/audi-a6-avant/wauzzz4g1gn152064/463643"
    assert lead.url == lead.fetch_url == url
    assert lead.modified == datetime(2026, 10, 23, 0, 0)  # the end of 22 October (UTC)
    assert OCTAVIA in {x.url for x in page.leads}
    assert len({x.external_id for x in page.leads}) == 174
    empty = SOURCE.parse_listing(read("sitemap_empty.xml"), 1, places)
    assert not empty.leads and empty.none_listed and not empty.has_next
    broken = SOURCE.parse_listing("<html>Chyba</html>", 1, places)
    assert not broken.leads and not broken.none_listed


def test_listings_by_region():
    assert SOURCE.listings(ScrapeQuery(kraje=[])) == []
    listings = SOURCE.listings(ScrapeQuery(kraje=["Karlovarský kraj"]))
    assert [url(1) for _, url in listings] == [
        "https://autospektrum2000.skoda-auto.cz/sitemap.xml",
        "https://cerveny.skoda-auto.cz/sitemap.xml",
        "https://schoner.skoda-auto.cz/sitemap.xml",
    ]
    assert {region for region, _ in listings} == {"Karlovarský kraj"}
    # A dealer with branches in two regions in scope is listed under each of them.
    both = SOURCE.listings(ScrapeQuery(kraje=["Hlavní město Praha", "Středočeský kraj"]))
    brejla = [region for region, url in both if "brejla" in url(1)]
    assert brejla == ["Hlavní město Praha", "Středočeský kraj"]
    everywhere = SOURCE.listings(ScrapeQuery(kraje=list(KRAJ_SLUGS)))
    assert len({url(1) for _, url in everywhere}) == len(skodadealers.HOSTS) == 113
    assert set(skodadealers.REGIONS.values()) == set(KRAJ_SLUGS)
    assert all(set(codes) <= set(skodadealers.REGIONS) for codes in skodadealers.HOSTS.values())


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("473482", OCTAVIA), places)
    assert car is not None and car.external_id == "473482" and car.url == OCTAVIA
    assert car.title == "Škoda Octavia 2.0 85kW"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2024, 27660, 559900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.engine_cc, car.drive) == (
        "diesel", "manual", 85, 1968, None,
    )  # fmt: skip
    assert car.vat_deductible is True and car.condition == "used"
    assert car.vin == "TMBAG8NX4SM015407" and car.color == "stříbrná"
    assert car.first_owner is True and car.service_book is True
    assert car.origin_country == "Česká republika"
    assert car.seller_kind == "dealer" and car.seller_name == "Přerost a Švorc - auto, s.r.o."
    assert car.place_text == "Praha 6"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.description.startswith("Prodloužená záruka do 12/2029")
    assert car.description.endswith("Stav vozu: Velmi dobrý po opravě")
    assert len(car.equipment) == 52 and "Bluetooth" in car.equipment
    assert len(car.images) == 4
    assert all("/usedcars/27570/tmbag8nx4sm015407/" in u and "?" not in u for u in car.images)
    assert car.contact == {"phone": "+420 220 000 101"}


def test_detail_hybrid_4x4(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("433174", TUCSON), places)
    assert car is not None and car.title == "Hyundai Tucson 1.6 169kW"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "hyundai", "Tucson", 2022, 37538, 579990,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.drive, car.power_kw) == ("hybrid", "automatic", "4x4", 169)
    assert car.vat_deductible is None and car.vin == "TMAJC811DMJ084917"
    assert car.seller_name == "Louda Auto a.s." and car.place_text == "Teplice"
    assert car.location is None  # not among the test municipalities
    assert car.description.endswith("Stav vozu: Dobrý") and len(car.equipment) == 48
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_all():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.sitemap_url("prerost-a-svorc", 1))
    assert rules.allowed(OCTAVIA)


def test_models_after_long_makes():
    assert skodadealers.model_name("Alfa Romeo Giulia", "alfa-romeo") == "Giulia"
    assert skodadealers.model_name("Land Rover Defender 110", "land-rover") == "Defender 110"


def test_zero_km_is_a_mileage():
    assert skodadealers._km("\xa00\xa0km") == 0 and skodadealers._km(None) is None
