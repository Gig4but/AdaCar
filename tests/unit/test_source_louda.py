from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import louda

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "louda"
SOURCE = louda.SOURCE


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


def test_sitemap_lists_used_cars(places):
    """The sitemap (trimmed to 402 of its ~4 000 cars) lists new cars too: only those with
    a used car's photo are taken."""
    page = SOURCE.parse_listing(read("sitemap-cars.xml"), 1, places)
    assert not page.has_next and len(page.leads) == 122
    lead = page.leads[0]
    assert lead.external_id == "59899" and lead.card is None
    assert lead.url == lead.fetch_url
    assert lead.url == "https://autolouda.cz/koupit/dacia-jogger-extreme-10-tce-67-kw-manual-59899"
    ids = {x.external_id for x in page.leads}
    assert "60370" in ids and "949" not in ids  # 949: a demonstrator, under cars-new
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://autolouda.cz/sitemap-cars.xml" and SOURCE.max_pages == 1


def test_detail(places):
    lead = Lead("59899", "https://autolouda.cz/koupit/x-59899")
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "59899"
    assert car.title == "DACIA JOGGER Extreme 1.0 TCe 67 kW manuál"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "dacia", "JOGGER", 2023, 99527, 309990,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive, car.condition) == (
        "lpg", "manual", "combi", None, "used",
    )  # fmt: skip
    assert (car.power_kw, car.color, car.doors, car.vat_deductible) == (67, "Hnědá", 5, None)
    assert car.first_owner is True and car.service_book is True and car.vin is None
    assert car.seller_kind == "dealer" and car.seller_name == "Auto Louda"
    assert car.location is None
    assert "Vyhřívaný volant" in car.equipment and len(car.equipment) == 52
    assert len(car.images) == 30
    assert car.images[0] == "https://content.louda.cz/cars-used/O419V08694_10.webp"


def test_detail_with_vat_and_4x4(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("60370", "u"), places)
    assert car is not None and car.title == "ŠKODA KODIAQ L&K 2.0 TDI 147 kW automat, DPH"
    assert (car.make, car.model, car.price, car.vat_deductible) == (
        "skoda", "Kodiaq", 919990, True,
    )  # fmt: skip
    assert (car.gearbox, car.body, car.drive, car.power_kw) == ("automatic", "suv", "4x4", 147)
    car = SOURCE.parse_detail(read("detail_demo.html"), Lead("949", "u"), places)
    assert car is not None and car.condition == "demo" and car.mileage_km == 11
    assert car.price == 489_900 + 20_000
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_sitemap_and_details():
    """It disallows only filter parameters (the file starts with a byte order mark)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=(FIXTURES / "robots.txt").read_bytes())
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed("https://autolouda.cz/sitemap-cars.xml")
    assert fetcher.allowed(
        "https://autolouda.cz/koupit/dacia-jogger-extreme-10-tce-67-kw-manual-59899"
    )
