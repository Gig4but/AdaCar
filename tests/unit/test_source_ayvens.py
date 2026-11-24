from datetime import datetime
from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.sources import ayvens

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "ayvens"
SOURCE = ayvens.SOURCE


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


def test_listing_records(places):
    page = SOURCE.parse_listing(read("listing.json"), 1, places)
    assert page.has_next and not page.none_listed and len(page.leads) == 100
    electric = [x.card for x in page.leads if x.card and x.card.fuel == "electric"]
    assert electric and all(car.engine_cc is None for car in electric)
    lead = page.leads[0]
    assert lead.external_id == "91771" and lead.fetch_url is None  # the record is all there is
    assert lead.url == "https://drive.ayvens.cz/cs/bazar/91771-Skoda-Karoq_23_5_dv_SUV"
    car = lead.card
    assert car is not None and car.title == "Škoda Karoq 1.5 TSI 110kW Style"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Karoq", 2023, 60777, 499000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "petrol", "manual", "suv", 110, 1498,
    )  # fmt: skip
    assert (car.color, car.doors, car.seats, car.vin) == ("bílá", 5, 5, "TMBJR7NU5P5050016")
    assert car.vat_deductible is True and car.posted_at == datetime(2026, 7, 10, 15, 41, 20)
    assert car.seller_kind == "dealer" and car.seller_name == "Ayvens"
    assert car.place_text == "Praha 10"
    assert car.location is not None and car.location.municipality == "Praha"
    assert len(car.images) == 11  # the main photo first
    assert car.images[0] == (
        "https://drive-api.myayvens.cz/api/assets/external/8db8e560-6a43-74e8-8f59-3a225e845986"
    )
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert (cards["69828"].make, cards["69828"].fuel) == ("bmw", "plugin_hybrid")  # "ba_el"
    assert (cards["18479"].model, cards["18479"].fuel) == ("A6 V", "diesel")  # mild hybrid
    assert (cards["76114"].make, cards["76114"].body) == ("mercedes-benz", "mpv")
    assert (cards["83008"].make, cards["83008"].fuel) == ("tesla", "electric")


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.json"), 2, places)
    assert not page.has_next and len(page.leads) == 26
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.kind == "leasing"
    assert url(2) == "https://drive-api.myayvens.cz/api/usedcars?pageNumber=2&pageSize=100"
    empty = SOURCE.parse_listing('{"totalItemCount": 0, "items": []}', 1, places)
    assert empty.none_listed and not empty.leads


def test_no_robots_txt_allows_all():
    """The API host answers /robots.txt with 404: every page may be read."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="{}")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.search_url(1))
