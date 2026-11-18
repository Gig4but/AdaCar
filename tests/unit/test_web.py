import re
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_hw
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from adacar.config import Settings
from adacar.core import ParsedCar, ParsedLocation
from adacar.db.models import Car, CarAd, CarImage, SavedSearch
from adacar.scraper.runner import Progress
from adacar.scraper.sources import SWITCHABLE
from adacar.scraper.store import CarStore
from adacar.search.service import start_batch
from adacar.secrets import MemorySecrets
from adacar.system.resources import ResourceManager
from adacar.web.app import create_app
from adacar.web.routes import maps, search

MODELS = (("skoda", "Octavia"), ("volkswagen", "Golf"), ("toyota", "Yaris"))


class FakeRunner:
    def __init__(self, sessions, cache_dir, **kwargs) -> None:
        self.sessions = sessions
        self.progress = Progress()
        self.starts = 0
        self.restarts = 0
        self.scope_changes = 0
        self.fresh = False
        self.started_at = None
        self.focus = None

    def start(self, fresh: bool = False) -> bool:
        if self.progress.running:
            return False
        self.starts += 1
        self.fresh = fresh
        self.progress = Progress(running=True, phase="listing")
        self.started_at = datetime(2026, 10, 13, 8, 0)
        return True

    def start_over(self) -> None:
        self.restarts += 1
        self.progress = Progress()
        self.start(fresh=True)

    def scope_changed(self) -> None:
        self.scope_changes += 1

    def last_start(self):
        return self.started_at


@pytest.fixture
def app(tmp_path):
    settings = Settings.load(tmp_path / "config.json")
    autostart = []
    app = create_app(
        settings,
        db_path=tmp_path / "t.db",
        cache_dir=tmp_path / "cache",
        runner_factory=FakeRunner,
        resources=ResourceManager(make_hw(16)),
        data_dir=tmp_path / "data",
        secrets=MemorySecrets(),
        autostart_sync=autostart.append,
        refreshes=(),
        routing_updates=False,
    )
    app.state.autostart = autostart  # what the Run value was set to, in order
    return app


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://127.0.0.1")


def fixture_cars(n: int = 30) -> list[ParsedCar]:
    """Car `i` (1-based, also its id once stored): Praha every 4th, else Kolín."""
    cars = []
    for i in range(1, n + 1):
        make, model = MODELS[i % 3]
        place = "Praha" if i % 4 == 0 else "Kolín"
        cars.append(
            ParsedCar(
                external_id=str(i), title=f"{model} {i:02d}",
                url=f"https://auto.bazos.cz/inzerat/{i}/auto.php",
                description=f"Prodám {model}, servisní knížka.", make=make, model=model,
                year=2010 + i % 10, mileage_km=10_000 * i, price=100_000 + 10_000 * i,
                fuel="diesel" if i % 2 else "petrol", gearbox="manual", seller_kind="private",
                place_text=place, images=[f"https://www.bazos.cz/img/1/{i}.jpg"],
            )
        )  # fmt: skip
    return cars


def store_cars(app, batch, source: str = "bazos") -> None:
    with app.state.services.sessions() as s:
        CarStore(s, source).add_all(batch)


def load_fixture_cars(app) -> None:
    store_cars(app, fixture_cars())


def new_batch(app) -> None:
    with app.state.services.sessions() as s:
        start_batch(s)


def order(page: str, ids) -> list[int]:
    return sorted(ids, key=lambda i: page.index(f'id="car-{i}"'))


def test_first_start_goes_through_wizard(app, client, tmp_path):
    assert client.get("/health").json()["status"] == "ok"
    first = client.get("/", follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/wizard/welcome"
    welcome = client.get("/wizard/welcome").text
    assert "Všechna auta na prodej ve vašem okolí" in welcome
    assert re.search(r'value="system"[^>]*checked', welcome)
    assert welcome.count('name="theme"') == 8
    assert 'href="/wizard/profile"' in welcome
    # In a form: htmx sends the picked language and scheme only from one.
    assert re.search(r'<form>\s*<div hx-post="/settings/appearance"', welcome)
    client.post("/settings/appearance", data={"language": "cs", "theme": "forest"})
    assert app.state.services.settings.theme == "forest"
    page = client.get("/wizard/profile").text
    assert "Odkud pojedete auta obhlédnout" in page
    assert 'data-theme="light" data-scheme="forest"' in page
    assert re.search(r'value="any"\s*checked', page)  # every way to travel by default
    assert 'name="makes"' not in page  # brands and price have their own step
    assert "nav-button" not in page  # only the wizard until it is done

    form = {"home_address": " Kolín ", "travel_mode": "transit", "max_minutes": "40"}
    r = client.post("/wizard/profile", data=form, follow_redirects=False)
    assert r.headers["location"] == "/wizard/cars"
    page = client.get("/wizard/cars").text
    assert "Modely a cena" in page and 'href="/wizard/profile"' in page
    assert 'name="makes" value="skoda"' in page and 'name="max_price"' in page
    form = {"makes": ["skoda", "toyota", "nonsense"], "max_price": "350 000"}
    r = client.post("/wizard/cars", data=form, follow_redirects=False)
    assert r.headers["location"] == "/wizard/sources"
    assert client.get("/wizard/ai", follow_redirects=False).status_code == 303  # no step
    page = client.get("/wizard/sources").text
    assert 'href="/wizard/cars"' in page
    assert 'name="source_bazos" checked' in page
    assert re.search(r'name="source_sauto"\s*>', page)  # off
    assert "robots.txt" in page  # why sauto.cz is off

    # sauto.cz without ticking the risk box stays off.
    r = client.post("/wizard/sources", data={"source_bazos": "on", "source_sauto": "on"})
    assert "zaškrtnete, že rozumíte riziku" in r.text
    assert app.state.services.settings.sources == {}
    assert 'name="source_sauto" checked' in r.text  # the choices made are kept
    every = {f"source_{source_id}": "on" for source_id, _ in SWITCHABLE}
    r = client.post("/wizard/sources", data=every | {"consent_sauto": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/wizard/automation"
    page = client.get("/wizard/automation").text
    assert 'href="/wizard/sources"' in page
    assert re.search(r'name="enabled"\s*>', page)  # off by default
    assert 'value="12" checked' in page
    form = {"interval_hours": "12", "start_with_windows": "on", "notifications": "on"}
    r = client.post("/wizard/automation", data=form | {"auto_update": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/wizard/map"
    assert app.state.services.settings.updates.auto
    page = client.get("/wizard/map").text
    assert "Stáhnout mapu" in page and "Přeskočit" in page  # nothing downloads unasked
    r = client.post("/wizard/map", data={"routing_area": "auto"}, follow_redirects=False)
    assert r.headers["location"] == "/wizard/done"
    assert not app.state.services.settings.routing_download
    r = client.post("/wizard/map", data={"routing_area": "home", "download": "1"},
                    follow_redirects=False)  # fmt: skip
    assert r.headers["location"] == "/wizard/map"  # stays to show the progress
    done = client.get("/wizard/done").text
    assert "Automatické hledání je vypnuté" in done and 'href="/wizard/map"' in done

    r = client.post("/wizard/done", follow_redirects=False)
    assert r.headers["location"] == "/"
    page = client.get("/").text
    assert 'href="/settings" class="nav-button icon-only"' in page
    assert app.state.services.runner.starts == 1  # Start runs the first search
    assert app.state.autostart == [False]  # no start with Windows while checks are off

    saved = Settings.load(tmp_path / "config.json")
    assert saved.wizard_completed
    p = saved.profile
    assert (p.home_address, p.travel_mode, p.max_minutes) == ("Kolín", "transit", 40)
    assert p.makes == ["skoda", "toyota"] and p.max_price == 350_000
    assert saved.sources == {source_id: True for source_id, _ in SWITCHABLE}
    assert saved.automation.enabled is False
    assert saved.routing_download and saved.routing_area == "home"


def test_sauto_needs_consent_ticked_by_the_user(app, client):
    app.state.services.settings.wizard_completed = True
    form = {"source_bazos": "on", "source_sauto": "on"}
    r = client.post("/sources", data=form, follow_redirects=False)
    assert r.status_code == 200 and 'name="source_sauto" checked' in r.text
    # The refused form comes back without the consent ticked, so saving it again fails too.
    assert 'name="consent_sauto" checked' not in r.text
    r = client.post("/sources", data=form | {"consent_sauto": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/sources?saved=1"
    assert app.state.services.settings.sources["sauto"] is True
    assert 'name="consent_sauto" checked' in client.get("/sources").text


def test_bad_profile_values_keep_the_old_ones(app, client):
    profile = app.state.services.settings.profile
    data = {"home_address": "Kolín", "max_minutes": "999", "depart_time": "x"}
    client.post("/wizard/profile", data=data)
    client.post("/wizard/cars", data={"max_price": "5"})
    assert (profile.home_address, profile.max_minutes) == ("Kolín", 0)
    assert profile.max_price is None and profile.depart_time == "09:00"
    r = client.post("/settings/appearance", data={"language": "cs", "theme": "neon"})
    assert r.status_code == 422


def test_scraped_links_are_web_addresses_only(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_cars(app)
    with app.state.services.sessions() as s:
        s.execute(update(CarAd).values(url="javascript:alert(1)"))
        s.execute(update(CarImage).values(url="javascript:alert(2)"))
        s.commit()
    for page in ("/results", "/cars/1"):
        text = client.get(page, headers={"HX-Request": "true"}).text
        assert "javascript:" not in text and 'href="#"' in text


def test_only_local_same_origin_requests(app, client):
    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 400
    evil = client.post("/scrape", headers={"Origin": "https://attacker.example"})
    assert evil.status_code == 403 and app.state.services.runner.starts == 0
    own = client.post("/scrape", headers={"Origin": "http://127.0.0.1"})
    assert own.status_code == 200 and app.state.services.runner.starts == 1


def test_english_ui(app, client):
    client.post("/settings/appearance", data={"language": "en"})
    assert "Where you will travel from to see cars" in client.get("/wizard/profile").text


def test_language_and_scheme_are_saved_when_picked(app, client):
    assert 'hx-post="/settings/appearance"' in client.get("/wizard/welcome").text
    r = client.post("/settings/appearance", data={"language": "en", "theme": "ocean"})
    assert r.headers["HX-Refresh"] == "true"  # the page comes back in the new language
    saved = Settings.load(app.state.services.settings._path)
    assert (saved.language, saved.theme) == ("en", "ocean")
    r = client.post("/settings/appearance", data={"language": "en", "theme": "plum"})
    assert "HX-Refresh" not in r.headers and app.state.services.settings.theme == "plum"
    assert client.post("/settings/appearance", data={"language": "de"}).status_code == 422


def test_last_update_is_shown_in_local_time(app, client):
    from adacar.db.models import Run

    finished = datetime(2026, 10, 9, 23, 30)  # stored as naive UTC
    with app.state.services.sessions() as s:
        s.add(Run(source_id="bazos", started_at=finished, finished_at=finished, status="ok"))
        s.commit()
    local = finished.replace(tzinfo=UTC).astimezone()
    when = f"{local.day}. {local.month}. {local.year} {local:%H:%M}"
    assert f"poslední aktualizace {when}" in client.get("/status").text


def test_search_page_and_results(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_cars(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Auta v prodeji: 30" in page.text
    assert "automatické hledání vypnuto" in page.text
    assert "Octavia 03" in page.text

    partial = client.get("/results", params={"q": "octavia"}).text
    assert "Octavia 03" in partial and "Golf 01" not in partial
    assert "130\xa0000\xa0Kč" in partial and "30\xa0000\xa0km" in partial
    assert "2013" in partial and "Nafta" in partial and "Soukromý prodejce" in partial
    assert 'src="https://www.bazos.cz/img/1/3.jpg"' in partial
    assert "auto.bazos.cz/inzerat/3/auto.php" in partial
    assert "<html" not in partial  # fragment only

    with app.state.services.sessions() as s:
        s.execute(update(Car).where(Car.id == 3).values(price=None))
        s.commit()
    assert "Cena dohodou" in client.get("/results", params={"q": "octavia 03"}).text


def test_list_is_in_found_order_the_last_search_first(app, client):
    app.state.services.settings.wizard_completed = True
    cars = fixture_cars()
    store_cars(app, cars[:10])
    new_batch(app)
    store_cars(app, cars[10:20])
    page = client.get("/").text
    assert 'id="car-list"' in page and page.count('class="car"') == 20
    assert order(page, range(1, 21)) == [*range(11, 21), *range(1, 11)]
    assert page.count('class="new-mark"') == 10  # the last search's finds
    assert page.index('class="new-mark"') < page.index('id="car-1"')
    with app.state.services.sessions() as s:
        start_batch(s, 0)  # started over: everything was found again, nothing is new
    assert 'class="new-mark"' not in client.get("/").text


def test_cars_can_be_hidden_and_brought_back(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_cars(app)
    page = client.get("/").text
    assert page.count('class="hide secondary outline"') == 30 and "Skryto" not in page
    for car_id in (29, 30):
        r = client.post(f"/cars/{car_id}/hide", data={"sort": "relevance"})
    assert 'id="hidden-bar"' in r.text and 'hx-swap-oob="true"' in r.text
    assert "Skryto: 2" in r.text and "Nalezená auta: 28" in r.text
    page = client.get("/results").text
    assert 'id="car-30"' not in page and 'id="car-29"' not in page and "Skryto: 2" in page

    r = client.post("/hidden/restore")  # the last hidden comes back first
    assert r.headers["HX-Trigger"] == "cars-updated" and "Skryto: 1" in r.text
    page = client.get("/results").text
    assert 'id="car-30"' in page and 'id="car-29"' not in page
    r = client.post("/hidden/restore?every=1")
    assert "Skryto" not in r.text and 'id="car-29"' in client.get("/results").text
    assert client.post("/cars/999999/hide").status_code == 404


def test_a_running_search_adds_its_finds_in_found_order(app, client, monkeypatch):
    monkeypatch.setattr(search, "PAGE_SIZE", 10)
    app.state.services.settings.wizard_completed = True
    cars = fixture_cars()

    def poll(since, shown, more=False, qs=""):
        data = {"shown": ",".join(map(str, shown)), "more": "1" if more else ""}
        return client.post(f"/results/new?{qs}since={since}&cleared=0", data=data)

    assert "/results/new" not in client.get("/").text  # no search running
    new_batch(app)  # the first search: the list is empty
    app.state.services.runner.progress = Progress(running=True, phase="listing")
    assert "since=0&cleared=0" in client.get("/").text
    r = poll(0, [])
    assert "since=0" in r.text and "hx-swap-oob" not in r.text  # nothing new yet

    store_cars(app, cars[:5])  # put at the end, in the order found
    r = poll(0, [])
    assert 'hx-swap-oob="beforeend:#car-list"' in r.text
    assert order(r.text, range(1, 6)) == [1, 2, 3, 4, 5] and "since=5" in r.text
    assert "Nalezená auta: 5" in r.text
    store_cars(app, cars[5:15])  # fills the page; the rest is behind "more"
    r = poll(5, range(1, 6))
    assert r.text.count('class="car arrived"') == 5
    assert order(r.text, range(6, 11)) == [6, 7, 8, 9, 10]
    assert 'class="secondary outline more"' in r.text and "since=15" in r.text

    with app.state.services.sessions() as s:  # no longer offered: drops out of the list
        s.execute(update(Car).where(Car.id == 2).values(status="expired"))
        s.commit()
    store_cars(app, cars[15:16])  # belongs on a page not loaded yet
    r = poll(15, range(1, 11), more=True)
    assert '<div id="car-2" hx-swap-oob="delete">' in r.text
    assert 'class="car"' not in r.text and "since=16" in r.text

    new_batch(app)  # the next search: its finds go above the cars found before
    store_cars(app, cars[16:19])
    shown = [1, *range(3, 11)]
    r = poll(16, shown, more=True)
    assert 'hx-swap-oob="beforebegin:#car-1"' in r.text
    assert order(r.text, range(17, 20)) == [17, 18, 19] and "since=19" in r.text
    page = client.get("/").text
    assert order(page, [3, 1, 19, 18, 17]) == [17, 18, 19, 1, 3]

    store_cars(app, cars[19:])  # with words to rank by too, above the cars found before
    r = poll(19, shown, qs="q=praha&")
    assert "HX-Trigger" not in r.headers and 'hx-swap-oob="beforebegin:#car-4"' in r.text
    assert 'id="car-24"' in r.text and 'id="car-21"' not in r.text  # Praha only
    assert '<div id="car-3" hx-swap-oob="delete">' in r.text  # Kolín
    r = poll(19, shown, qs="sort=date&")  # another order: new finds belong anywhere, reload
    assert r.headers["HX-Trigger"] == "cars-updated" and r.text == ""

    app.state.services.runner.progress.cleared = True  # old cars deleted: ids start again
    assert poll(30, shown).headers["HX-Trigger"] == "cars-updated"
    app.state.services.runner.progress = Progress(phase="done")
    r = client.post("/results/new?since=30&cleared=1")
    assert r.text == ""  # the poll stops


def test_starting_over_empties_the_list_at_once(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_cars(app)
    for progress in (
        Progress(running=True, phase="listing", restarting=True),  # the running one stops
        Progress(running=True, phase="clear"),
    ):
        app.state.services.runner.progress = progress
        r = client.get("/results", headers={"HX-Request": "true"})
        assert 'class="car' not in r.text and "since=0&cleared=0" in r.text


def test_a_refresh_dims_the_listed_cars_until_checked(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_cars(app)
    checked_since = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1)
    app.state.services.runner.progress = Progress(
        running=True, phase="check", checked_since=checked_since, checking=("bazos",)
    )
    with app.state.services.sessions() as s:  # one was checked already
        s.execute(update(CarAd).where(CarAd.car_id == 2).values(last_seen_at=checked_since))
        s.commit()
    page = client.get("/").text
    assert 'class="car unchecked" id="car-1"' in page and 'class="car" id="car-2"' in page
    r = client.post("/results/new?since=30&cleared=0", data={"shown": "1,2,3"})
    assert 'data-unchecked="1,3"' in r.text
    app.state.services.runner.progress.checking = ()
    assert 'class="car unchecked"' not in client.get("/").text


def test_a_car_opens_with_its_specs_and_whole_text(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_cars(app)
    with app.state.services.sessions() as s:
        text = "Úvod.\n" + "slovo " * 100
        s.execute(
            update(Car)
            .where(Car.id == 1)
            .values(description=text, vin="TMBJJ7NE5J0123456", equipment=["ABS", "Klimatizace"])
        )
        s.commit()
    page = client.get("/").text
    assert 'href="/cars/1" class="car-open"' in page
    assert 'id="car-detail"' in page and 'id="to-top"' in page
    r = client.get("/cars/1", headers={"HX-Request": "true"})
    assert "<html" not in r.text and "Golf 01" in r.text
    assert text.strip() in r.text and "auto.bazos.cz" in r.text
    assert "TMBJJ7NE5J0123456" in r.text and "Výbava (2)" in r.text and "Klimatizace" in r.text
    assert "Rok výroby" in r.text and "10\xa0000 km" in r.text
    assert "<html" in client.get("/cars/1").text  # opened as a page of its own
    boosted = {"HX-Request": "true", "HX-Boosted": "true"}  # from the page beside the search
    assert "<html" in client.get("/cars/1", headers=boosted).text
    assert client.get("/cars/999999").status_code == 404


def test_a_car_on_two_portals_shows_both_ads_and_prices(app, client):
    app.state.services.settings.wizard_completed = True
    store_cars(app, fixture_cars(1))
    with app.state.services.sessions() as s:
        car = s.get(Car, 1)
        car.ads.append(
            CarAd(source_id="sauto", external_id="9", url="https://www.sauto.cz/x/9",
                  is_primary=False, price=105_000, first_seen_at=car.first_seen_at,
                  last_seen_at=car.first_seen_at)
        )  # fmt: skip
        s.commit()
    r = client.get("/cars/1", headers={"HX-Request": "true"}).text
    assert "Bazoš – auta" in r and "Sauto.cz" in r and "Jinde za 105\xa0000 Kč" in r


def test_scrape_button_starts_and_polls(app, client):
    app.state.services.settings.wizard_completed = True
    runner = app.state.services.runner
    r = client.post("/scrape")
    assert runner.starts == 1 and not runner.fresh
    assert 'hx-trigger="every 1s"' in r.text
    assert r.headers["HX-Trigger"] == "cars-updated"  # the list starts asking for new finds
    page = client.get("/").text  # both buttons, also while a search runs
    assert 'hx-post="/scrape"' in page and 'hx-post="/scrape?fresh=1"' in page
    client.post("/scrape")
    assert runner.starts == 1  # not again while one runs
    client.post("/scrape?fresh=1")  # starts over: the running one stops for it
    assert runner.restarts == 1 and runner.starts == 2 and runner.fresh

    app.state.services.runner.progress = Progress(
        running=False, phase="done", results={"bazos": {"new": 5, "updated": 1, "removed": 2}}
    )
    r = client.get("/scrape/progress")
    assert r.headers["HX-Trigger"] == "cars-updated"
    assert "5 nových, 1 změněných, 2 již neprodávaných" in r.text


def test_filters_from_url_and_facets_panel(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_cars(app)
    params = [("make", "skoda"), ("price_max", "300000"), ("sort", "price"), ("bogus", "x")]
    r = client.get("/results", params=params, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.headers["HX-Push-Url"].startswith("/?make=skoda&price_max=300000")
    assert 'hx-get="/facets?make=skoda' in r.text  # filter counts refresh after results
    assert order(r.text, [3, 6, 9]) == [3, 6, 9]  # cheapest first

    page = client.get("/", params=params)
    assert '<option value="price" selected>' in page.text

    panel = client.get("/facets", params=params).text
    assert 'name="make" value="skoda" checked' in panel
    assert 'name="price_max" value="300000"' in panel
    assert 'name="source_off" value="bazos" disabled' in panel  # every source on
    assert 'value="sauto"' not in panel  # off unless the user turns it on
    assert panel.index("Cena (Kč)") < panel.index("Značka") < panel.index("Kraj")
    assert panel.index("Kraj") < panel.index("Zdroje")
    assert "Volkswagen" in panel and "Nafta" in panel and "Manuální" in panel
    off = client.get("/facets", params=[*params, ("source_off", "bazos")]).text
    assert 'name="source_off" value="bazos" >' in off
    r = client.get("/results", params={"source_off": "bazos"}, headers={"HX-Request": "true"})
    assert r.headers["HX-Push-Url"] == "/?source_off=bazos" and 'class="car"' not in r.text


def test_the_profile_presets_makes_and_price(app, client):
    settings = app.state.services.settings
    settings.wizard_completed = True
    settings.profile.makes, settings.profile.max_price = ["toyota"], 200_000
    load_fixture_cars(app)
    page = client.get("/").text
    assert "Yaris 02" in page and "Octavia 03" not in page and "Yaris 11" not in page
    panel = client.get("/facets").text
    assert 'name="make" value="toyota" checked' in panel and 'value="200000"' in panel
    assert 'name="photo" value="1" checked' in panel  # only cars with a photo, by default
    everything = client.get("/results", params={"make": "", "price_max": ""}).text
    assert "Octavia 03" in everything and "Yaris 11" in everything
    assert 'href="/?make=&price_max="' in panel  # "Zrušit filtry" clears the preset too


def test_malformed_filters_are_ignored(app, client):
    app.state.services.settings.wizard_completed = True
    r = client.get("/", params={"price_max": "abc", "sort": "drop table", "posted_days": "-1"})
    assert r.status_code == 200
    for value in ("²", "9" * 30, "1" * 5000):
        r = client.get("/results", params={"price_min": value, "year_min": value})
        assert r.status_code == 200


def test_empty_database_is_not_blamed_on_filters(client):
    r = client.get("/results?q=&sort=date", headers={"HX-Request": "true"})
    assert "Zatím tu nejsou žádná auta" in r.text
    r = client.get("/results?q=xyz", headers={"HX-Request": "true"})
    assert "Těmto filtrům neodpovídá" in r.text


def test_map_points_follow_filters(app, client, monkeypatch, municipalities):
    app.state.services.settings.wizard_completed = True
    commute_cars(app, municipalities, many=True)

    everything = client.get("/map/points").json()
    assert everything["type"] == "FeatureCollection"
    total = sum(f["properties"]["count"] for f in everything["features"])
    assert total == 9
    lon, lat = everything["features"][0]["geometry"]["coordinates"]
    assert 14 <= lon < 16 and 50 <= lat < 51

    some = client.get("/map/points", params={"q": "praha"}).json()
    assert sum(f["properties"]["count"] for f in some["features"]) == 3

    loc_id = some["features"][0]["properties"]["id"]
    popup = client.get(f"/map/location/{loc_id}", params={"q": "praha"}).text
    assert "Octavia Praha" in popup and "250\xa0000\xa0Kč" in popup and "<html" not in popup
    monkeypatch.setattr(maps, "POPUP_SIZE", 2)
    popup = client.get(f"/map/location/{loc_id}").text
    assert popup.count("<li>") == 2 and "další auta: 1" in popup

    page = client.get("/", params={"view": "map"}).text
    assert 'document.body.dataset.view = "map"' in page and 'id="map"' in page


def test_updates_page_checks_installs_and_switches_auto(app, client, tmp_path, monkeypatch):
    from adacar.desktop.update import UpdateStatus

    svc = app.state.services
    svc.settings.wizard_completed = True
    page = client.get("/settings/updates").text
    assert "Nainstalovaná verze: " in page and 'hx-post="/updates/check"' in page
    asked = []
    monkeypatch.setattr(svc.updates, "check_now", lambda: asked.append("check"))
    monkeypatch.setattr(svc.updates, "install_now", lambda: asked.append("install"))
    client.post("/updates/check")
    svc.updates.status = UpdateStatus("available", "9.9.9")
    page = client.get("/updates/status").text
    assert "9.9.9" in page and "jen nainstalovaná" in page  # a developer's copy: no install
    monkeypatch.setattr(svc.updates, "can_install", lambda: True)
    assert 'hx-post="/updates/install"' in client.get("/updates/status").text
    client.post("/updates/install")
    assert asked == ["check", "install"]
    (tmp_path / "data" / "updates").mkdir(parents=True)
    (tmp_path / "data" / "updates" / "pending.json").write_text("{}")
    client.post("/settings/updates", data={"auto_update": "on"})
    assert svc.settings.updates.auto
    client.post("/settings/updates", data={})  # off: a downloaded installer is not run
    assert not svc.settings.updates.auto and not any((tmp_path / "data" / "updates").iterdir())


def test_automatic_checks_are_switched_in_settings_without_a_restart(app, client):
    app.state.services.settings.wizard_completed = True
    page = client.get("/settings/automation").text
    assert "Automaticky hledat nová auta" in page and 'value="12" checked' in page
    assert "automatické hledání vypnuto" in client.get("/status").text

    form = {"enabled": "on", "interval_hours": "6", "start_with_windows": "on"}
    r = client.post("/settings/automation", data=form, follow_redirects=False)
    assert r.headers["location"] == "/settings/automation?saved=1"
    auto = app.state.services.settings.automation
    assert auto.enabled and auto.interval_hours == 6
    assert app.state.autostart == [True]  # starts with Windows while the checks are on
    # Never searched: due now (the scheduler thread starts it).
    assert app.state.services.scheduler.next_check() is not None
    app.state.services.runner.start()
    started = app.state.services.runner.started_at
    assert app.state.services.scheduler.next_check() == started + timedelta(hours=6)

    client.post("/settings/pause")
    assert "pozastaveno do" in client.get("/status").text
    assert "Pokračovat" in client.get("/settings/automation").text
    client.post("/settings/resume")
    assert "další hledání" in client.get("/status").text

    form = {"interval_hours": "24"}  # switched off: no start with Windows
    client.post("/settings/automation", data=form)
    assert not auto.enabled and app.state.autostart[-1] is False
    assert app.state.services.scheduler.next_check() is None


def test_settings_end_with_the_license_texts(client, monkeypatch):
    from adacar import licenses

    files = (
        licenses.LicenseFile("notices", "THIRD-PARTY-NOTICES.txt", text="all of them"),
        licenses.LicenseFile("python", "demo-1.0 / LICENSE", text="<b>MIT</b> & co"),
    )
    monkeypatch.setattr(licenses, "files", lambda: files)
    settings = client.get("/settings").text
    assert settings.index('href="/licenses"') > settings.index("Záznamy (logy)")  # at the end
    page = client.get("/licenses").text
    assert "Přehled" in page and "Balíčky Pythonu" in page and "demo-1.0 / LICENSE" in page
    assert 'hx-get="/licenses/1"' in page
    assert client.get("/licenses/1").text == "<pre>&lt;b&gt;MIT&lt;/b&gt; &amp; co</pre>"
    assert client.get("/licenses/2").status_code == 404
    assert client.get("/licenses/-1").status_code == 422


def test_settings_list_the_wizard_steps_in_order(app, client):
    app.state.services.settings.wizard_completed = True
    page = client.get("/settings").text
    assert 'href="/wizard/welcome"' in page and "Spustit průvodce znovu" in page
    catalog = page.split('class="catalog"')[1].split("</ul>")[0]
    pages = re.findall(r'<li><a href="([^"]+)"', catalog)
    assert pages == [
        "/settings/appearance",
        "/settings/profile",
        "/settings/cars",
        "/sources",
        "/settings/automation",
        "/settings/map",
        "/settings/updates",
    ]
    assert "Čeština · Podle Windows" in page and "domov nezadán" in page
    assert "všechny značky · jakákoli cena" in page
    for href in pages:
        assert 'href="/settings" class="back' in client.get(href).text
    assert client.get("/settings/nothing", follow_redirects=False).status_code == 303
    for gone in ("/ai", "/settings/keys", "/settings/employers", "/add"):
        assert client.get(gone, follow_redirects=False).status_code in (303, 404), gone

    profile = app.state.services.settings.profile
    form = {"home_address": "Kolín", "travel_mode": "car", "max_minutes": "30"}
    r = client.post("/settings/profile", data=form, follow_redirects=False)
    assert r.headers["location"] == "/settings/profile?saved=1"
    assert (profile.home_address, profile.travel_mode, profile.max_minutes) == ("Kolín", "car", 30)
    r = client.post("/settings/cars", data={"makes": ["skoda"], "max_price": "250000"},
                    follow_redirects=False)  # fmt: skip
    assert r.headers["location"] == "/settings/cars?saved=1"
    assert profile.makes == ["skoda"] and profile.max_price == 250_000
    page = client.get("/settings").text
    assert "Kolín · Auto · nejvýš 30 min" in page and "Škoda · do 250\xa0000 Kč" in page
    client.post("/settings/profile", data=form | {"max_minutes": "0"})  # no limit
    assert profile.max_minutes == 0 and "Kolín · Auto · bez omezení" in client.get("/settings").text
    page = client.get("/settings/cars").text
    assert 'name="makes" value="skoda" checked' in page and 'value="250000"' in page


def test_saved_searches_count_new_matches_until_opened(app, client):
    app.state.services.settings.wizard_completed = True
    cars = fixture_cars()
    store_cars(app, cars[:10])
    assert "Zatím žádná" in client.get("/saved").text

    form = {"q": "", "make": "", "sort": "date", "view": "list", "saved_name": "Všechno"}
    r = client.post("/saved", data=form)
    assert "Všechno" in r.text and "nové:" not in r.text
    with app.state.services.sessions() as s:
        saved = s.scalar(select(SavedSearch))
        assert saved.query == "q=&make=&sort=date&price_max=" and saved.notify
    store_cars(app, cars[10:13])
    assert "nové: 3" in client.get("/saved").text

    r = client.get(f"/saved/{saved.id}", follow_redirects=False)
    # As counted: no profile price or start point.
    assert r.headers["location"] == "/?q=&make=&sort=date&price_max=&at="
    assert "nové:" not in client.get("/saved").text  # opened: seen

    r = client.post(f"/saved/{saved.id}/notify")
    assert "(vypnuto)" in r.text
    client.post(f"/saved/{saved.id}/delete")
    assert "Zatím žádná" in client.get("/saved").text

    r = client.post("/saved", data={"q": "octavia"})  # no name typed: the words
    assert "octavia" in r.text


def commute_cars(app, municipalities, many: bool = False) -> dict[str, int]:
    """Cars in Kolín (home), Pardubice (~41 km) and Praha (~55 km); title -> id. `many`:
    three in each."""
    places = {"Kolín": (50.031, 15.201), "Pardubice": (50.04, 15.78), "Praha": (50.08, 14.43)}
    with app.state.services.sessions() as s:
        municipalities(s)
        store = CarStore(s, "bazos")
        for name, (lat, lon) in places.items():
            location = ParsedLocation(
                key=f"t:{name}", precision="address", lat=lat, lon=lon, municipality=name
            )
            for n in range(3 if many else 1):
                title = f"Octavia {name}" + (f" {n}" if n else "")
                store.add(ParsedCar(external_id=title, title=title, make="skoda",
                                    price=250_000, location=location,
                                    images=["https://img.test/1.jpg"]))  # fmt: skip
        store.commit()
        return {c.title: c.id for c in s.scalars(select(Car))}


def map_imported_once(app) -> None:
    """The map was imported, but the data is being imported again: times are estimates."""
    app.state.services.settings.routing_download = True
    app.state.services.routing.state.update(version=1, usable=False)


def test_travel_times_are_estimates_until_the_map_is_downloaded(app, client, municipalities):
    settings = app.state.services.settings
    settings.wizard_completed = True
    settings.profile.home_address, settings.profile.max_minutes = "Kolín", 45
    ids = commute_cars(app, municipalities)
    page = client.get("/").text
    assert "odhad vzdušnou čarou" in page and 'href="/settings/map">Stáhnout mapu' in page
    assert 'name="at" value="50.0300,15.2000"' in page and 'value="travel"' in page
    assert "Octavia Kolín" in page and "Octavia Praha" not in page  # ~55 km: over 45 min
    assert 'class="tt est"' in page
    wide = client.get("/results", params={"at": "50.0300,15.2000", "max_min": "120"}).text
    assert "Octavia Praha" in wide
    params = {"at": "50.0300,15.2000", "from": "Kolín", "mode": "car"}
    window = client.get(f"/commute/car/{ids['Octavia Pardubice']}", params=params).text
    assert "Odhadem asi" in window and 'href="/settings/map">Stáhnout mapu' in window

    assert "Nestaženo" in client.get("/settings").text
    page = client.get("/settings/map").text
    assert "Stáhnout mapu" in page and "Aktualizovat teď" not in page
    r = client.post("/settings/map/update", follow_redirects=False)
    assert r.headers["location"] == "/settings/map" and not settings.routing_download
    r = client.post("/settings/map", data={"routing_area": "auto", "download": "1"},
                    follow_redirects=False)  # fmt: skip
    assert settings.routing_download and r.headers["location"] == "/settings/map?saved=1"
    page = client.get("/settings/map").text
    assert "Stáhnout mapu" not in page and "Aktualizovat teď" in page
    assert "Zatím nejsou připravené" in page and 'hx-trigger="every 3s"' in page
    assert "odhad vzdušnou čarou" in client.get("/").text  # until the import is done


def test_travel_time_from_home_before_the_map_data_is_ready(app, client, municipalities):
    settings = app.state.services.settings
    settings.wizard_completed = True
    settings.profile.home_address, settings.profile.max_minutes = "Kolín", 45
    ids = commute_cars(app, municipalities)
    map_imported_once(app)
    page = client.get("/").text
    # The bar starts from the profile; times are estimates until the import is done.
    assert 'name="at" value="50.0300,15.2000"' in page and 'value="Kolín"' in page
    assert "Doba cesty je zatím odhad" in page
    assert "Octavia Kolín" in page and "Octavia Praha" not in page  # ~55 km: over 45 min
    wide = client.get("/results", params={"at": "50.0300,15.2000", "max_min": "120"}).text
    assert "Octavia Praha" in wide
    off = client.get("/results", params={"at": "50.0300,15.2000", "max_min": "0"}).text
    assert "Octavia Praha" in off  # no limit
    params = {"at": "50.0300,15.2000", "sort": "travel", "max_min": "120"}
    by_time = client.get("/results", params=params).text
    assert order(by_time, [ids["Octavia Kolín"], ids["Octavia Pardubice"]]) == [
        ids["Octavia Kolín"], ids["Octavia Pardubice"]
    ]  # fmt: skip

    window = client.get(f"/commute/car/{ids['Octavia Pardubice']}",
                        params={"at": "50.0300,15.2000", "from": "Kolín", "mode": "car",
                                "arrive": ""}).text  # fmt: skip
    assert "Jak se tam dostanu" in window and "Odhadem asi" in window
    assert "mapy.com" in window and "google.com/maps" in window
    no_start = client.get(f"/commute/car/{ids['Octavia Praha']}", params={"at": ""}).text
    assert "Nejdřív zadejte, odkud pojedete" in no_start and "Odhadem" not in no_start

    area = client.get("/commute/area", params={"at": "50.0300,15.2000", "mode": "car",
                                               "max_min": "20"}).json()  # fmt: skip
    assert area["estimate"] and area["bucket"] == 30 and area["features"]
    assert client.get("/commute/locate", params={"text": "Kolín"}).json()["at"] == "50.0300,15.2000"
    assert client.get("/commute/locate", params={"text": "Nikde"}).status_code == 404
    assert "Kolín" in client.get("/commute/places", params={"from": "Kol"}).text


def test_the_bar_takes_a_saved_profile(app, client, municipalities):
    app.state.services.settings.wizard_completed = True
    commute_cars(app, municipalities)
    form = {"home_address": "Kolín", "travel_mode": "car", "max_minutes": "60",
            "depart_time": "06:30"}  # fmt: skip
    client.post("/settings/profile", data=form)
    assert client.get("/commute/defaults").json() == {
        "from": "Kolín", "at": "50.0300,15.2000", "mode": "car", "max_min": "60",
        "depart": "06:30",
    }  # fmt: skip
    client.post("/settings/profile", data=form | {"home_address": "Nikde"})
    assert client.get("/commute/defaults").json()["at"] == ""  # not found: no start point
    assert "/commute/defaults" in client.get("/static/commute.js").text


def test_saving_what_is_searched_for_restarts_a_running_search(app, client):
    svc = app.state.services
    svc.settings.wizard_completed = True
    client.post("/settings/profile", data={"home_address": "Kolín"})
    client.post("/sources", data={"source_bazos": "on"})
    client.post("/wizard/profile", data={"home_address": "Kolín"})
    client.post("/wizard/sources", data={"source_bazos": "on"})
    assert svc.runner.scope_changes == 4


def test_profile_keeps_the_departure_time(app, client):
    app.state.services.settings.wizard_completed = True
    client.post("/settings/profile", data={"home_address": "Kolín", "depart_time": "06:15"})
    assert app.state.services.settings.profile.depart_time == "06:15"
    client.post("/settings/profile", data={"home_address": "Kolín", "depart_time": "25:00"})
    assert app.state.services.settings.profile.depart_time == "06:15"  # invalid: kept
    assert 'name="depart_time" value="06:15"' in client.get("/settings/profile").text


def test_the_travel_note_and_saved_searches_follow_the_start_point(app, client, municipalities):
    app.state.services.settings.wizard_completed = True
    commute_cars(app, municipalities)
    map_imported_once(app)
    page = client.get("/").text
    assert "Zadejte, odkud pojedete" in page and 'value="travel" selected' not in page
    near = {"at": "50.0300,15.2000", "from": "Kolín", "max_min": "30", "sort": "date"}
    results = client.get("/results", params=near, headers={"HX-Request": "true"}).text
    assert 'id="commute-state" class="muted" hx-swap-oob="true"' in results
    assert "Doba cesty je zatím odhad" in results

    client.post("/saved", data={**near, "view": "list", "saved_name": "Kolín"})
    with app.state.services.sessions() as s:  # a new car near the start, found later
        store = CarStore(s, "bazos")
        location = ParsedLocation(key="t:Velim", precision="address", lat=50.06,
                                  lon=15.15, municipality="Velim")  # fmt: skip
        store.add(
            ParsedCar(
                external_id="Velim",
                title="Octavia Velim",
                location=location,
                images=["https://img.test/1.jpg"],
            )
        )
        store.commit()
    assert "nové: 1" in client.get("/saved").text

    # Saved without a start point: counted and opened without the one from the profile.
    app.state.services.settings.profile.home_address = "Kolín"
    client.post("/saved", data={**near, "at": "", "from": "", "saved_name": "Všude"})
    with app.state.services.sessions() as s:
        saved = s.scalar(select(SavedSearch).where(SavedSearch.name == "Všude"))
    assert "at=&" in saved.query or saved.query.endswith("at=")
    page = client.get(f"/saved/{saved.id}").text
    assert 'name="at" value=""' in page


def test_times_of_saved_start_points_are_kept(app, client, municipalities, monkeypatch):
    monkeypatch.setattr("adacar.routing.commute.IN_USE", 0)  # all asked for long ago
    from adacar.db.models import TravelSet
    from adacar.routing.commute import KEEP_STARTS

    app.state.services.settings.wizard_completed = True
    commute_cars(app, municipalities)
    map_imported_once(app)
    client.post("/saved", data={"at": "50.0300,15.2000", "max_min": "30", "saved_name": "Kolín"})
    for i in range(KEEP_STARTS + 2):  # other start points tried since
        client.get("/results", params={"at": f"50.{i + 10:02d}00,15.0000", "max_min": "30"})
    with app.state.services.sessions() as s:
        origins = set(s.scalars(select(TravelSet.origin)))
    assert "50.0300,15.2000" in origins and len(origins) == KEEP_STARTS + 1


def test_other_sites_cannot_start_work_through_the_app(app, client):
    app.state.services.settings.wizard_completed = True
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert (
        client.get("/commute/area", params={"at": "50.0300,15.2000"}, headers=cross).status_code
        == 403
    )
    assert client.get("/", headers=cross).status_code == 403
    assert client.get("/static/app.css", headers=cross).status_code == 200
    assert client.get("/", headers={"Sec-Fetch-Site": "none"}).status_code == 200  # typed in
