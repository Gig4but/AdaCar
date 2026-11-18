from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import toyota

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "toyota"
SOURCE = toyota.SOURCE


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
    assert page.has_next and len(page.leads) == 20
    lead = page.leads[0]
    assert lead.external_id == "441909"
    assert (
        lead.url == lead.fetch_url == "https://proverenevozy.toyota.cz/nabidka/toyota-verso/441909"
    )
    car = lead.card
    assert car is not None and car.title == "Toyota Verso 1.8 108KW"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "toyota", "Verso", 2016, 178000, 179000,
    )  # fmt: skip
    assert (car.fuel, car.engine_cc, car.power_kw, car.vat_deductible) == (
        "petrol",
        1798,
        108,
        True,
    )
    assert car.seller_kind == "dealer" and car.seller_name == "AUTOSTYL, a.s."
    assert car.images and car.images[0].startswith("https://")
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["443281"].make == "lexus" and cards["443281"].fuel == "hybrid"
    assert cards["446126"].fuel == "plugin_hybrid"
    assert cards["446147"].fuel == "hybrid"  # "Benzín", but a "2.5Hybrid"
    assert cards["445924"].make == "peugeot" and cards["445924"].vat_deductible is None
    assert cards["446057"].model == "PROACE CITY VERSO"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 187, places)
    assert not page.has_next and len(page.leads) == 7
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.newest_first
    assert url(1) == "https://proverenevozy.toyota.cz/nabidky"
    assert url(3) == "https://proverenevozy.toyota.cz/nabidky?strona=3"


def test_detail(places):
    card = SOURCE.parse_listing(read("listing.html"), 1, places).leads[7]
    car = SOURCE.parse_detail(read("detail_1.html"), card, places)
    assert car is not None and car.external_id == "446147"
    assert car.title == "Toyota RAV4 2.5Hybrid,Executive,4X4,CZ"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "toyota", "RAV4", 2022, 31924, 879900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.engine_cc) == ("hybrid", "automatic", "suv", 2487)
    assert car.power_kw == 131  # 178 hp
    assert car.vin == "JTMD63FV10D077831" and (car.doors, car.seats) == (5, 5)
    assert car.vat_deductible is None and car.color is None
    assert car.seller_name == "Emil Frey ojeté vozy" and car.place_text == "Praha 9 - Kyje"
    assert car.location is not None and car.location.municipality == "Praha"
    assert "Prohlídku vozu" in car.description
    assert len(car.equipment) > 40 and len(car.images) == 45


def test_detail_with_vat(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("441909", "u"), places)
    assert car is not None
    assert (car.price, car.vat_deductible, car.gearbox, car.body) == (
        179000,
        True,
        "manual",
        "combi",
    )
    assert (car.power_kw, car.color, car.seller_name) == (108, "Šedá", "Toyota Trutnov")
    assert car.place_text == "Trutnov" and car.vin == "NMTDG26R70R085736"
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_no_robots_txt_allows_all():
    """The site answers /robots.txt with its 404 page: every page may be read."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="<html>Stránka nenalezena</html>")
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_page(2))
    assert fetcher.allowed("https://proverenevozy.toyota.cz/nabidka/toyota-rav4/446147")
