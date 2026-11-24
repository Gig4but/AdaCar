from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autorro

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "autorro"
SOURCE = autorro.SOURCE
AUDI = "https://www.autorro.cz/auto/audi-a4-allroad-2-0-tdi-quattro-130kw-2012-137e0c51-b94c-43d5-9bda-ca7ff16d7a1b/"


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
    lead = page.leads[8]
    assert lead.external_id == "137e0c51-b94c-43d5-9bda-ca7ff16d7a1b"
    assert lead.url == lead.fetch_url == AUDI
    car = lead.card
    assert car is not None and car.title == "Audi A4 Allroad 2.0 TDI quattro, 130kW (2012)"
    assert (car.make, car.year, car.mileage_km, car.price, car.vat_deductible) == (
        "audi", 2012, 224140, 275000, None,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive, car.power_kw) == (
        "diesel", "manual", "combi", "4x4", 130,
    )  # fmt: skip
    assert car.seller_kind == "dealer" and car.seller_name == "Autorro"
    assert car.place_text == "Jihomoravský kraj"
    assert car.location is not None and car.location.precision == "region"
    assert car.location.kraj == "Jihomoravský kraj"
    assert car.images == ["https://www.autorro.cz/wp-content/uploads/2026/11/1-10-1024x683.jpg"]
    cards = {x.external_id[:8]: x.card for x in page.leads if x.card}
    assert cards["f319d991"].vat_deductible is True and cards["f319d991"].price == 120000
    assert cards["f319d991"].body == "other"  # "Skříň", a box van
    assert cards["0821892f"].body == "cabrio" and cards["48b98b4a"].drive == "front"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 8, places)
    assert not page.has_next and len(page.leads) == 2
    listings = SOURCE.listings(ScrapeQuery(kraje=["Jihomoravský kraj", "Hlavní město Praha"]))
    assert [region for region, _ in listings] == ["Jihomoravský kraj", "Hlavní město Praha"]
    url = listings[0][1]
    assert url(1) == "https://www.autorro.cz/nabidka-vozidel/?locality=CZ-B"
    assert url(2) == "https://www.autorro.cz/nabidka-vozidel/?locality=CZ-B&pagenum=2"
    assert listings[1][1](1).endswith("?locality=CZ-A")
    assert not SOURCE.nationwide and len(autorro.REGION_CODES) == 14


def test_a_region_without_cars_says_so(places):
    page = SOURCE.parse_listing(read("listing_empty.html"), 1, places)
    assert not page.leads and not page.has_next and page.none_listed
    assert not SOURCE.parse_listing(read("listing_empty.html"), 2, places).none_listed
    assert not SOURCE.parse_listing(read("listing_last.html"), 8, places).none_listed
    assert not SOURCE.parse_listing("<html><body>Chyba</body></html>", 1, places).none_listed
    no_cards = read("listing_last.html").replace(' efect"', ' other"')
    assert not SOURCE.parse_listing(no_cards, 8, places).none_listed


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_1.html"), Lead("137e0c51", AUDI), places)
    assert car is not None and car.title == "Audi A4 Allroad 2.0 TDI quattro, 130kW (2012)"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "audi", "A4 Allroad", 2012, 224140, 275000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("diesel", "manual", "combi", "4x4")
    assert (car.power_kw, car.engine_cc, car.color, car.doors, car.seats) == (
        130, 1968, "Šedá", 5, 5,
    )  # fmt: skip
    assert car.vin == "WAUZZZ8KXDA013732" and car.condition == "used"
    assert car.seller_name == "Autorro" and car.place_text == "Jihomoravský kraj"
    assert car.location is not None and car.location.kraj == "Jihomoravský kraj"
    assert "Navigace" in car.equipment and len(car.equipment) == 53
    assert "Protiprokluzový systém kol (TC, ASR)" in car.equipment
    assert car.description.startswith("Audi A4 Allroad quattro 2.0 TDI")
    assert len(car.images) == 24


def test_detail_in_prague(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("683b390b", "u"), places)
    assert car is not None and car.title == "Bentley Continental GT 60 AT/6 2012 AWD 423kW"
    assert (car.make, car.model, car.price, car.body, car.power_kw, car.vin) == (
        "bentley", "Continental GT", 1350000, "coupe", 423, "SCBFE63W0CC077732",
    )  # fmt: skip
    assert car.place_text == "Hlavní město Praha"
    assert car.location is not None and car.location.municipality == "Praha"
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_listing_and_details():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_url("CZ-B", 2))
    assert fetcher.allowed(AUDI)
    assert not fetcher.allowed("https://www.autorro.cz/wp-admin/")
