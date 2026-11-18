from datetime import UTC, datetime

import pytest

from adacar.core import ParsedCar
from adacar.db.models import Car, CarAd, CarImage
from adacar.db.session import backup, make_engine, make_sessionmaker, migrate
from adacar.scraper.store import CarStore
from adacar.search.service import Filters, fts_query, search_cars, start_batch


@pytest.fixture
def session(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        yield s
    engine.dispose()


def test_backup_copies_the_database(tmp_path, session):
    add_car(session, "Škoda Octavia")
    copy = backup(session.get_bind())
    assert copy == tmp_path / "test.db.bak"
    with make_sessionmaker(make_engine(copy))() as s:
        assert [c.title for c in s.query(Car)] == ["Škoda Octavia"]


def test_no_backup_when_the_schema_is_current(tmp_path):
    engine = make_engine(tmp_path / "new.db")
    migrate(engine)  # a new database
    migrate(engine)  # already current
    engine.dispose()
    assert not (tmp_path / "new.db.bak").exists()


def add_car(
    session, title, description="", make=None, model=None, place="", status="active", ext=None
):
    now = datetime.now(UTC)
    car = Car(
        title=title,
        description=description,
        make=make,
        model=model,
        place_text=place,
        status=status,
        content_hash="x",
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
        posted_at=now,
    )
    car.images.append(CarImage(url=f"https://img.test/{title}.jpg", created_at=now))
    car.ads.append(
        CarAd(source_id="test", external_id=ext or title, first_seen_at=now, last_seen_at=now)
    )
    session.add(car)
    session.commit()
    return car


def test_fts_query_is_safe():
    assert fts_query("Octavia  Brno") == '"octavia"* AND "brno"*'
    assert fts_query('NOT "x" OR (') == '"not"* AND "x"* AND "or"*'
    assert fts_query("  -- ") is None
    assert fts_query("octavia, , škoda fabia") == '("octavia"*) OR ("škoda"* AND "fabia"*)'


def test_search_ignores_diacritics_and_matches_prefix(session):
    add_car(session, "Škoda Superb", place="Brno")
    add_car(session, "Renault Mégane Grandtour", place="Kolín")
    assert [c.title for c in search_cars(session, "skoda").cars] == ["Škoda Superb"]
    assert [c.title for c in search_cars(session, "megane kol").cars] == [
        "Renault Mégane Grandtour"
    ]
    assert search_cars(session, "megane brno").total == 0


def test_cars_without_a_photo_are_hidden_by_default(session):
    add_car(session, "Octavia")
    add_car(session, "Fabia").images.clear()
    session.commit()
    assert [c.title for c in search_cars(session, Filters()).cars] == ["Octavia"]
    assert search_cars(session, Filters(with_photo=False)).total == 2


def test_make_and_model_are_searched(session):
    add_car(session, "Kombi po servisu", make="volkswagen", model="Passat Variant")
    assert search_cars(session, "volkswagen passat").total == 1


def test_title_ranks_above_description(session):
    add_car(session, "Ford Focus", description="Lepší než octavia", ext="a")
    add_car(session, "Škoda Octavia", description="Servisní knížka", ext="b")
    assert search_cars(session, "octavia").cars[0].title == "Škoda Octavia"


def test_the_last_searchs_finds_come_first_then_by_rank(session):
    add_car(session, "Ford Focus", description="Lepší než octavia", ext="a")
    add_car(session, "Škoda Octavia", ext="b")
    start_batch(session)
    add_car(session, "Kia Ceed", description="Také octavia", ext="c")
    titles = [c.title for c in search_cars(session, "octavia").cars]
    assert titles == ["Kia Ceed", "Škoda Octavia", "Ford Focus"]


def test_phrases_separated_by_commas_are_alternatives(session):
    add_car(session, "Škoda Octavia", ext="a")
    add_car(session, "Škoda Fabia", ext="b")
    add_car(session, "Ford Fiesta", ext="c")
    add_car(session, "Kia Ceed", ext="d")
    titles = {c.title for c in search_cars(session, "octavia, ford fi").cars}
    assert titles == {"Škoda Octavia", "Ford Fiesta"}


def test_fts_follows_updates_and_deletes(session):
    car = add_car(session, "Škoda Fabia")
    car.title = "Škoda Roomster"
    session.commit()
    assert search_cars(session, "fabia").total == 0
    assert search_cars(session, "roomster").total == 1
    session.delete(car)
    session.commit()
    assert search_cars(session, "roomster").total == 0


def test_empty_query_lists_active_cars(session):
    add_car(session, "A")
    add_car(session, "B", status="expired")
    result = search_cars(session, "")
    assert result.total == 1 and result.cars[0].title == "A"


def test_equal_ranks_page_without_repeats(session):
    for i in range(12):
        add_car(session, "Škoda Octavia", ext=f"s{i}")
    pages = [search_cars(session, "octavia", limit=5, offset=o).cars for o in (0, 5, 10)]
    ids = [c.id for page in pages for c in page]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 12


def test_photos_without_a_host_are_not_stored(session):
    store = CarStore(session, "test")
    photos = ["/img/fotka-neni-k-dispozici.png", "https://img.test/1.jpg"]
    store.add(ParsedCar("1", "Octavia", images=photos))
    store.commit()
    assert [i.url for i in session.query(Car).one().images] == ["https://img.test/1.jpg"]
