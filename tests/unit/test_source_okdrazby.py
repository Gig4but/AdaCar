from datetime import datetime
from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import auction, okdrazby

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "okdrazby"
SOURCE = okdrazby.SOURCE


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
    assert page.has_next and len(page.leads) == 15
    query = ScrapeQuery(kraje=[])
    unwanted = [x.external_id for x in page.leads if not SOURCE.wanted(x, query, places)]
    assert unwanted == ["29854"]  # a Fiat Ducato van
    lead = page.leads[1]
    assert lead.external_id == "29807"
    assert (
        lead.url == lead.fetch_url == "https://okdrazby.cz/drazba/29807-skoda-kodiaq-2-0-tdi-style"
    )
    car = lead.card
    assert car is not None and car.title.startswith("Škoda Kodiaq 2.0 TDI, Style")
    assert (car.make, car.model, car.price, car.power_kw) == ("skoda", "Kodiaq", 149000, 140)
    assert (car.fuel, car.gearbox, car.seller_kind) == ("diesel", "automatic", "dealer")
    assert car.description == "Dražba 27. 11. 2026 9:00, Litoměřice"
    assert car.expires_at == datetime(2026, 11, 27, 8, 0)  # 9:00 CET
    assert car.images and car.images[0].startswith("https://")
    prague = page.leads[2].card
    assert prague is not None and (prague.make, prague.model) == ("land-rover", "Sport")
    assert prague.location is not None and prague.location.kraj == "Hlavní město Praha"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 3, places)
    assert not page.has_next and len(page.leads) == 9
    assert page.leads[0].card is not None and page.leads[0].card.year == 2004
    query = ScrapeQuery(kraje=[])
    unwanted = [x.external_id for x in page.leads if not SOURCE.wanted(x, query, places)]
    assert unwanted == ["30424", "30421"]  # a Renault Trafic and a motorcycle
    [(region, url)] = SOURCE.listings(query)
    assert region is None and SOURCE.nationwide and SOURCE.kind == "auction"
    assert url(1) == "https://okdrazby.cz/drazby/movity/automobily"
    assert url(3) == "https://okdrazby.cz/drazby/movity/automobily?page=3"


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[1]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "29807"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Kodiaq", 2020, 208639, 149000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw) == ("diesel", "automatic", 140)
    assert car.vin == "TMBLK7NS4L8060346" and car.vat_deductible is True
    assert car.seller_kind == "dealer" and car.seller_name == "OK dražebník a.s."
    lines = car.description.splitlines()
    assert lines[0] == "Dražba 27. 11. 2026 9:00 (online), Litoměřice"
    assert lines[1] == (
        "Nejnižší podání 149 000 Kč, odhadní cena 449 000 Kč, dražební jistota nepožadována."
    )
    assert "Roudnice nad Labem" in car.description
    assert car.expires_at == datetime(2026, 11, 27, 8, 30)  # the earliest end, 9:30 CET
    assert len(car.images) > 20 and car.images[0].endswith("/29807/images/main")


def test_detail_in_prague(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("30563", "u"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2011, 295985, 6000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.vin) == ("diesel", "manual", "TMBHT61ZXB2144821")
    assert car.description.startswith("Dražba 3. 12. 2026 10:00 (online), Hlavní město Praha")
    assert car.location is not None and car.location.kraj == "Hlavní město Praha"
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_auction_helpers():
    assert auction.auction_time("Zahájení 3. 12. 2026 10:00") == datetime(2026, 12, 3, 9, 0)
    assert auction.auction_time("27.7.2026 v 9:30") == datetime(2026, 7, 27, 7, 30)  # CEST
    assert auction.auction_time("1. 1. 2027") == datetime(2026, 12, 31, 23, 0)
    assert auction.auction_time("brzy") is None
    assert auction.model_after_make("Dražba vozidla AUDI A4") == "A4"
    assert auction.model_after_make("Mercedes Benz CLS 350CDi") == "CLS"
    assert auction.model_after_make("Osobní automobil") is None
    assert auction.not_a_car("FIAT DUCATO, r.v. 2013") and auction.not_a_car("Přívěsný vozík")
    assert not auction.not_a_car("Škoda Octavia Combi")
    assert not auction.not_a_car("Subaru Forester 2.0 Boxer Diesel")
    notice = "Datum první registrace nebo rok výroby vozidla: 01.07.2013"
    assert auction.notice_year(notice) == 2013
    vans = ("Peugeot Boxer 2.2", "Citroën Jumpy", "Opel Vivaro", "Renault Master", "PEUGEOT EXPERT")
    assert all(auction.not_a_car(van) for van in vans)
