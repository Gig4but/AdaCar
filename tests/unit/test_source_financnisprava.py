from datetime import datetime
from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import financnisprava

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "financnisprava"
SOURCE = financnisprava.SOURCE
DETAIL = (
    "https://financnisprava.gov.cz/cs/financni-sprava/drazby-financni-spravy/drazby-prehled"
    "/app/detail/{}/APED"
)


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
    assert page.has_next and len(page.leads) == 10
    lead = page.leads[1]
    assert lead.external_id == "261249"
    assert lead.url == lead.fetch_url == DETAIL.format("261249")
    car = lead.card
    assert car is not None and car.title == "BMW 530D XDRIVE"
    assert (car.make, car.model, car.price) == ("bmw", "530D", None)
    assert car.description == (
        "Elektronická dražba 26. 11. 2026 9:00, Finanční úřad pro hlavní město Prahu"
    )
    assert car.expires_at == datetime(2026, 11, 26, 8, 0)
    assert (car.seller_kind, car.seller_name) == ("dealer", "Finanční úřad pro hlavní město Prahu")
    assert car.location is not None and car.location.kraj == "Hlavní město Praha"
    query = ScrapeQuery(kraje=[])
    unwanted = [x.card.title for x in page.leads if x.card and not SOURCE.wanted(x, query, places)]
    assert unwanted == ["Přívěsný vozík KLABO", "Volkswagen Transporter"]


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 3, places)
    assert not page.has_next and [x.external_id for x in page.leads] == ["261280"]
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.kind == "auction"
    listing = (
        "https://financnisprava.gov.cz/cs/financni-sprava/drazby-financni-spravy/drazby-prehled"
    )
    assert url(1) == listing + "?stav=1&druh=DRAZBA_TYP_MOT"
    assert url(3) == listing + "?page=3&stav=1&druh=DRAZBA_TYP_MOT"


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[2]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "261303"
    assert (car.make, car.model, car.price) == ("skoda", "OCTAVIA", 10000)
    assert car.seller_name == "Finanční úřad pro Olomoucký kraj"
    lines = car.description.splitlines()
    assert lines[:2] == ["Dražba 1. 12. 2026 8:30, Přerov", "Nejnižší podání 10 000 Kč"]
    assert lines[-1].startswith("Místo vyzvednutí: Dvořákova 1, 750 02 Přerov")
    assert car.place_text == "Přerov" and car.expires_at == datetime(2026, 12, 1, 7, 30)
    assert car.location is not None and car.location.kraj == "Olomoucký kraj"  # by the office


def test_detail_in_prague(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("261249", "u"), places)
    assert car is not None and (car.make, car.model, car.price) == ("bmw", "530D", 150000)
    assert car.description.startswith(
        "Dražba 26. 11. 2026 9:00, Finanční úřad pro hlavní město Prahu\n"
    )
    assert car.location is not None and car.location.kraj == "Hlavní město Praha"
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None
