from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import emilfrey

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "emilfrey"
SOURCE = emilfrey.SOURCE


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
    assert page.has_next and len(page.leads) == 9
    lead = page.leads[5]
    assert lead.external_id == "1294664"
    assert (
        lead.url
        == lead.fetch_url
        == (
            "https://www.emilfreyselect.cz/emil-frey-select/detail-vozu/citroen/spacetourer/mpv/"
            "22bluehdi132kwshinel3at8/id262/1294664"
        )
    )
    car = lead.card
    assert car is not None and car.title == "Citroën SpaceTourer 2.2BlueHDi,132kW,SHINE,L3,AT8"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "citroen", "SpaceTourer", 2026, 21980, 999900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("diesel", "automatic", "mpv", 132)
    assert car.seller_kind == "dealer" and car.seller_name == "Emil Frey"
    assert car.place_text == "Praha 14"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.images == [
        "https://www.emilfreyselect.cz/files/gallery/cars/1294664/234_176/5625995.jpg"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["1254982"].gearbox == "manual" and cards["1254982"].fuel == "petrol"
    assert cards["1295905"].fuel == "electric" and cards["1295905"].model == "ë-C4"
    # A long version is cut short on the card.
    assert cards["1288544"].title == "Toyota bZ4X Touring, BEV, AT, Executive, 4"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 25, places)
    assert not page.has_next and len(page.leads) == 4
    card = page.leads[0].card
    assert card is not None and (card.make, card.body, card.place_text) == (
        "subaru",
        "combi",
        "Praha 5",
    )
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and not SOURCE.newest_first
    assert url(1) == "https://www.emilfreyselect.cz/ojete-vozy"
    assert url(3) == "https://www.emilfreyselect.cz/ojete-vozy?page=3"


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[5]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "1294664"
    assert car.title == "Citroën SpaceTourer 2.2BlueHDi,132kW,SHINE,L3,AT8"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "citroen", "SpaceTourer", 2026, 21980, 999900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.engine_cc, car.power_kw) == (
        "diesel", "automatic", "mpv", 2184, 132,
    )  # fmt: skip
    assert car.vin == "VF7VNFC19SZ090749" and (car.doors, car.seats) == (5, 8)
    assert car.color == "šedá metalíza" and car.vat_deductible is True
    assert car.service_book is True and car.origin_country == "Česká republika"
    assert car.seller_name == "Emil Frey, s.r.o. - Černý Most" and car.place_text == "Praha 14"
    assert car.location is not None and car.location.municipality == "Praha"
    assert car.location.address_text == "Skorkovská 1759, Praha"
    assert car.description.startswith("VOZIDLO STOJÍ NA CENTRÁLNÍM SKLADU")
    assert len(car.equipment) == 47 and "satelitní navigace" in car.equipment
    assert len(car.images) == 48
    assert all("/cars/1294664/1680_1024/" in i for i in car.images)


def test_detail_other_branch(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("1291676", "u"), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "kia", "Soul", 2009, 166151, 69900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("diesel", "manual", "suv", 94)
    assert car.vin == "KNAJG553297020232" and car.vat_deductible is None
    assert car.service_book is None and car.origin_country is None
    assert car.seller_name == "Emil Frey, s.r.o. - Stodůlky" and car.place_text == "Praha 5"
    assert car.location is not None and car.location.address_text == "Pekařská 638/5, Praha"
    assert len(car.equipment) == 13 and len(car.images) == 28
    assert car.description.startswith("Prohlídku vozu si prosím dohodněte")
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt_allows_all():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_url_at(2))
    assert fetcher.allowed(
        "https://www.emilfreyselect.cz/ojete-vozy/detail-vozu/kia/soul/suv/16crdi94kwmanualcz/id263/1291676"
    )
