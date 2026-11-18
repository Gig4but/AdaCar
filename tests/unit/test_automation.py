import threading
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from adacar.config import Settings
from adacar.core import ParsedCar
from adacar.db.models import Car, SavedSearch, SourceState
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.net.http import Refresh
from adacar.scheduler import Scheduler
from adacar.scraper.runner import Progress
from adacar.scraper.store import CarStore
from adacar.worker import Worker

T0 = datetime(2026, 10, 13, 8, 0)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def forward(self, **delta) -> None:
        self.now += timedelta(**delta)


class Runner:
    """Starts "runs" that the test finishes; stores cars like a search would."""

    def __init__(self, sessions, clock: Clock) -> None:
        self.sessions = sessions
        self.clock = clock
        self.progress = Progress()
        self.starts: list[datetime] = []

    def start(self, fresh: bool = False) -> bool:
        if self.progress.running:
            return False
        self.starts.append(self.clock())
        with self.sessions() as s:
            after = s.scalar(select(func.max(Car.id))) or 0
        self.progress = Progress(running=True, phase="start", after_id=after)
        return True

    def finish(self, titles=(), phase="done") -> None:
        with self.sessions() as s:
            store = CarStore(s, "x")
            for title in titles:
                store.add(
                    ParsedCar(external_id=title, title=title, images=["https://img.test/1.jpg"])
                )
            store.commit()
        self.progress.running, self.progress.phase = False, phase

    def last_start(self):
        return self.starts[-1] if self.starts else None


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    yield make_sessionmaker(engine)
    engine.dispose()


@pytest.fixture
def setup(tmp_path, sessions):
    settings = Settings.load(tmp_path / "config.json")
    settings.wizard_completed = True
    clock = Clock()
    runner = Runner(sessions, clock)
    notes: list[tuple[str, str]] = []
    scheduler = Scheduler(
        settings, runner, sessions, notify=lambda title, text: notes.append((title, text)),
        clock=clock,
    )  # fmt: skip
    return settings, clock, runner, scheduler, notes


def test_automatic_checks_are_off_until_switched_on(setup):
    settings, clock, runner, scheduler, _ = setup
    assert settings.automation.enabled is False
    scheduler.tick()
    assert runner.starts == [] and scheduler.next_check() is None

    scheduler.set_enabled(True)  # from the tray or Settings: no restart needed
    assert scheduler.next_check() == T0  # never searched: now
    scheduler.tick()
    assert runner.starts == [T0]
    scheduler.tick()  # running: not started twice
    assert runner.starts == [T0]


def test_the_interval_counts_from_the_last_search_and_changes_at_once(setup):
    settings, clock, runner, scheduler, _ = setup
    runner.start()  # a search the user started
    runner.finish()
    scheduler.set_enabled(True)
    assert scheduler.next_check() == T0 + timedelta(hours=12)
    clock.forward(hours=7)
    scheduler.tick()
    assert len(runner.starts) == 1

    settings.automation.interval_hours = 6  # changed in Settings
    scheduler.wake()
    scheduler.tick()
    assert runner.starts == [T0, T0 + timedelta(hours=7)]
    runner.finish()
    clock.forward(hours=5, minutes=59)
    scheduler.tick()
    assert len(runner.starts) == 2
    clock.forward(minutes=1)
    scheduler.tick()
    assert len(runner.starts) == 3


def test_a_pause_holds_the_checks_until_resumed(setup):
    settings, clock, runner, scheduler, _ = setup
    scheduler.set_enabled(True)
    scheduler.pause()
    assert scheduler.paused() and scheduler.next_check() == T0 + timedelta(hours=24)
    scheduler.tick()
    assert runner.starts == []
    clock.forward(hours=2)
    scheduler.resume()
    scheduler.tick()
    assert runner.starts == [T0 + timedelta(hours=2)]
    assert Settings.load(settings._path).automation.paused_until is None  # saved


def test_a_failed_check_is_tried_again_after_an_hour(setup):
    settings, clock, runner, scheduler, _ = setup
    scheduler.set_enabled(True)
    scheduler.tick()
    runner.finish(phase="failed")  # e.g. no internet
    clock.forward(minutes=1)
    scheduler.tick()
    assert scheduler.next_check() == T0 + timedelta(hours=1, minutes=1)
    clock.forward(hours=1)
    scheduler.tick()
    assert len(runner.starts) == 2


def test_a_search_after_a_failed_check_replaces_the_retry(setup):
    settings, clock, runner, scheduler, _ = setup
    scheduler.set_enabled(True)
    scheduler.tick()
    runner.finish(phase="failed")
    scheduler.tick()
    clock.forward(minutes=10)
    runner.start()  # the user searched
    runner.finish()
    assert scheduler.next_check() == T0 + timedelta(hours=12, minutes=10)


def test_the_tray_menu_is_redrawn_when_a_pause_runs_out(setup):
    settings, clock, runner, scheduler, _ = setup
    redraws: list[bool] = []
    scheduler.on_change = lambda: redraws.append(scheduler.paused())
    scheduler.set_enabled(True)
    scheduler.pause()
    scheduler.tick()
    runner.start()  # a search runs: no check starts when the pause ends
    redraws.clear()
    clock.forward(hours=24)
    scheduler.tick()
    assert redraws == [False]  # "Resume" leaves the menu


def test_new_matches_are_notified_after_an_automatic_check(setup, sessions):
    settings, clock, runner, scheduler, notes = setup
    scheduler.set_enabled(True)
    scheduler.tick()
    runner.finish(["Octavia Kolín", "Fabia"])
    scheduler.tick()
    assert notes == [("AdaCar", "Nová auta: 2")]  # no saved searches: all new cars

    with sessions() as s:
        s.add(SavedSearch(name="Octavia", query="q=octavia", created_at=T0))
        s.add(SavedSearch(name="Superb", query="q=superb", created_at=T0))
        s.add(SavedSearch(name="Fabia", query="q=fabia", notify=False, created_at=T0))
        s.commit()
    clock.forward(hours=12)
    scheduler.tick()
    runner.finish(["Octavia Praha", "Fabia Brno"])
    scheduler.tick()
    # Only the saved searches that notify and found something.
    assert notes[-1][1].splitlines() == ["Octavia: nová auta 1"]

    # Nothing new, or notifications off: no notification.
    clock.forward(hours=12)
    scheduler.tick()
    runner.finish([])
    scheduler.tick()
    settings.automation.notifications = False
    clock.forward(hours=12)
    scheduler.tick()
    runner.finish(["Octavia Beroun"])
    scheduler.tick()
    assert len(notes) == 2


def test_a_search_the_user_started_is_not_notified(setup):
    settings, clock, runner, scheduler, notes = setup
    scheduler.set_enabled(True)
    runner.start()
    scheduler.tick()  # running: the scheduler waits
    runner.finish(["Octavia"])
    scheduler.tick()
    assert notes == [] and len(runner.starts) == 1


def test_a_search_started_right_after_a_check_is_not_reported_as_it(setup):
    settings, clock, runner, scheduler, notes = setup
    scheduler.set_enabled(True)
    scheduler.tick()
    runner.finish(["Octavia"])
    runner.start()  # the user searched before the scheduler looked again
    scheduler.tick()
    assert notes == [("AdaCar", "Nová auta: 1")]


def test_data_is_refreshed_when_due_whether_checks_are_on_or_not(setup, sessions):
    settings, clock, runner, scheduler, _ = setup
    calls: list[str] = []
    failing = {"second": True}

    def download(name):
        def run(client):
            calls.append(name)
            if failing.get(name):
                raise httpx.ConnectError("offline")

        return run

    scheduler.refreshes = (
        Refresh("first", timedelta(days=1), download("first")),
        Refresh("second", timedelta(days=1), download("second")),
    )
    scheduler.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(None))
    scheduler.tick()
    assert calls == ["first", "second"] and runner.starts == []
    clock.forward(minutes=30)
    scheduler.tick()
    assert calls == ["first", "second"]  # the failed one waits an hour
    failing.clear()
    clock.forward(minutes=31)
    scheduler.tick()
    assert calls == ["first", "second", "second"]
    clock.forward(hours=23)
    scheduler.tick()
    assert calls[-1] == "first"
    with sessions() as s:
        state = s.get(SourceState, "data:second").state
    assert "failed_at" not in state and state["done_at"]


def test_nothing_runs_before_the_wizard_is_done(setup):
    settings, clock, runner, scheduler, _ = setup
    settings.wizard_completed = False
    settings.automation.enabled = True
    called = []
    scheduler.refreshes = (Refresh("first", timedelta(days=1), called.append),)
    scheduler.tick()
    assert runner.starts == [] and called == []


def test_a_worker_runs_rounds_until_stopped():
    class Counter(Worker):
        name = "counter"

        def __init__(self) -> None:
            super().__init__()
            self.rounds = 0
            self.ran = threading.Event()

        def work(self) -> float:
            self.rounds += 1
            self.ran.set()
            return 3600  # the next round only when woken

    worker = Counter()
    worker.start()
    assert worker.ran.wait(5)
    worker.ran.clear()
    worker.wake()
    assert worker.ran.wait(5)
    worker.stop()
    assert worker.rounds == 2 and worker.stopping
    assert worker.sleep(10)  # returns at once: stopping
