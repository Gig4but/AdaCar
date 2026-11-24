from datetime import datetime
from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autoaukce

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autoaukce"
SOURCE = autoaukce.SOURCE


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
    assert not page.has_next
    assert [x.external_id for x in page.leads] == ["350", "422", "410", "351"]  # promoted ones once
    lead = page.leads[1]
    assert lead.url == lead.fetch_url == "https://www.autoaukce.cz/detail/422-tesla-model-y"
    car = lead.card
    assert car is not None and car.title == "Tesla Model Y SR LFP DPH"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "tesla", "Model Y", 2023, 177292, 604000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw) == ("electric", "automatic", 250)
    assert car.description == "Aukce do 27. 11. 2026 12:15 (prodej podléhá schválení)"
    assert car.expires_at == datetime(2026, 11, 27, 11, 15)
    assert car.images and car.images[0].endswith("_thumb.jpg")
    bmw = page.leads[0].card
    assert bmw is not None and (bmw.make, bmw.model, bmw.year) == ("bmw", "Řada 3", 1989)
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and url(1) == "https://www.autoaukce.cz/" and SOURCE.kind == "auction"


def test_detail(places):
    card = SOURCE.parse_listing(read("listing.html"), 1, places).leads[1]
    car = SOURCE.parse_detail(read("detail_1.html"), card, places)
    assert car is not None and car.title == "Tesla Model Y SR LFP DPH"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "tesla", "Model Y", 2023, 177292, 604000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.drive, car.power_kw) == (
        "electric",
        "automatic",
        "rear",
        250,
    )
    assert car.vin == "LRWYGCFS4PC634832" and car.vat_deductible is True
    assert (car.seller_kind, car.seller_name) == ("dealer", "AutoAukce s.r.o.")
    assert car.description.startswith(
        "Aukce do 27. 11. 2026 12:15, Praha (prodej podléhá schválení)\nTesla Model Y"
    )
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.expires_at == datetime(2026, 11, 27, 11, 15)
    html = read("detail_1.html")
    badge = html.index(autoaukce.APPROVAL, html.index('id="snippet--bid"'))
    sure = html[:badge] + html[badge + len(autoaukce.APPROVAL) :]
    other = SOURCE.parse_detail(sure, card, places)
    assert other is not None and "schválení" not in other.description
    assert len(car.images) == 19 and not car.images[0].endswith("_thumb.jpg")


def test_detail_import(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("410", "u"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "ford", "Mustang Mach-E", 2021, 86216, 678000,
    )  # fmt: skip
    assert (car.drive, car.color, car.place_text) == ("4x4", "červená metalýza", "NL - Nizozemsko")
    assert car.seller_name == "Prověřené dovozy vozidel" and car.seller_kind is None
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_model_after_a_two_word_make():
    assert autoaukce._model("Alfa Romeo Giulia") == "Giulia"
    assert autoaukce._model("Mercedes-Benz GLC 300") == "GLC 300"
    assert autoaukce._model("Tesla Model Y") == "Model Y"


def test_empty_robots_txt_allows_all():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed("https://www.autoaukce.cz/detail/422-tesla-model-y")
