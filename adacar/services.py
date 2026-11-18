"""The app's long-lived parts, made and connected in one place (ARCHITECTURE.md).

`build` makes them for one data folder; `start` and `stop` run their background work. The web
interface (web/app.py) and the tray (cli.py) use what is here; nothing here knows about them.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import QueryParams

from adacar import paths
from adacar.ai.services import AiServices, make_ai_services
from adacar.checks import official
from adacar.checks.service import CheckService
from adacar.config import Settings
from adacar.db.models import Car
from adacar.db.session import db_file, make_engine, make_sessionmaker, migrate
from adacar.desktop import autostart, update
from adacar.geo import ruian
from adacar.geo.home import Locator
from adacar.geo.places import SharedPlaces
from adacar.net.http import Refresh, make_client
from adacar.routing.commute import CommuteService
from adacar.routing.data import HOME_KM, Area, RoutingData
from adacar.routing.motis import MotisServer
from adacar.scheduler import Scheduler
from adacar.scraper.runner import RunHooks, ScrapeRunner
from adacar.scraper.sources import MANUAL
from adacar.search.service import all_cars
from adacar.search.travel import TravelFilter, kept_starts
from adacar.secrets import Secrets, default_secrets
from adacar.system import hardware
from adacar.system.resources import Priority, ResourceManager

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    sessions: sessionmaker[Session]
    secrets: Secrets
    data_dir: Path
    cache_dir: Path
    places: SharedPlaces
    resources: ResourceManager
    ai: AiServices
    checks: CheckService
    runner: ScrapeRunner
    scheduler: Scheduler
    motis: MotisServer
    routing: RoutingData
    commute: CommuteService
    travel: TravelFilter
    autostart_sync: Callable[[bool], object]
    updates: update.Updater

    def set_auto_update(self, on: bool) -> None:
        self.settings.updates.auto = on
        self.settings.save()
        if on:
            self.updates.wake()
        else:
            self.updates.discard()

    def set_automation(self, on: bool | None = None) -> None:
        """Automatic checks changed (or `on`: switch them): rescheduled at once."""
        if on is None:
            self.scheduler.wake()
        else:
            self.scheduler.set_enabled(on)
        auto = self.settings.automation
        self.autostart_sync(auto.enabled and auto.start_with_windows)

    def start(self) -> None:
        if self.settings.wizard_completed:
            try:  # resume an interrupted download, follow hardware changes (Auto)
                self.ai.manager.start_download()
                self.ai.enricher.start()  # warning signs; the AI finishes what was left
            except Exception:
                log.exception("AI start-up failed")
            self.checks.start()
            self.set_automation()  # the Run value follows the app if it moved
        self.scheduler.start()
        self.routing.start()
        self.updates.start()

    def stop(self) -> None:
        self.updates.stop()
        self.scheduler.stop()  # first: it could start a new search meanwhile
        self.runner.stop()
        self.checks.stop()
        self.routing.stop()
        self.motis.close()
        self.ai.shutdown()


def build(
    settings: Settings | None = None,
    db_path: Path | None = None,
    cache_dir: Path | None = None,
    data_dir: Path | None = None,
    secrets: Secrets | None = None,
    runner_factory=None,
    ai_factory=None,
    resources: ResourceManager | None = None,
    autostart_sync: Callable[[bool], object] | None = None,  # the Run value added or removed
    refreshes: tuple[Refresh, ...] | None = None,  # None: the official lists (checks)
    routing_updates: bool = True,  # False in tests: no map data downloads
) -> Services:
    settings = settings or Settings.load()
    engine = make_engine(db_path or db_file())
    migrate(engine)
    sessions = make_sessionmaker(engine)
    data_dir = data_dir or paths.data_dir()
    cache_dir = cache_dir or data_dir / "cache"
    keys = secrets or default_secrets()
    resources = resources or ResourceManager(hardware.probe())
    places = SharedPlaces(sessions)
    locator = Locator(places, cache_dir / "ruian" / ruian.FILE_NAME)

    # --- AI (PLAN §6): the profile's cars are read first ---
    def profile_cars(s: Session) -> set[int] | None:
        f = travel.filters(QueryParams())  # places found since the last search get times
        return {r.id for r in all_cars(s, f, [Car.id])} if f.is_filtered() else None

    ai: AiServices = (ai_factory or make_ai_services)(
        settings, sessions, data_dir, resources, profile_cars
    )

    # --- Vehicle and seller checks (PLAN §4.5): their warnings go through the rules pass ---
    lists_dir = cache_dir / "checks"
    checks = CheckService(sessions, lists_dir, lambda: settings.checks, keys, profile_cars)
    checks.on_changed = ai.enricher.start
    if refreshes is None:
        refreshes = official.refreshes(lists_dir)

    # --- Travel times to the cars (PLAN §4.6) ---
    vendor = paths.vendor_dir()
    motis = MotisServer(vendor, paths.log_dir() / "motis.log")

    def wanted_area() -> Area | None:
        choice = settings.routing_area
        if choice == "auto":
            choice = "whole" if resources.limits.routing_radius_km is None else "home"
        if choice == "whole":
            return Area()
        home = locator.locate(settings.profile.home_address)
        return Area(HOME_KM, home.lat, home.lon) if home else None

    def free_ram_for_import() -> None:
        if resources.limits.parallel_heavy == 1:  # 8 GB: the model and the import never overlap
            ai.manager.server.stop()

    routing = RoutingData(
        data_dir / "routing",
        vendor,
        motis,
        wanted_area,
        make_client,
        allowed=lambda: routing_updates and settings.routing_download,
        heavy=lambda: resources.heavy(Priority.IMPORT),
        busy=resources.busy,
        before_import=free_ram_for_import,
    )

    # `travel` is made below: `keep` is first called by a later computation.
    commute = CommuteService(
        sessions, routing, motis, keep=lambda s: kept_starts(s, travel.home_start())
    )
    travel = TravelFilter(lambda: settings.profile, locator, routing, commute)
    routing.on_ready = travel.routing_ready

    # --- Searching ---
    def after_run() -> None:
        ai.enricher.start()
        checks.wake()

    def stop_enricher() -> None:
        """Before the cars are deleted: an answer being read would update a deleted car."""
        ai.enricher.stop()
        ai.enricher.wait(15)

    runner = (runner_factory or ScrapeRunner)(
        sessions,
        cache_dir,
        profile=lambda: settings.profile,
        source_choices=lambda: settings.sources,
        hooks=RunHooks(before_clear=stop_enricher, after_run=after_run),
        kept_sources=(MANUAL,),
    )
    scheduler = Scheduler(settings, runner, sessions, refreshes, prepare=travel.prepare)

    return Services(
        settings=settings,
        sessions=sessions,
        secrets=keys,
        data_dir=data_dir,
        cache_dir=cache_dir,
        places=places,
        resources=resources,
        ai=ai,
        checks=checks,
        runner=runner,
        scheduler=scheduler,
        motis=motis,
        routing=routing,
        commute=commute,
        travel=travel,
        autostart_sync=autostart_sync or autostart.sync,
        updates=update.Updater(data_dir / update.FOLDER, lambda: settings.updates.auto),
    )
