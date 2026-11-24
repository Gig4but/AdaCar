from datetime import datetime
from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import aukro

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "aukro"
SOURCE = aukro.SOURCE


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
    assert page.has_next and len(page.leads) == 60
    lead = page.leads[0]
    assert lead.external_id == "7142620323"
    assert (
        lead.url
        == lead.fetch_url
        == ("https://aukro.cz/vw-bora-1-9-tdi-110-kw-arl-plne-pojizdne-bez-stk-od-1-kc-7142620323")
    )
    car = lead.card
    assert car is not None and car.title.startswith("VW Bora 1.9 TDI 110 kW (ARL)")
    assert (car.make, car.model, car.year, car.mileage_km) == ("volkswagen", "Bora", 1998, 280000)
    assert car.price is None and car.extra == {"auction": True}
    assert car.expires_at == datetime(2026, 11, 30, 19, 20, 26)  # in UTC
    assert (car.gearbox, car.body, car.drive, car.power_kw, car.engine_cc) == (
        "manual",
        "sedan",
        "front",
        110,
        1986,
    )
    assert (car.color, car.doors, car.condition) == ("Zelená", 5, "used")
    assert len(car.equipment) == 12 and car.equipment[0] == "ABS"
    assert car.seller_kind == "private" and car.seller_name == "jan.novak77"
    assert car.place_text == "Dačice" and car.images[0].startswith("https://cdn.aukro.cz/")
    cards = {x.external_id: x.card for x in page.leads if x.card}
    fixed = cards["7142726839"]  # "Kup teď" only
    assert fixed.price == 22000 and "auction" not in fixed.extra and fixed.fuel == "cng"
    both = cards["7142188980"]  # an auction that can also be bought at once
    assert both.price == 55000 and both.extra == {"auction": True} and both.fuel == "lpg"
    assert cards["7142210505"].price is None  # a bid of 156 Kč is no price
    assert cards["7142682797"].condition == "damaged" and cards["7142682797"].model == "308"
    assert cards["7141984411"].seller_kind == "dealer"
    assert cards["7142419097"].model == "Proace"  # "7místná Toyota Proace City Verso"
    assert cards["7142408983"].vin == "TMADB81CP9J005212"
    prague = cards["7142698598"]  # "Praha 5", 15800
    assert prague.location is not None and prague.location.municipality == "Praha"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 10, places)
    assert not page.has_next and len(page.leads) == 17
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=["Plzeňský kraj"]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://aukro.cz/osobni-automobily"
    assert url(3) == "https://aukro.cz/osobni-automobily?page=3"
    assert SOURCE.parse_listing("<html></html>", 1, places).leads == []


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[0]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "7142620323"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volkswagen", "Bora", 1998, 280000, None,
    )  # fmt: skip
    assert car.description.startswith("Nabízím k dražbě Volkswagen Bora")
    assert len(car.equipment) == 12 and "Klimatronic" in car.equipment
    assert len(car.images) == 8 and "/thumbnail/" not in car.images[0]
    assert car.extra == {"auction": True} and not SOURCE.gone(read("detail_1.html"))
    ended = read("detail_1.html").replace('"state": "ACTIVE"', '"state": "ENDED"')
    assert SOURCE.gone(ended)
    car = SOURCE.parse_detail(ended, Lead("1", "u"), places)
    assert car is not None and car.extra.get("not_offer") == "sold"


def test_detail_reads_the_text(places):
    """The page's form has only the condition: the text gives year, mileage, power, VIN."""
    lead = next(
        x
        for x in SOURCE.parse_listing(read("listing.html"), 1, places).leads
        if x.external_id == "7141784340"
    )
    car = SOURCE.parse_detail(read("detail_2.html"), lead, places)
    assert car is not None and car.title == "Genesis GV70 2.2CRDI,AWD,SPORT LINE,DPH,4x4 nafta"
    assert (car.year, car.mileage_km, car.power_kw, car.price) == (2021, 90000, 148, 798000)
    assert (car.fuel, car.gearbox, car.vin) == ("diesel", "automatic", "KMTMB81ADMU006161")
    assert "auction" not in car.extra
    assert car.location is not None and car.location.municipality == "Brno"
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_what_is_read(places):
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    leads = SOURCE.parse_listing(read("listing.html"), 1, places).leads
    urls = [SOURCE.listing_page(1), SOURCE.listing_page(2)]
    urls += [u for lead in leads for u in (lead.url, lead.fetch_url) if u]
    assert leads and all(rules.allowed(u) for u in urls)
    assert not rules.allowed("https://aukro.cz/nove-nabidky?page=2")


def test_bids_are_no_price_and_slovak_sellers_skipped(places):
    leads = SOURCE.parse_listing(read("listing.html"), 1, places).leads
    leads += SOURCE.parse_listing(read("listing_last.html"), 10, places).leads
    cards = {lead.external_id: lead.card for lead in leads if lead.card}
    assert (cards["7124496056"].make, cards["7124496056"].model) == ("gwm", "Steed")
    assert aukro.model_name("Hyundai Genesis Coupe 3.8", "hyundai") == "Genesis"
    auctions = [c for c in cards.values() if c.extra.get("auction")]
    assert any(c.price is None for c in auctions)
    assert all(c.price is None or c.price >= 1000 for c in auctions)
    query = ScrapeQuery(kraje=[])
    abroad = [lead for lead in leads if lead.card and lead.card.extra.get("abroad")]
    assert abroad and not any(SOURCE.wanted(lead, query, places) for lead in abroad)
    assert SOURCE.wanted(leads[0], query, places)
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("1", "u"), places)
    assert car is not None and car.make == "genesis"


def test_hyphenated_makes():
    assert aukro.model_name("Mercedes-Benz C 220 CDI", "mercedes-benz") == "C"
    assert aukro.model_name("Prodám Land-Rover Defender 110", "land-rover") == "Defender"


def test_series_models():
    assert aukro.model_name("BMW Řada 3 320d Touring", "bmw") == "Řada 3"


def test_no_engine_size_as_model():
    assert aukro.model_name("Mercedes-Benz 3.0cdi", "mercedes-benz") is None
    assert aukro.model_name("Great Wall Motors Steed 5", "gwm") == "Steed"
