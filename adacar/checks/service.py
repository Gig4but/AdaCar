"""Vehicle and seller checks in the background and for opened cards (PLAN §4.5).

The pass (after each search, and hourly):
1. cars with a VIN: the km of their ads added to the VIN's history, and their check warnings
   (VIN decoded, mileage history, the register's record when read) kept in
   `car.check_warnings`; the enricher's rules pass merges them into `car.warnings`
   (`on_changed`);
2. dealers' cars linked to their dealer by name;
3. with "auto" on: the register (with the user's key) for the profile's cars, and the
   dealers (resolved, checked, rated) the profile's first, within limits per pass.
An opened car or dealer asks for what is missing or old at once, but not again within an
hour of a try that failed (a register that is down is not asked on every look).
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import bindparam, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from adacar.checks import official, register, vehicle, vin
from adacar.checks.dealers import DUE, Dealers
from adacar.config import CheckSettings
from adacar.core import utcnow
from adacar.db.models import Car, VehicleCheck, table
from adacar.net.http import make_client
from adacar.secrets import Secrets
from adacar.worker import Worker

log = logging.getLogger(__name__)

REGISTER_KEY = "rsv_api"  # secrets: the user's key to the vehicle register
GOOGLE_KEY = "google_places"  # secrets: the user's Google Places key
WAKE_EVERY = 3600.0
REGISTER_PER_PASS = 100  # VINs (25 a minute: four minutes)
RESOLVE_PER_PASS = 100  # dealer names (≤ 4 ARES calls each)
CHECKS_PER_PASS = 1000  # dealers (one ARES call per 100)
GOOGLE_PER_PASS = 10
CHUNK = 500  # VINs per query
RETRY_FAILED = 600.0  # seconds after a pass that failed
CARD_RETRY = timedelta(hours=1)  # an opened card does not ask again sooner after a try
# What the checks compare of a car (vehicle.warnings).
COLUMNS = (
    Car.id, Car.vin, Car.title, Car.description, Car.make, Car.year, Car.mileage_km,
    Car.power_kw, Car.fuel, Car.first_owner, Car.origin_country, Car.check_warnings,
)  # fmt: skip

ProfileCars = Callable[[Session], set[int] | None]


@dataclass
class CheckProgress:
    running: bool = False
    phase: str = ""  # vehicles | register | dealers | done | failed
    message: str = ""  # i18n key of a problem the user can fix, e.g. a refused key


class CheckService(Worker):
    name = "checks"

    def __init__(
        self,
        sessions: sessionmaker[Session],
        lists_dir: Path,
        settings: Callable[[], CheckSettings],
        secrets: Secrets,
        profile_cars: ProfileCars = lambda s: None,
        client_factory: Callable[[], httpx.Client] = make_client,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__()
        self.sessions = sessions
        self.settings = settings
        self.secrets = secrets
        self.profile_cars = profile_cars
        self.client_factory = client_factory
        self.clock = clock
        self.progress = CheckProgress()
        self.dealers = Dealers(
            sessions,
            official.VatList(lists_dir),
            lambda: secrets.get(GOOGLE_KEY),
            clock,
            lambda: self.stopping,
        )
        self.on_changed: Callable[[], object] = lambda: None  # car warnings changed
        # One writer of VIN records and check warnings: the pass or an opened car.
        self._lock = threading.Lock()
        self._tried: dict[tuple[str, int], datetime] = {}  # (car | dealer, id) -> last try

    # --- the background pass ----------------------------------------------------------------

    def work(self) -> float:
        try:
            more = self.run_pass()
            self.progress.phase = "done"
        except Exception as exc:
            log.exception("checks pass failed")
            self.progress.phase, self.progress.message = "failed", str(exc)[:300]
            return RETRY_FAILED
        finally:
            self.progress.running = False
        return 5.0 if more else WAKE_EVERY

    def run_pass(self) -> bool:
        """One round; True when more is waiting. Warnings already written reach the cars
        (`on_changed`) even when a later step fails."""
        kept = self.progress.message if self.progress.message.startswith("checks.") else ""
        self.progress = CheckProgress(running=True, phase="vehicles", message=kept)
        changed = 0
        try:
            changed = self.update_vehicles()
            self.dealers.link()
            self.dealers.apply_vat_list()
            if not self.settings().auto:
                return False
            with self.sessions() as s:
                profile = self.profile_cars(s)
                vins = self._profile_vins(s, profile)
                dealers = self.dealers.wanted(s, profile)
            self.progress.phase = "register"
            looked = self.look_up(vins, REGISTER_PER_PASS)
            if looked:
                changed += self.update_vehicles()
            self.progress.phase = "dealers"
            resolved = checked = 0
            try:
                with self.client_factory() as client:
                    ares = official.Ares(client)
                    resolved = self.dealers.resolve_due(dealers, ares, RESOLVE_PER_PASS)
                    checked = self.dealers.check_due(dealers, client)
                    self.dealers.rate_due(dealers, client, GOOGLE_PER_PASS, background=True)
            except (httpx.HTTPError, ValueError) as exc:  # offline, or a register is down
                log.info("dealer checks wait for the next pass: %s", exc)
            more = looked >= REGISTER_PER_PASS or resolved >= RESOLVE_PER_PASS
            return (more or checked >= CHECKS_PER_PASS) and not self.stopping
        finally:
            if changed:
                self.on_changed()

    def _profile_vins(self, s: Session, profile: set[int] | None) -> list[str]:
        """VINs of the profile's cars, newest first; none when the profile filters nothing
        (the whole market is too much for the register's limit)."""
        if profile is None:
            return []
        rows = s.execute(
            select(Car.id, Car.vin)
            .where(Car.status == "active", Car.vin.is_not(None))
            .order_by(Car.first_seen_at.desc())
        )
        return [code for car_id, raw in rows if car_id in profile and (code := vin.clean(raw))]

    # --- cars by VIN --------------------------------------------------------------------------

    def update_vehicles(self, car_ids: list[int] | None = None) -> int:
        """Km readings and check warnings of active cars with a VIN (or of `car_ids`); a car
        whose VIN is gone loses its check warnings. Returns how many cars' warnings
        changed. Only `cars.check_warnings` is written: the AI and the store own the rest."""
        today = self.clock().date().isoformat()
        with self._lock, self.sessions() as s:
            if car_ids is None:
                warned = func.json_array_length(Car.check_warnings) > 0
                where = [Car.status == "active", or_(Car.vin.is_not(None), warned)]
            else:
                where = [Car.id.in_(car_ids)]
            by_vin: dict[str, list] = defaultdict(list)
            changes: list[dict] = []
            for row in s.execute(select(*COLUMNS).where(*where)):
                if code := vin.clean(row.vin):
                    by_vin[code].append(row)
                elif row.check_warnings:
                    changes.append({"car_id": row.id, "found": []})
            records: dict[str, VehicleCheck] = {}
            codes = list(by_vin)
            for i in range(0, len(codes), CHUNK):
                query = select(VehicleCheck).where(VehicleCheck.vin.in_(codes[i : i + CHUNK]))
                records.update({r.vin: r for r in s.scalars(query)})
            for code, group in by_vin.items():
                record = records.get(code)
                if record is None:
                    record = records[code] = VehicleCheck(vin=code, km_seen=[], register={})
                    s.add(record)
                seen = record.km_seen or []
                # Several ads of one VIN at once: lowest first (no rollback among them).
                for row in sorted(group, key=lambda r: r.mileage_km or 0):
                    if row.mileage_km:
                        seen = vehicle.record_km(seen, row.mileage_km, today, row.id)
                if seen != record.km_seen:
                    record.km_seen = seen
                for row in group:
                    try:
                        found = vehicle.warnings(row, code, seen, record.register or {})
                    except (TypeError, ValueError, KeyError):  # an odd register record
                        log.warning("checks of car %s failed", row.id, exc_info=True)
                        continue
                    if found != (row.check_warnings or []):
                        changes.append({"car_id": row.id, "found": found})
            if changes:
                cars = table(Car)
                s.execute(
                    update(cars)
                    .where(cars.c.id == bindparam("car_id"))
                    .values(check_warnings=bindparam("found")),
                    changes,
                )
            s.commit()
        return len(changes)

    def look_up(self, vins: list[str], limit: int, force: bool = False) -> int:
        """The register's records of VINs not read or read 30 days ago; returns how many
        were asked. Without a key, nothing."""
        key = self.secrets.get(REGISTER_KEY)
        if not key or not vins:
            return 0
        old = self.clock() - DUE
        with self.sessions() as s:
            fresh = set()
            for i in range(0, len(vins), CHUNK):
                query = select(VehicleCheck.vin).where(
                    VehicleCheck.vin.in_(vins[i : i + CHUNK]), VehicleCheck.register_at >= old
                )
                fresh.update(s.scalars(query))
        due = [v for v in dict.fromkeys(vins) if force or v not in fresh][:limit]
        asked = 0
        with self.client_factory() as client:
            reg = register.Register(client, key)
            for code in due:
                if self.stopping:
                    break
                status = "ok"
                try:
                    facts = reg.lookup(code)
                except register.BadKey:
                    self.progress.message = "checks.register.bad_key"
                    break
                except register.TooFast:
                    break
                except httpx.HTTPError as exc:
                    log.info("register lookup of %s failed: %s", code, exc)
                    break  # offline or down: the next pass tries again
                except Exception:  # an answer it cannot read: again in 30 days
                    log.warning("register answer for %s unreadable", code, exc_info=True)
                    facts, status = None, "error"
                self.progress.message = ""
                with self._lock, self.sessions() as s:
                    record = s.get(VehicleCheck, code) or VehicleCheck(vin=code, km_seen=[])
                    record.register = facts or {}
                    record.register_status = status if status == "error" or facts else "not_found"
                    record.register_at = self.clock()
                    s.add(record)
                    s.commit()
                asked += 1
        return asked

    # --- what the cards ask ------------------------------------------------------------------

    def _may_try(self, kind: str, item: int, force: bool) -> bool:
        """An opened card asks its registers unless it did within CARD_RETRY."""
        now = self.clock()
        last = self._tried.get((kind, item))
        if not force and last is not None and now - last < CARD_RETRY:
            return False
        self._tried[(kind, item)] = now
        return True

    def check_car(self, car_id: int, force: bool = False) -> bool:
        """An opened car: its register record if missing or old (or `force`), its warnings.
        False when the register was due but could not be read."""
        with self.sessions() as s:
            car = s.get(Car, car_id)
            code = vin.clean(car.vin) if car else None
            record = s.get(VehicleCheck, code) if code else None
        if code is None:
            return True
        ok = True
        if (force or self.needs_check(record)) and self._may_try("car", car_id, force):
            ok = self.look_up([code], 1, force=True) == 1
            if ok:
                self._tried.pop(("car", car_id), None)  # read: no reason to wait
        if self.update_vehicles([car_id]):
            self.on_changed()
        return ok

    def check_dealer(self, dealer_id: int, force: bool = False) -> None:
        """An opened dealer: resolved, checked and rated if missing or old (or `force`)."""
        if not self._may_try("dealer", dealer_id, force):
            return
        ids = [dealer_id]
        with self.client_factory() as client:
            ares = official.Ares(client)
            self.dealers.resolve_due(ids, ares, 1, force)
            self.dealers.check_due(ids, client, force)
            self.dealers.rate_due(ids, client, 1, force)

    def correct_dealer(self, dealer_id: int, ico: str) -> str | None:
        """The user's pick; None when taken, else why not ("bad_ico", "person")."""
        with self.client_factory() as client:
            problem = self.dealers.correct(dealer_id, ico, official.Ares(client))
            if problem is None:
                self.dealers.check_due([dealer_id], client, force=True)
                self.dealers.rate_due([dealer_id], client, 1, force=True)
        return problem

    def needs_check(self, record: VehicleCheck | None) -> bool:
        """A car's card asks the register: a key is set and the record is missing or old."""
        if not self.secrets.get(REGISTER_KEY):
            return False
        when = record.register_at if record else None
        return when is None or when < self.clock() - DUE
