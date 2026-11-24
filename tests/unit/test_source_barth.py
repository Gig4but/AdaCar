from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead, merge_card
from adacar.scraper.sources import barth

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "barth"
SOURCE = barth.SOURCE


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
    assert page.has_next and not page.none_listed and len(page.leads) == 12
    lead = page.leads[2]
    assert lead.external_id == "34802"
    assert (
        lead.url
        == lead.fetch_url
        == (
            "https://www.autocentrum.cz/nabidka-vozu/skoda-octavia-combi-style-7dsg-1-5tsi-110kw-156397/"
        )
    )
    car = lead.card
    assert car is not None and car.title == "Škoda Octavia Combi Style 7DSG 1,5TSI / 110kW"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia Combi", 2022, 57293, 499900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "petrol", "automatic", "combi", 110, 1498,
    )  # fmt: skip
    assert car.color == "stříbrná briliant metalíza" and car.vat_deductible is True
    assert car.seller_kind == "dealer" and car.seller_name == "Autocentrum BARTH"
    assert car.place_text == "Pardubice - Dubina"
    assert car.location is not None and car.location.municipality == "Pardubice"
    assert car.images and car.images[0].startswith("https://barth-net.cz/")
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["34783"].fuel == "diesel" and cards["34783"].gearbox == "manual"
    assert cards["34751"].body == "liftback"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 14, places)
    assert not page.has_next and len(page.leads) == 5 and not page.none_listed
    empty = read("listing_last.html").replace('"total":161', '"total":0')
    assert SOURCE.parse_listing(empty, 14, places).none_listed
    assert not SOURCE.parse_listing("<html>chyba</html>", 1, places).none_listed
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["32492"].make == "cupra" and cards["32492"].vat_deductible is None
    assert cards["32029"].make == "fiat" and cards["32029"].body == "other"  # a camper
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide
    assert url(1) == "https://www.autocentrum.cz/nabidka-vozu/ojete/"
    assert url(3) == "https://www.autocentrum.cz/nabidka-vozu/ojete/?page=3"
    assert SOURCE.parse_listing("<html></html>", 1, places).leads == []


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[2]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "34802"
    assert car.title == "Škoda Octavia Combi Style 7DSG 1,5TSI / 110kW"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia Combi", 2022, 57293, 499900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.engine_cc, car.power_kw) == (
        "petrol", "automatic", "combi", 1498, 110,
    )  # fmt: skip
    assert car.crashed is True and car.color == "stříbrná briliant metalíza"
    assert car.place_text == "Pardubice – Dubina"
    assert car.location is not None and car.location.address_text == "Hůrka 1798, Pardubice"
    assert len(car.equipment) == 83 and "Dvouzónová klimatizace" in car.equipment
    assert len(car.images) == 24 and car.images[0].endswith(".webp")
    assert merge_card(car, lead.card).vat_deductible is True  # told by the card


def test_detail_without_vat(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("32492", "u"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "cupra", "Ateca", 2019, 190246, 544800,
    )  # fmt: skip
    assert (car.power_kw, car.engine_cc, car.body, car.gearbox) == (221, 1984, "suv", "automatic")
    assert car.vat_deductible is None and len(car.images) == 30
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_url_at(2))
    assert fetcher.allowed(
        "https://www.autocentrum.cz/nabidka-vozu/seat-cupra-ateca-dsg-4wd-2-0-tsi-221kw-460728/"
    )
    assert not fetcher.allowed("https://www.autocentrum.cz/nabidka-vozu/ojete/?page=2&maxPrice=1")
