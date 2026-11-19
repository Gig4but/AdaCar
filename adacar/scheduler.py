"""Automatic checks for new cars and background data refreshes (PLAN §7.1 step 4).

One background thread looks at the clock every minute:
- automatic checks (off until the user switches them on) start a search when the interval
  has passed since the last one began, manual searches included; a pause holds them back;
  a check that failed everywhere (e.g. no internet) is tried again after an hour;
- after an automatic check, the user is told about new and cheaper matches of the saved
  searches (or about all new cars when none are saved) and cheaper cars marked for a viewing;
- small official lists are downloaded again when they are due, whether automatic checks
  are on or not (`Refresh`); a failed download is retried after an hour.

Settings changed in the UI or the tray take effect at once (`wake`), without a restart.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from adacar.config import Settings
from adacar.core import utcnow
from adacar.db.models import Car, SavedSearch, SourceState
from adacar.i18n import translate
from adacar.net.http import Refresh, make_client
from adacar.scraper.runner import Progress, ScrapeRunner
from adacar.search.saved import filters_of, matches_after
from adacar.search.service import Filters, count_cheaper
from adacar.worker import Worker

log = logging.getLogger(__name__)

LOOK_EVERY = 60.0  # seconds between looks at the clock
RETRY_AFTER = timedelta(hours=1)
PAUSE = timedelta(hours=24)  # "Pozastavit" in the tray and Settings
MAX_LINES = 4  # saved searches and cars named in one notification


def _price(car: Car) -> str:
    currency = "Kč" if car.price_currency == "CZK" else car.price_currency
    return f"{car.price or 0:,}\xa0{currency}".replace(",", "\xa0")


class Scheduler(Worker):
    name = "scheduler"
    stop_timeout = 5

    def __init__(
        self,
        settings: Settings,
        runner: ScrapeRunner,
        sessions: sessionmaker[Session],
        refreshes: Sequence[Refresh] = (),
        notify: Callable[[str, str], None] = lambda title, text: None,
        prepare: Callable[[Filters], None] = lambda f: None,
        client_factory: Callable[[], httpx.Client] = make_client,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.runner = runner
        self.sessions = sessions
        self.refreshes = refreshes
        self.notify = notify  # (title, text): a Windows notification from the tray icon
        self.client_factory = client_factory
        self.clock = clock
        # The tray icon, made after the services, sets `notify` and this.
        self.on_change: Callable[[], None] = lambda: None  # e.g. redraw the tray menu
        self.prepare = prepare  # what saved searches need before counting: travel times
        self._watching: Progress | None = None  # the running automatic check
        self._failed_at: datetime | None = None  # when the last automatic check failed
        self._was_paused = False

    # --- control ----------------------------------------------------------------------------

    def wake(self) -> None:
        """The settings changed: look again now."""
        super().wake()
        self.on_change()

    def set_enabled(self, on: bool) -> None:
        self.settings.automation.enabled = on
        self.settings.automation.paused_until = None
        self.settings.save()
        self.wake()

    def pause(self) -> None:
        self.settings.automation.paused_until = self.clock() + PAUSE
        self.settings.save()
        self.wake()

    def resume(self) -> None:
        self.settings.automation.paused_until = None
        self.settings.save()
        self.wake()

    def paused(self) -> bool:
        return self.settings.automation.paused(self.clock())

    def next_check(self) -> datetime | None:
        """When the next automatic check starts (UTC); None while they are off."""
        auto = self.settings.automation
        if not auto.enabled or not self.settings.wizard_completed:
            return None
        last = self.runner.last_start()
        due = last + timedelta(hours=auto.interval_hours) if last else self.clock()
        if self._failed_at is not None and (last is None or last <= self._failed_at):
            due = min(due, self._failed_at + RETRY_AFTER)  # no search since the failed check
        if auto.paused_until is not None and auto.paused(self.clock()):
            due = max(due, auto.paused_until)
        return due

    # --- the loop ---------------------------------------------------------------------------

    def work(self) -> float:
        self.tick()
        return LOOK_EVERY

    def tick(self) -> None:
        """One look at the clock: what is due runs now."""
        if not self.settings.wizard_completed:
            return
        watched = self._watching
        if watched is not None and not watched.running:
            self._watching = None
            self._finished(watched)
        if self.paused() != self._was_paused:  # also when a pause ran out: the tray menu changes
            self._was_paused = not self._was_paused
            self.on_change()
        self._refresh_data()
        due = self.next_check()
        if due is not None and due <= self.clock() and not self.runner.progress.running:
            log.info("automatic check starts")
            if self.runner.start():
                self._watching, self._failed_at = self.runner.progress, None
                self.on_change()

    def _finished(self, p: Progress) -> None:
        if p.phase == "failed":
            self._failed_at = self.clock()
            log.warning("automatic check failed, tried again in an hour: %s", p.message)
        self.on_change()
        if self.settings.automation.notifications:
            message = self.news(p)
            if message:
                self.notify("AdaCar", message)

    def news(self, p: Progress) -> str:
        """What the check found for the user, as notification lines ("" = nothing)."""
        lang = self.settings.language
        lines = []
        if p.cleared:  # after a clear every car is "new": not news
            return ""
        with self.sessions() as session:
            visits = []  # the cars marked for a viewing come first
            if p.started_at is not None:
                marked = session.scalars(
                    select(Car)
                    .where(
                        Car.visit_at.is_not(None),
                        Car.hidden_at.is_(None),
                        Car.price_dropped_at >= p.started_at,
                    )
                    .order_by(Car.visit_at)
                )
                visits = [
                    translate(lang, "notify.visit_cheaper", title=car.title, price=_price(car))
                    for car in marked
                ]
            saved = list(session.scalars(select(SavedSearch).where(SavedSearch.notify)))
            for s in saved:
                f = filters_of(s)
                self.prepare(f)
                found = [
                    translate(lang, key, n=n)
                    for key, n in (
                        ("notify.new_part", matches_after(session, f, p.after_id)),
                        ("notify.cheaper_part", self._cheaper(session, f, p)),
                    )
                    if n
                ]
                if found:
                    what = ", ".join(found)
                    lines.append(translate(lang, "notify.saved", name=s.name, what=what))
            if not saved and (n := matches_after(session, Filters(), p.after_id)):
                lines.append(translate(lang, "notify.new", n=n))
        # Many cheaper cars to view leave room for the saved searches too.
        visits = visits[: MAX_LINES - min(len(lines), MAX_LINES // 2)]
        return "\n".join([*visits, *lines][:MAX_LINES])

    @staticmethod
    def _cheaper(session: Session, f: Filters, p: Progress) -> int:
        return count_cheaper(session, f, p.started_at) if p.started_at is not None else 0

    def _refresh_data(self) -> None:
        due = [r for r in self.refreshes if self._refresh_due(r)]
        if not due:
            return
        with self.client_factory() as client:
            for refresh in due:
                self._run_refresh(refresh, client)

    def _refresh_due(self, refresh: Refresh) -> bool:
        with self.sessions() as session:
            row = session.get(SourceState, refresh.state_id)
        state = (row.state if row else None) or {}
        now = self.clock()
        failed = state.get("failed_at")
        if failed and datetime.fromisoformat(failed) + RETRY_AFTER > now:
            return False
        done = state.get("done_at")
        return not done or datetime.fromisoformat(done) + refresh.every <= now

    def _run_refresh(self, refresh: Refresh, client: httpx.Client) -> None:
        state: dict[str, str] = {}
        try:
            refresh.run(client)
            state["done_at"] = self.clock().isoformat()
            log.info("%s refreshed", refresh.name)
        except Exception as exc:
            log.warning("%s not refreshed: %s", refresh.name, exc)
            state["failed_at"] = self.clock().isoformat()
            state["error"] = str(exc)[:300]
        with self.sessions() as session:
            row = session.get(SourceState, refresh.state_id)
            if row is None:
                row = SourceState(source_id=refresh.state_id)
                session.add(row)
            row.state = {k: v for k, v in (row.state or {}).items() if k == "done_at"} | state
            row.last_run_at = self.clock()
            session.commit()
