import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import exdrazby

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "exdrazby"
SOURCE = exdrazby.SOURCE


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
    page = SOURCE.parse_listing(read("listing.json"), 1, places)
    assert page.has_next and len(page.leads) == 15
    a7 = next(x.card for x in page.leads if x.external_id == "33318")
    assert a7 is not None and a7.fuel == "diesel"
    lead = next(x for x in page.leads if x.external_id == "33433")
    assert lead.url == "https://www.exdrazby.cz/drazba/33433"
    assert lead.fetch_url == "https://exdrazby.cz/api/web/auction/33433?type=auction_public"
    car = lead.card
    assert car is not None and car.title == "Dražba Škoda Octavia 2.0 TDI 110 kW DSG"
    assert (car.make, car.model, car.price, car.power_kw) == ("skoda", "Octavia", 101400, 110)
    assert (car.fuel, car.gearbox, car.seller_kind) == ("diesel", "automatic", "dealer")
    assert car.description == "Dražba 3. 12. 2026 10:00, Praha"
    assert car.expires_at == datetime(2026, 12, 3, 9, 30)  # the end of the bidding, 10:30 CET
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.images and car.images[0].startswith("https://cdn.exdrazby.cz/")
    tender = page.leads[0]
    assert tender.url == "https://www.exdrazby.cz/verejna-soutez/33301"
    assert tender.card is not None and tender.card.description.startswith("Veřejná soutěž 24. 11.")
    query = ScrapeQuery(kraje=[])
    unwanted = {x.external_id for x in page.leads if not SOURCE.wanted(x, query, places)}
    assert unwanted == {"33306", "33370", "33313", "33676"}  # Trafic and Jumpy vans


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.json"), 2, places)
    assert not page.has_next and len(page.leads) == 14
    empty = SOURCE.parse_listing('{"data": [], "total": 0}', 1, places)
    assert empty.none_listed and not empty.leads
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.kind == "auction"
    params = parse_qs(urlsplit(url(2)).query)
    assert params["page"] == ["2"] and params["perPage"] == ["15"]
    assert json.loads(params["sort"][0]) == {"property": "auction.startDt", "direction": "ASC"}
    statuses, category = json.loads(params["filter"][0])
    assert [s["value"] for s in statuses] == ["prepared", "ongoing"]
    assert category["property"] == "auctionCategory.id" and category["value"] == 17


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.json"), Lead("33433", "u"), places)
    assert car is not None and car.url == "https://www.exdrazby.cz/drazba/33433"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2018, 210330, 101400,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw) == ("diesel", "automatic", 110)
    assert car.vin == "TMBAJ7NE5K0069624"
    assert (car.seller_kind, car.seller_name, car.seller_ico) == (
        "dealer", "KOVA Group a.s.", "27411672",
    )  # fmt: skip
    lines = car.description.splitlines()
    assert lines[0] == "Dražba 3. 12. 2026 10:00, Praha"
    assert lines[1] == "Vyvolávací cena 101 400 Kč, odhad 152 000 Kč."
    assert "Prohlídka: 1. termín" in car.description
    assert car.expires_at == datetime(2026, 12, 3, 9, 30)
    assert car.location is not None and car.location.municipality == "Praha"
    assert len(car.images) == 16


def test_public_tender(places):
    car = SOURCE.parse_detail(read("detail_2.json"), Lead("33439", "u"), places)
    assert car is not None and car.url == "https://www.exdrazby.cz/verejna-soutez/33439"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Fabia", 2018, 115000, 95000,
    )  # fmt: skip
    assert (car.fuel, car.body, car.power_kw, car.vin) == (
        "petrol", "combi", 55, "TMBJB6NJ5JZ190412",
    )  # fmt: skip
    assert car.description.startswith("Veřejná soutěž 1. 12. 2026 11:00, Ivančice\n")
    assert "Nejnižší nabídka 95 000 Kč." in car.description
    assert car.seller_name == "Juris Real Dražby, a.s"
    assert SOURCE.parse_detail('{"data": null}', Lead("1", "u"), places) is None
