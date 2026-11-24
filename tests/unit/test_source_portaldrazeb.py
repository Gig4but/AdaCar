import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.scraper.sources import auction, portaldrazeb

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "portaldrazeb"
SOURCE = portaldrazeb.SOURCE


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


def test_listing_lots(places):
    """The fixture keeps 12 of the page's 50 lots; the count says more pages follow."""
    page = SOURCE.parse_listing(read("listing.json"), 1, places)
    assert page.has_next and len(page.leads) == 12
    lead = page.leads[1]
    assert lead.external_id == "Yk5JQ" and lead.fetch_url is None
    assert lead.url == "https://www.portaldrazeb.cz/drazba/091dd461-00-002-yk5jq"
    car = lead.card
    assert car is not None and car.title == "Škoda Octavia 2.0TDi 110kW DSG Style, tažné"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2023, 65000, 160000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.vin) == (
        "diesel", "automatic", 110, "TMBAJ8NX5PY127263",
    )  # fmt: skip
    lines = car.description.splitlines()
    assert lines[:2] == [
        "Dražba 25. 11. 2026 10:00 (elektronická)",
        "Nejnižší podání 160 000 Kč, odhadní cena 480 000 Kč.",
    ]
    assert car.expires_at == datetime(2026, 11, 25, 9, 0)
    assert car.seller_kind == "dealer" and car.seller_name == "Soudní exekutor Svobodová Eva JUDr."
    assert len(car.images) == 14 and car.images[0].startswith(
        "https://www.portaldrazeb.cz/media/cache/thumb_large/auctions/Yk5JQ/"
    )
    volvo = page.leads[2].card
    assert volvo is not None and (volvo.fuel, volvo.year) == ("petrol", 2022)  # "palivo BA"
    brno = page.leads[10].card
    assert brno is not None and brno.place_text == "Brno"
    assert brno.location is not None and brno.location.municipality == "Brno"
    assert brno.description.startswith("Dražba 26. 11. 2026 13:00, Brno")
    query = ScrapeQuery(kraje=[])
    unwanted = [x.external_id for x in page.leads if not SOURCE.wanted(x, query, places)]
    assert unwanted == ["ngOpY"]  # a Fiat Ducato van
    taycan = next(x.card for x in page.leads if x.external_id == "ngOyQ")
    assert taycan is not None and taycan.year is None
    lots = {x.external_id: x.card for x in page.leads}
    chevrolet, octavia = lots["lDO9b"], lots["lDOar"]
    assert chevrolet is not None and chevrolet.model == "HHR"
    assert octavia is not None and octavia.year == 2004


def test_last_page_running_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.json"), 2, places)
    assert not page.has_next and len(page.leads) == 5
    query = ScrapeQuery(kraje=[])
    assert not SOURCE.wanted(page.leads[0], query, places)  # an excavator
    lots = {x.external_id: x for x in page.leads}
    assert not SOURCE.wanted(lots["dbOlY"], query, places)
    bora, previa = lots["poOJR"].card, lots["dbOOP"].card
    assert bora is not None and previa is not None
    assert (bora.gearbox, previa.gearbox, previa.fuel) == (None, None, None)
    running = SOURCE.parse_listing(read("listing_running.json"), 1, places)
    assert not running.has_next and len(running.leads) == 2
    tiguan = running.leads[0].card
    assert tiguan is not None and (tiguan.mileage_km, tiguan.year) == (255080, 2018)
    empty = SOURCE.parse_listing('{"@count": 0}', 1, places)
    assert empty.none_listed and not empty.leads
    assert not SOURCE.parse_listing('{"error": {"message": "x"}}', 1, places).none_listed
    listings = SOURCE.listings(query)
    assert [region for region, _ in listings] == [None, None] and SOURCE.kind == "auction"
    url = listings[0][1](3)
    parts = urlsplit(url)
    assert parts.path == "/drazby/pripravovane.json"
    assert json.loads(parse_qs(parts.query)["filter"][0]) == {
        "category": [33], "limit": 50, "offset": 100, "sort": "asc", "sort_by": "start",
    }  # fmt: skip
    assert urlsplit(listings[1][1](1)).path == "/drazby/probihajici.json"


def test_auction_vin_and_summer_time():
    assert auction.notice_vin("VIN/číslo podvozku:\nWVWZZZ1JZ1W607735") == "WVWZZZ1JZ1W607735"
    assert auction.utc_to_prague(datetime(2027, 3, 28, 0, 30)) == datetime(2027, 3, 28, 1, 30)
    assert auction.utc_to_prague(datetime(2027, 3, 28, 1, 30)) == datetime(2027, 3, 28, 3, 30)
    assert auction.utc_to_prague(datetime(2027, 10, 31, 0, 30)) == datetime(2027, 10, 31, 2, 30)
    assert auction.utc_to_prague(datetime(2027, 10, 31, 1, 30)) == datetime(2027, 10, 31, 2, 30)
