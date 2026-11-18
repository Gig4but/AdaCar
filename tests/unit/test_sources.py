import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from adacar.core import ParsedCar, ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.normalize import cars
from adacar.normalize.vocab import make_code
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import NEEDS_CONSENT, SWITCHABLE, bazos, sauto

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"


def read(path: str) -> str:
    return (SOURCES / path).read_text(encoding="utf-8")


@pytest.fixture
def places(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        yield PlaceResolver(s)
    engine.dispose()


def test_sauto_is_opt_in_and_the_rest_on():
    on = {source_id for source_id, default in SWITCHABLE if default}
    assert len(SWITCHABLE) == 16 and on == {source_id for source_id, _ in SWITCHABLE} - {"sauto"}
    assert set(NEEDS_CONSENT) == {"sauto"}
    assert sauto.SOURCE.obey_robots is False and sauto.SOURCE.delay == 3.0


def test_sauto_listing_cards(places):
    page = sauto.SOURCE.parse_listing(read("sauto/listing_vysocina.json"), 1, places)
    assert page.has_next and len(page.leads) == 3
    lead = page.leads[0]
    car = lead.card
    assert lead.fetch_url == "https://www.sauto.cz/api/v1/items/211086942"
    assert lead.url == "https://www.sauto.cz/osobni/detail/skoda/fabia/211086942"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Fabia", 2017, 98000, 199900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.seller_kind) == ("diesel", "manual", "private")
    assert car.images[0].startswith("https://")
    dealer = page.leads[1].card
    assert dealer.seller_kind == "dealer" and dealer.seller_name
    assert dealer.gearbox == "automatic" and dealer.fuel == "petrol"


def test_sauto_detail(places):
    car = sauto.SOURCE.parse_detail(read("sauto/detail_2.json"), Lead("211382397", "u"), places)
    assert car is not None
    assert car.vin == "VF1JZNA0652766723"
    assert (car.body, car.drive, car.condition) == ("mpv", "front", "used")
    assert (car.power_kw, car.engine_cc, car.doors, car.seats) == (96, 1598, 5, 5)
    assert car.service_book is True and car.crashed is False and car.first_owner is False
    assert car.location is not None and car.location.municipality == "Praha"
    assert "Scénic" in car.description and len(car.equipment) > 10


def test_sauto_gone():
    assert sauto.SOURCE.gone(read("sauto/detail_1.json")) is False
    gone = json.dumps({"result": {"id": 1, "status": "deleted"}})
    assert sauto.SOURCE.gone(gone) is True
    assert sauto.SOURCE.parse_detail(gone, Lead("1", "u"), None) is None  # type: ignore[arg-type]


def test_sauto_reads_regions_in_price_bands():
    query = ScrapeQuery(kraje=["Kraj Vysočina", "Hlavní město Praha"])
    listings = sauto.SOURCE.listings(query)
    assert len(listings) == 2 * len(sauto.BANDS)
    assert {kraj for kraj, _ in listings} == {"Kraj Vysočina", "Hlavní město Praha"}
    url = listings[1][1](3)
    params = parse_qs(urlsplit(url).query)
    assert params["region_seo"] == ["kraj-vysocina"] and params["offset"] == ["200"]
    assert params["price_from"] == ["50000"] and params["price_to"] == ["99999"]
    assert params["sort"] == ["price"]
    last = parse_qs(urlsplit(listings[len(sauto.BANDS) - 1][1](1)).query)
    assert "price_to" not in last
    # The profile's makes and highest price narrow what is asked for.
    narrow = sauto.SOURCE.listings(
        ScrapeQuery(kraje=["Kraj Vysočina"], makes=["skoda", "volkswagen"], max_price=120_000)
    )
    assert len(narrow) == 3
    asked = parse_qs(urlsplit(narrow[0][1](1)).query)
    assert asked["manufacturer_model_seo"] == ["skoda|volkswagen"]


def test_sauto_pages_and_empty_bands():
    deep = json.dumps({"pagination": {"total": 50_000}, "results": []})
    # Past the API's depth the walk stops at max_pages, so the band is not read to the end.
    assert sauto.SOURCE.parse_listing(deep, 100, None).has_next  # type: ignore[arg-type]
    assert sauto.SOURCE.max_pages * sauto.PAGE_SIZE == sauto.MAX_OFFSET
    last = json.dumps({"pagination": {"total": 250}, "results": []})
    assert not sauto.SOURCE.parse_listing(last, 3, None).has_next  # type: ignore[arg-type]
    none = sauto.SOURCE.parse_listing(  # type: ignore[arg-type]
        json.dumps({"pagination": {"total": 0}, "results": []}), 1, None
    )
    assert none.none_listed and not none.has_next
    broken = sauto.SOURCE.parse_listing(json.dumps({"results": []}), 1, None)  # type: ignore[arg-type]
    assert not broken.none_listed  # no answer about the band: not "nothing here"
    assert sauto.SOURCE.check_url("https://www.sauto.cz/osobni/detail/skoda/fabia/211086942") == (
        "https://www.sauto.cz/api/v1/items/211086942"
    )


def test_bazos_listing_cards(places):
    page = bazos.SOURCE.parse_listing(read("bazos/listing_skoda.html"), 1, places)
    assert page.has_next and len(page.leads) == 40
    first = page.leads[0]
    assert first.external_id == "226557088"
    assert first.url.startswith("https://auto.bazos.cz/inzerat/226557088/")
    car = first.card
    assert car.make == "skoda" and car.model == "Octavia"
    assert (car.year, car.mileage_km, car.price, car.fuel) == (2020, 183000, 309900, "diesel")
    assert car.location is not None and car.location.kraj == "Zlínský kraj"
    assert car.posted_at is not None and car.images
    superb = page.leads[1].card
    assert (superb.model, superb.year, superb.mileage_km) == ("Superb", 2022, 95000)
    assert superb.gearbox == "automatic"


@pytest.mark.parametrize(("place", "wanted"), [("Zahraničí", False), ("Slovensko", False),
                                              ("Kolín", True)])  # fmt: skip
def test_bazos_skips_ads_from_abroad(place, wanted):
    card = ParsedCar("1", "Škoda Octavia", place_text=place)
    query = ScrapeQuery(kraje=[], home="Kolín", radius_km=50)
    assert bazos.SOURCE.wanted(Lead("1", "u", "u", card), query, None) is wanted  # type: ignore[arg-type]


def test_bazos_next_page_and_makes(places):
    page = bazos.SOURCE.parse_listing(read("bazos/listing_skoda_40.html"), 2, places)
    assert page.has_next and page.leads
    assert all(lead.card and lead.card.make == "skoda" for lead in page.leads)
    assert bazos.Bazos.category_url("skoda", 3) == "https://auto.bazos.cz/skoda/80/"
    every = bazos.SOURCE.listings(ScrapeQuery(kraje=[]))
    assert len(every) == len(bazos.CATEGORIES)
    chosen = bazos.SOURCE.listings(ScrapeQuery(kraje=[], makes=["skoda"]))
    assert [url(1) for _, url in chosen] == [
        "https://auto.bazos.cz/skoda/",
        "https://auto.bazos.cz/ostatni/",  # other makes: may hold the chosen one too
    ]


def test_bazos_detail(places):
    card = bazos.SOURCE.parse_listing(read("bazos/listing_skoda.html"), 1, places).leads[3]
    car = bazos.SOURCE.parse_detail(read("bazos/detail_1.html"), card, places)
    assert car is not None
    assert car.title == "Škoda Fabia 3 combi, 1.0 TSI 70 kW Ambition CZ DPH"
    assert (car.make, car.model, car.year, car.mileage_km) == ("skoda", "Fabia", 2022, 112931)
    assert (car.power_kw, car.fuel, car.gearbox, car.body) == (70, "petrol", "manual", "combi")
    assert car.price == 229800 and car.seller_name == "Auta BO"
    assert car.location is not None and car.location.municipality == "Pardubice"
    assert len(car.images) > 3


def test_bazos_marks_ads_that_sell_no_car():
    for title in (
        "Koupím Octavii 2",
        "Výkup aut za hotové",
        "Náhradní díly Fabia 1",
        "Motor BUSSO 2,5 V6 24V",
        'Alu kola 16" Octavia',
        "Zimní pneu 205/55",
        'Sada zimních kol 15"',
        "Prodám disky a pneu",
    ):
        car = bazos.ParsedCar("1", title)
        bazos.add_facts(car, "skoda")
        assert car.extra.get("not_offer"), title
    for title in ("Octavia 2 combi", "Motorově v pořádku, Fabia 1.2"):
        car = bazos.ParsedCar("1", title)
        bazos.add_facts(car, "skoda")
        assert "not_offer" not in car.extra, title


@pytest.mark.parametrize(
    ("title", "text", "year", "km"),
    [
        ("Octavia 2.0 TDI 110kW 2020", "", 2020, None),
        ("Superb 2.0 TDI DSG 2022, jen 95 tkm", "", 2022, 95000),
        ("Fabia 1.2 r.v. 2008 120tis km", "", 2008, 120000),
        ("Golf 2.0", "Rok výroby: 2015\nNajeto: 183 xxx km", 2015, 183000),
        ("Passat", "1. registrace 3/2016, stav tachometru 210 000 km", 2016, 210000),
        ("Golf 2000 km", "", None, 2000),
        ("Fabia", "Dojezd 400 km na nádrž", None, None),
        ("Superb", "Rozvody měněny při 150 000 km, najeto 190 000 km", None, 190000),
        ("Superb", "Nové pneu ujeto 5000 km", None, None),
    ],
)
def test_year_and_mileage_in_text(title, text, year, km):
    assert cars.year_in_text(title, text) == year
    assert cars.km_in_text(title, text) == km


def test_model_numbers_are_not_years():
    assert cars.year_in_text("Peugeot 2008 1.2 PureTech", make="peugeot") is None
    assert cars.year_in_text("Peugeot 2008 1.2 r.v. 2019", make="peugeot") == 2019
    assert cars.year_in_text("Octavia 2008 1.9 TDI", make="skoda") == 2008


def test_words_inside_other_words_say_nothing():
    text = "Dvouzónová automatická klimatizace, kombinovaná spotřeba 5 l, elektro-okna."
    assert cars.gearbox_in_text("Superb", text + " S manuální převodovkou.") == "manual"
    assert cars.body_in_text("Superb", text) is None
    assert cars.fuel_in_text("Superb", text + " Nafta.") == "diesel"
    assert cars.gearbox_in_text("Octavia 2.0 TDI automat") == "automatic"
    assert cars.gearbox_in_text("Golf", "Převodovka: automatická") == "automatic"
    assert cars.not_offer_reason("Hledám nového majitele pro Fabii") is None
    assert cars.not_offer_reason("Hledám Octavii") == "wanted"


def test_model_names_skip_makes_and_fillers():
    assert bazos.model_name("Alfa Romeo Giulia 2.2", "alfa-romeo") == "Giulia"
    assert bazos.model_name("Land Rover Discovery 3", "land-rover") == "Discovery"
    assert bazos.model_name("Prodám VW Golf", "volkswagen") == "Golf"
    assert bazos.model_name("octavia 2.0", "skoda") == "Octavia"


def test_car_words_in_text():
    assert cars.fuel_in_text("Octavia 1.5 TSI G-TEC") == "cng"
    assert cars.fuel_in_text("Superb 2.0 TDI") == "diesel"
    assert cars.fuel_in_text("Golf GTE plug-in hybrid") == "plugin_hybrid"
    assert cars.gearbox_in_text("2.0 TDI DSG") == "automatic"
    assert cars.body_in_text("Passat Variant") == "combi"
    assert cars.power_in_text("1.6 dCi 96 kW") == 96
    assert cars.parse_price("  309 900 Kč") == (309900, "CZK")
    assert cars.parse_price("12 500 €") == (12500, "EUR")
    assert cars.parse_price("Dohodou") is None and cars.parse_price("1 Kč") is None


def test_make_codes():
    assert make_code("Prodám VW Golf") == "volkswagen"
    assert make_code("Mercedes C 220") == "mercedes-benz"
    assert make_code("ŠKODA OCTAVIA") == "skoda"
    assert make_code("Land Rover Defender") == "land-rover"
    assert make_code("Octavia 2.0") is None
