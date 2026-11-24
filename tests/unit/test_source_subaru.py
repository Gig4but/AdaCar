from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import subaru

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "subaru"
SOURCE = subaru.SOURCE


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
    assert page.has_next and len(page.leads) == 12
    lead = page.leads[1]
    assert lead.external_id == "15156"
    assert (
        lead.url
        == lead.fetch_url
        == ("https://www.subaru.cz/vozy-prodej/detail.php?detail=subaru-crosstrek-15156")
    )
    car = lead.card
    assert car is not None and car.title == "Subaru Crosstrek 2,0i CVT Comfort CZ"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "subaru", "Crosstrek", 2024, 72000, 599900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.vat_deductible) == ("hybrid", "automatic", True)
    assert car.seller_kind == "dealer" and car.condition == "used"
    assert car.images == [
        "https://www.subaru.cz/vozy-prodej/files/av/small/15156-13733-whatsapp-image-2026-05-26-at-09.47.44.jpeg"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["15151"].year is None  # "Rok výroby 0"
    assert cards["15151"].power_kw == 147  # from the title
    assert cards["15077"].gearbox == "manual" and cards["15064"].fuel == "electric"
    assert cards["15060"].gearbox == "automatic"  # "lineartronic"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 3, places)
    assert not page.has_next and len(page.leads) == 9
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide
    assert url(1) == "https://www.subaru.cz/vozy-prodej/ojete/"
    assert url(2) == "https://www.subaru.cz/vozy-prodej/ojete/?page=2"
    assert SOURCE.kind == "dealer" and SOURCE.label == "Subaru ojeté vozy"


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[11]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "15060"
    assert car.title == "Subaru Outback 2,5 CVT Touring CZ Harman Kardon"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "subaru", "Outback", 2022, 75300, 799000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body) == ("petrol", "automatic", "combi")
    assert (car.power_kw, car.engine_cc, car.color) == (124, 2498, "tmavě šedá metalíza")
    assert (car.first_owner, car.service_book, car.vat_deductible) == (False, True, None)
    assert car.origin_country == "Česká republika" and car.vin is None
    assert car.seller_name == "SUBARU LIŠKA PLZEŇ" and car.place_text == "Plzeň"
    assert car.location is not None and car.location.municipality == "Plzeň"
    assert car.description.startswith("Koupené a po celou dobu servisované jen u nás.")
    assert len(car.images) == 4 and "/files/av/large/15060-" in car.images[0]


def test_detail_electric(places):
    lead = Lead(
        "15064", "https://www.subaru.cz/vozy-prodej/detail.php?detail=subaru-solterra-15064"
    )
    car = SOURCE.parse_detail(read("detail_2.html"), lead, places)
    assert car is not None and (car.make, car.model, car.year) == ("subaru", "Solterra", 2023)
    assert (car.fuel, car.body, car.power_kw, car.engine_cc) == ("electric", "suv", 160, None)
    assert (car.doors, car.seats, car.first_owner) == (5, 5, True)
    assert car.seller_name == "IVOS Zlín, s.r.o." and car.place_text == "Fryšták"
    assert "vozidlo naší paní klientky" in car.description
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_gone_page():
    assert SOURCE.gone(read("gone.html"))
    assert not SOURCE.gone(read("detail_1.html"))


def test_robots_allow_listing_and_cars():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_page(2))
    assert fetcher.allowed("https://www.subaru.cz/vozy-prodej/detail.php?detail=subaru-brz-15151")


def test_zero_km_is_a_mileage():
    assert subaru._km("0 km") == 0 and subaru._km("12 500 km") == 12500 and subaru._km("") is None
