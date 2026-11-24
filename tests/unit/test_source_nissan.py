from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import nissan

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "nissan"
SOURCE = nissan.SOURCE


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


def test_sitemap_is_the_listing(places):
    page = SOURCE.parse_listing(read("sitemap.xml"), 1, places)
    assert not page.has_next and len(page.leads) == 112
    lead = page.leads[0]
    assert lead.external_id == "17020" and lead.card is None
    assert (
        lead.url == lead.fetch_url == "https://www.nissan-exclusive.cz/vuz/17020-qashqai-n-connecta"
    )
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert (
        region is None
        and SOURCE.nationwide
        and url(1) == "https://www.nissan-exclusive.cz/sitemap.xml"
    )
    assert SOURCE.delay == 5.0 and SOURCE.kind == "dealer" and SOURCE.label == "Nissan ojeté vozy"
    assert SOURCE.parse_listing("<html>Chyba</html>", 1, places).leads == []


def test_detail_demonstrator(places):
    lead = Lead("18035", "https://www.nissan-exclusive.cz/vuz/18035-townstar")
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.title == "Nissan Townstar N-Connecta EV Van L1 / 2 sedadla"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "nissan", "Townstar", 2024, 5617, 920000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == ("electric", "automatic", "van", "front")
    assert (car.power_kw, car.vat_deductible, car.condition) == (90, True, "demo")
    assert car.vin is None and car.color is None
    assert car.seller_kind == "dealer" and car.seller_name == "Auto Trutnov"
    assert car.place_text == "Trutnov"
    assert car.description.startswith("elektromotor / 45 kWh / 90 kW")
    assert car.equipment == ["Základní bílý lak + Červený polep"]
    assert len(car.images) == 17
    assert car.images[0] == "https://www.nissan-jpn.cz/cloud/car/xlarge/342359.jpg"


def test_detail_with_location(places):
    lead = Lead("21258", "https://www.nissan-exclusive.cz/vuz/21258-micra-evolve-52kwh-two-tone")
    car = SOURCE.parse_detail(read("detail_2.html"), lead, places)
    assert car is not None and car.title == "Nissan Micra EVOLVE 52kWh + Two-tone"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "nissan", "Micra", 2026, 100, 759900,
    )  # fmt: skip
    assert (car.fuel, car.body, car.power_kw, car.condition) == (
        "electric", "hatchback", 110, "used",
    )  # fmt: skip
    assert car.seller_name == "Autoprodej Šrámek s.r.o." and car.place_text == "Plzeň"
    assert car.location is not None and car.location.municipality == "Plzeň"
    assert len(car.images) == 8 and car.equipment == []
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_sitemap_and_cars():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(nissan.SITEMAP)
    assert fetcher.allowed("https://www.nissan-exclusive.cz/vuz/18035-townstar")
    assert not fetcher.allowed("https://www.nissan-exclusive.cz/pdf/18035")


def test_a_sitemap_with_no_cars_says_so():
    empty = "<urlset><url><loc>https://www.nissan-exclusive.cz/</loc></url></urlset>"
    assert SOURCE.parse_listing(empty, 1, None).none_listed  # type: ignore[arg-type]
    assert not SOURCE.parse_listing("<html>chyba</html>", 1, None).none_listed  # type: ignore[arg-type]
