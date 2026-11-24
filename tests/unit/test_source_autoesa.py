from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autoesa

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autoesa"
SOURCE = autoesa.SOURCE


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
    assert page.has_next and len(page.leads) == 19
    lead = page.leads[0]
    assert lead.external_id == "370454289"
    assert (
        lead.url
        == lead.fetch_url
        == ("https://www.autoesa.cz/volkswagen/taigo/suv/benzin/370454289")
    )
    car = lead.card
    assert car is not None and car.title == "Volkswagen Taigo 1.0 TSi Style"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "volkswagen", "Taigo", 2022, 38692, None,
    )  # fmt: skip
    assert (car.fuel, car.body, car.power_kw, car.condition) == ("petrol", "suv", 81, "used")
    assert car.origin_country == "Česká republika" and car.service_book is True
    assert car.seller_kind == "dealer" and car.seller_name == "Auto ESA"
    assert car.location is None
    assert car.images == ["https://www.autoesa.cz/files/cars/370454289/950_713_e/370454289-1.jpg"]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["714465912"].make == "bmw" and cards["714465912"].model == "Řada 3"
    assert cards["305447398"].vat_deductible is True  # "-DPH"
    assert cards["370454289"].vat_deductible is None
    assert cards["170450220"].body == "van" and cards["948446055"].body is None  # minibus


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 298, places)
    assert not page.has_next and len(page.leads) == 14
    assert all(x.card and not x.card.images for x in page.leads)
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.autoesa.cz/vsechna-auta"
    assert url(3) == "https://www.autoesa.cz/vsechna-auta?stranka=3"


def test_detail(places):
    card = SOURCE.parse_listing(read("listing.html"), 1, places).leads[11]
    car = SOURCE.parse_detail(read("detail_1.html"), card, places)
    assert car is not None and card.external_id == "305447398"
    assert car.title == "Škoda Superb III 2.0 TDi Style"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Superb III", 2022, 190751, 445000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("diesel", "automatic", "combi", None)
    assert (car.power_kw, car.color, car.doors, car.seats) == (110, "šedá metalíza", 5, 5)
    assert car.vat_deductible is True and car.vin is None
    assert car.seller_name == "Auto ESA" and car.location is None
    assert "adaptivní tempomat" in car.equipment and len(car.equipment) == 42
    assert len(car.images) == 19 and car.images[0].endswith("/305447398-1.jpg")


def test_detail_4x4_minibus(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("948446055", "u"), places)
    assert car is not None and car.title == "Volkswagen Multivan 2.5TDI 4x4"
    assert (car.model, car.year, car.price, car.gearbox, car.body, car.drive) == (
        "Multivan", 2008, 270000, "manual", "other", "4x4",
    )  # fmt: skip
    assert (car.seats, car.service_book, car.vat_deductible) == (7, True, None)
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_listing_and_details():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_url_at(2))
    assert fetcher.allowed("https://www.autoesa.cz/skoda/superb/kombi/nafta/305447398")
    assert not fetcher.allowed("https://www.autoesa.cz/xml-feed")
