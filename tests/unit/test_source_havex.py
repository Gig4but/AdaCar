from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import havex

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "havex"
SOURCE = havex.SOURCE


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
    assert page.has_next and len(page.leads) == 6
    lead = page.leads[1]
    assert lead.external_id == "528033"
    assert (
        lead.url
        == lead.fetch_url
        == ("https://prodej-vozu.cz/detail/528033-skoda-octavia-combi-1-6-tdi-ambition")
    )
    car = lead.card
    assert car is not None and car.title == "Škoda Octavia Combi 1.6 TDI Ambition"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia Combi", 2013, 162200, 168000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.condition) == (
        "diesel", "manual", "combi", "used",
    )  # fmt: skip
    assert car.seller_kind == "dealer" and car.seller_name == "HAVEX-auto"
    assert car.location is None
    assert car.images == ["https://prodej-vozu.cz/upload/car_sm/528033/20198918.jpg"]
    golf = page.leads[0].card
    assert golf is not None and (golf.make, golf.model, golf.body) == ("volkswagen", "Golf", None)


def test_last_page_and_listing_urls(places):
    """A page number past the end shows the last page (107), with no link further."""
    page = SOURCE.parse_listing(read("listing_last.html"), 107, places)
    assert not page.has_next and [x.external_id for x in page.leads] == [
        "528134", "528681", "524777",
    ]  # fmt: skip
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://prodej-vozu.cz/ojete-vozy"
    assert url(3) == "https://prodej-vozu.cz/ojete-vozy?page=3"


def test_detail(places):
    url = "https://prodej-vozu.cz/detail/528371-volkswagen-golf-1-6-tdi-trend"
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("528371", url), places)
    assert car is not None and car.title == "Volkswagen Golf 1.6 TDI Trend"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volkswagen", "Golf", 2012, 180052, 138000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.engine_cc, car.color) == (
        "diesel", "manual", 77, 1598, "černá",
    )  # fmt: skip
    assert car.vat_deductible is None and car.vin is None and car.location is None
    assert car.seller_name == "HAVEX-auto"
    assert "navigační systém" in car.equipment and len(car.equipment) == 46
    assert len(car.images) == 30
    assert car.images[0] == "https://prodej-vozu.cz/upload/car_lg/528371/20199255.jpg"


def test_detail_combi(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("528033", "u"), places)
    assert car is not None and (car.model, car.body, car.price, car.color) == (
        "Octavia Combi", "combi", 168000, "hnědá",
    )  # fmt: skip
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_listing_and_details():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_url_at(2))
    assert fetcher.allowed("https://prodej-vozu.cz/detail/528371-volkswagen-golf-1-6-tdi-trend")
    assert not fetcher.allowed("https://prodej-vozu.cz/import")
