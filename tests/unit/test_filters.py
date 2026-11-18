from dataclasses import replace

import pytest
from starlette.datastructures import QueryParams

from adacar.core import ParsedCar
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.scraper.dedup import DedupIndex
from adacar.scraper.store import CarStore
from adacar.search.service import (
    Filters,
    facet_counts,
    filters_from,
    filters_to_query,
    search_cars,
)

# title, make, model, year, km, price, fuel, gearbox, power, seller, place, source
CARS = [
    ("Škoda Octavia 2.0 TDI", "skoda", "Octavia", 2018, 150_000, 289_000, "diesel", "manual",
     110, "dealer", "Kolín", "sauto"),
    ("Škoda Fabia 1.2", "skoda", "Fabia", 2011, 180_000, 99_000, "petrol", "manual", 51,
     "private", "Praha", "bazos"),
    ("VW Golf 1.5 TSI DSG", "volkswagen", "Golf", 2021, 40_000, 459_000, "petrol", "automatic",
     110, "dealer", "Brno", "sauto"),
    ("Toyota Yaris Hybrid", "toyota", "Yaris", 2020, 60_000, 349_000, "hybrid", "automatic",
     85, "private", "Plzeň", "bazos"),
    ("Škoda Superb 2.0 TDI", "skoda", "Superb", 2015, 240_000, None, "diesel", "automatic",
     140, "private", "Jihlava", "bazos"),
    ("Koupím Octavii", "skoda", None, None, None, None, None, None, None, None, "Kolín", "bazos"),
]  # fmt: skip


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from conftest import add_municipalities

    engine = make_engine(tmp_path_factory.mktemp("db") / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        add_municipalities(s)
        places = PlaceResolver(s)
        dedup = DedupIndex()
        for n, row in enumerate(CARS):
            title, make, model, year, km, price, fuel, gear, kw, seller, place, source = row
            car = ParsedCar(
                str(n), title, make=make, model=model, year=year, mileage_km=km, price=price,
                fuel=fuel, gearbox=gear, power_kw=kw, seller_kind=seller, place_text=place,
                location=places.resolve(place), images=[f"https://img.test/{n}.jpg"],
            )  # fmt: skip
            if title.startswith("Koupím"):
                car.extra["not_offer"] = "wanted"
            store = CarStore(s, source, dedup=dedup)
            store.add(car)
            store.commit()
        yield s
    engine.dispose()


def titles(session, f: Filters) -> list[str]:
    return [c.title for c in search_cars(session, f, limit=100).cars]


def test_ads_that_sell_no_car_are_never_listed(session):
    assert search_cars(session, Filters()).total == 5
    assert "Koupím Octavii" not in titles(session, Filters(q="octavii"))


def test_value_filters_match_any_chosen_value(session):
    assert set(titles(session, Filters(make=["toyota", "volkswagen"]))) == {
        "VW Golf 1.5 TSI DSG",
        "Toyota Yaris Hybrid",
    }
    assert set(titles(session, Filters(make=["skoda"], gearbox=["automatic"]))) == {
        "Škoda Superb 2.0 TDI"
    }
    assert titles(session, Filters(seller_kind=["dealer"], fuel=["petrol"])) == [
        "VW Golf 1.5 TSI DSG"
    ]
    assert titles(session, Filters(kraj=["Kraj Vysočina"])) == ["Škoda Superb 2.0 TDI"]
    assert search_cars(session, Filters(sources_off=["bazos"])).total == 2


def test_ranges(session):
    cheap = titles(session, Filters(price_max=300_000))
    assert set(cheap) == {"Škoda Octavia 2.0 TDI", "Škoda Fabia 1.2"}  # no price: not cheap
    assert set(titles(session, Filters(year_min=2018, year_max=2020))) == {
        "Škoda Octavia 2.0 TDI",
        "Toyota Yaris Hybrid",
    }
    assert set(titles(session, Filters(km_max=60_000))) == {
        "VW Golf 1.5 TSI DSG",
        "Toyota Yaris Hybrid",
    }
    assert titles(session, Filters(power_min=120)) == ["Škoda Superb 2.0 TDI"]
    assert search_cars(session, Filters(with_price=True)).total == 4


def test_sorting(session):
    assert titles(session, Filters(sort="price"))[:2] == [
        "Škoda Fabia 1.2",
        "Škoda Octavia 2.0 TDI",
    ]
    assert titles(session, Filters(sort="price"))[-1] == "Škoda Superb 2.0 TDI"  # no price last
    assert titles(session, Filters(sort="price_desc"))[0] == "VW Golf 1.5 TSI DSG"
    assert titles(session, Filters(sort="km"))[0] == "VW Golf 1.5 TSI DSG"
    assert titles(session, Filters(sort="year"))[0] == "VW Golf 1.5 TSI DSG"


def test_full_text_ignores_diacritics_and_finds_makes(session):
    assert set(titles(session, Filters(q="skoda tdi"))) == {
        "Škoda Octavia 2.0 TDI",
        "Škoda Superb 2.0 TDI",
    }
    assert titles(session, Filters(q="golf, yaris"))  # alternatives
    assert set(titles(session, Filters(q="volkswagen"))) == {"VW Golf 1.5 TSI DSG"}  # the make
    assert titles(session, Filters(q='"; drop table cars; --')) == []


def test_each_facet_ignores_its_own_choice(session):
    counts = facet_counts(session, Filters(make=["skoda"]))
    assert dict(counts["make"]) == {"skoda": 3, "volkswagen": 1, "toyota": 1}
    assert dict(counts["fuel"]) == {"diesel": 2, "petrol": 1}  # only the chosen make's
    assert dict(counts["sources"]) == {"sauto": 1, "bazos": 2}
    assert dict(counts["kraj"])["Středočeský kraj"] == 1


def test_filters_from_the_address_and_back():
    f = filters_from(
        QueryParams("make=skoda&make=bmw&price_max=300 000&year_min=2015&km_max=x&sort=km")
    )
    assert f.make == ["skoda", "bmw"] and f.price_max == 300_000 and f.year_min == 2015
    assert f.km_max is None and f.sort == "km"
    assert filters_from(QueryParams(filters_to_query(f))) == replace(f, sort="relevance")
    assert filters_from(QueryParams("sort=travel")).sort == "relevance"  # needs a start point


def test_the_photo_filter_is_on_unless_the_form_unticks_it():
    assert filters_from(QueryParams("")).with_photo  # a link without it
    off = filters_from(QueryParams("photo=0"))  # the form, box not ticked
    assert not off.with_photo and filters_from(QueryParams(filters_to_query(off))) == off
    assert filters_from(QueryParams("photo=0&photo=1")).with_photo  # ticked


def test_profile_defaults_apply_only_to_missing_fields():
    defaults = {"make": "skoda,toyota", "price_max": "400000"}
    assert filters_from(QueryParams(""), defaults).make == ["skoda", "toyota"]
    assert filters_from(QueryParams(""), defaults).price_max == 400_000
    # The form sends an empty value when nothing is chosen: then no default.
    cleared = filters_from(QueryParams("make=&price_max="), defaults)
    assert cleared.make == [] and cleared.price_max is None
    assert not cleared.is_filtered()
