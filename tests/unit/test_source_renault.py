from pathlib import Path

import httpx
import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher
from adacar.scraper.sources import renault

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "renault"
SOURCE = renault.SOURCE


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
    assert page.has_next and len(page.leads) == 48
    lead = page.leads[1]
    assert lead.external_id == "VF1RFE00369505732" and lead.fetch_url is None
    assert lead.url == (
        "https://renew.renault.cz/vyhledani-ojeteho-vozidla/"
        "renault-kadjar-suv-13-tce-103-kw-manual-intens-fwd/"
    )
    car = lead.card
    assert car is not None and car.title == "Renault Kadjar SUV 1,3 TCe 103 kW Manual Intens FWD"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "renault", "Kadjar", 2022, 65000, 369000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("petrol", "manual", "suv", 103)
    assert car.vin == "VF1RFE00369505732" and car.condition == "used"
    assert car.seller_kind == "dealer" and car.location is None
    assert car.description.startswith("ABS, 6x airbag")
    assert car.images == [
        "https://renew.renault.cz/used-cars-photos/_x265/8a3e9ebf3fa2a8d43e8497758fa5df5e1038024.jpg"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    arkana = cards["VF1RJL001UC394472"]
    assert arkana.fuel == "hybrid" and arkana.images == []  # a "no photo" placeholder
    assert arkana.description.startswith("Informace k vozidlu")
    assert cards["VYSP01H0375298064"].fuel == "electric"  # "E-Tech 52 kWh"
    assert cards["VYSP01H0375298064"].power_kw == 110  # "150k": horsepower
    assert cards["VF1RHN00975984693"].fuel == "hybrid"  # "fullhybrid"
    assert cards["VF1RFB00159084591"].fuel == "petrol"  # "1,6SCe"
    assert cards["VF1JL000371458789"].title.endswith("Manual Equilibre F")  # cut short
    assert cards["VF1HJD40365707184"].make == "dacia"


def test_last_page_and_listing_urls(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 22, places)
    assert not page.has_next and len(page.leads) == 22
    assert not SOURCE.parse_listing(read("listing.html"), 99, places).has_next
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide
    assert url(1) == "https://renew.renault.cz/?cars_per_page=48"
    assert url(2) == "https://renew.renault.cz/?cars_per_page=48&carlist_page=2"
    assert SOURCE.kind == "dealer" and SOURCE.label == "Renault a Dacia RENEW"


def test_robots_allow_listing_not_car_pages():
    """Cars' pages are disallowed: the source reads only the listing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=read("robots.txt"))
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.allowed(SOURCE.listing_page(1)) and fetcher.allowed(SOURCE.listing_page(5))
    assert not fetcher.allowed(
        "https://renew.renault.cz/vyhledani-ojeteho-vozidla/renault-captur-techno-tce-115/"
    )


def test_battery_names_are_electric():
    assert renault.fuel("techno 150 k comfort range") == "electric"
    assert renault.fuel("Techno 80 k urban range") == "electric"
    assert renault.fuel("1,3 TCe 140 Techno") == "petrol"
