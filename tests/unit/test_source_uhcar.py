from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import Lead, merge_card
from adacar.scraper.sources import uhcar

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "uhcar"
SOURCE = uhcar.SOURCE


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
    lead = page.leads[10]
    assert lead.external_id == "953420"
    assert (
        lead.url
        == lead.fetch_url
        == (
            "https://uhcar.cz/ojeta-auta/suzuki-sx4-s-cross-14-panorama-elegance-4x4-oranzova-rok-2017-id-953420/"
        )
    )
    car = lead.card
    assert car is not None and car.title == "Suzuki SX4 S-Cross 1,4 PANORAMA ELEGANCE, 4x4"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "suzuki", "SX4 S-Cross", 2017, 135623, 359000,  # "Akční cena", not "Původní cena"
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.engine_cc, car.power_kw) == (
        "petrol", "automatic", 1373, 103,
    )  # fmt: skip
    assert car.vat_deductible is None and car.condition == "used"
    assert car.seller_kind == "dealer" and car.seller_name == "UH CAR"
    assert car.images == ["https://uhcar.cz/wp-content/uploads/1o4702-460x345.jpg"]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    audi = cards["953492"]  # "Cena s DPH"
    assert (audi.price, audi.vat_deductible, audi.fuel) == (575960, True, "diesel")
    assert cards["969744"].fuel == "electric" and cards["969744"].engine_cc is None


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 11, places)
    assert not page.has_next and len(page.leads) == 7 and not page.none_listed
    empty = read("listing_last.html").replace('"total_rows":127', '"total_rows":0')
    assert SOURCE.parse_listing(empty, 11, places).none_listed
    assert not SOURCE.parse_listing("<html>chyba</html>", 1, places).none_listed
    cards = {x.external_id: x.card for x in page.leads if x.card}
    zafira = cards["730387"]  # "Ceníková cena" and "Akční cena s DPH" of a new car
    assert (zafira.price, zafira.vat_deductible, zafira.condition) == (1239000, True, "new")
    assert zafira.mileage_km is None
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide
    assert url(1) == "https://uhcar.cz/prodej-aut/autobazar/"
    assert url(3) == "https://uhcar.cz/prodej-aut/autobazar/?_paged=3"
    assert SOURCE.parse_listing("<html></html>", 1, places).leads == []


def test_detail(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[10]
    car = SOURCE.parse_detail(read("detail_1.html"), lead, places)
    assert car is not None and car.external_id == "953420"
    assert car.title == "Suzuki SX4 S-Cross 1,4 PANORAMA ELEGANCE, 4x4"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "suzuki", "SX4 S-Cross", 2017, 135623, 359000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("petrol", "automatic", "suv", 103)
    assert (car.color, car.seats, car.first_owner, car.service_book) == ("oranžová", 5, True, True)
    assert car.place_text == "Zlín"  # "Vůz se nachází v UH CAR-Zlín - tř. 3. května 1170..."
    assert "PROVĚŘENO AUTORIZOVANÝM SERVISEM" in car.description
    assert len(car.equipment) == 65 and "Apple CarPlay" in car.equipment
    assert (
        len(car.images) == 24 and car.images[0] == "https://uhcar.cz/wp-content/uploads/1o4702.jpg"
    )
    assert merge_card(car, lead.card).engine_cc == 1373  # told by the card


def test_detail_price_without_vat(places):
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("999203", "u"), places)
    assert car is not None and car.title == "Suzuki Jimny 1,5 VVT GLX ALLGRIP AT"
    assert (car.price, car.vat_deductible) == (908999, True)  # 751 239 Kč without VAT
    assert (car.year, car.mileage_km, car.body, car.seats) == (2026, 15, "suv", 4)
    assert car.place_text == "Staré Město" and car.first_owner is None
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_txt():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_url_at(2))
    assert fetcher.allowed(
        "https://uhcar.cz/ojeta-auta/suzuki-jimny-15-vvt-glx-allgrip-at-cerna-rok-2026-id-999203/"
    )


def test_instalment_given_as_price_is_no_price():
    text = "Akontace: 349 750Kč\nsplátka bez pojištění: 16.247Kč\nMožnost předčasného ukončení"
    assert uhcar.is_instalment(16247, text)
    assert not uhcar.is_instalment(1399000, text)
    assert not uhcar.is_instalment(16247, "Cena vozu 16 247 Kč")


def test_a_car_priced_at_its_instalment_has_no_price(places):
    lead = SOURCE.parse_listing(read("listing.html"), 1, places).leads[10]
    html = read("detail_1.html").replace(
        'leading-relaxed">', 'leading-relaxed">splátka 359 000 Kč ', 1
    )
    car = SOURCE.parse_detail(html, lead, places)
    assert car is not None and car.price is None
    assert lead.card is not None and lead.card.price is None
