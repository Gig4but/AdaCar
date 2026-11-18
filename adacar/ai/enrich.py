"""Background work after each search (PLAN §6): warning signs, what the sources left out of
their ads, and the cover photos.

1. Rules (no model, seconds): warning signs of every car for sale, in an ad's words
   ("záloha předem") and a price far below similar cars.
2. Added pictures (the model with its vision part): the text of a screenshot the user added
   is written out and added to the ad, which then goes through the AI like any other text.
3. AI (the local model, minutes to hours): ads with a description that still lack fields;
   private sellers' ads also for warning signs. Cars matching the profile go first; on slow
   PCs only those are read.
4. Cover photos (a model that tells photos apart, `ModelSpec.photos`): the first photos of
   the profile's newest cars, until one shows the car from the front (`Car.photos` then opens
   with it).

Only empty fields are filled; what a source states is never overwritten. Filled fields are
recorded in `car.extra["origin"]` so the UI can mark them. Each car is read once per content
version (`ai_hash`); answers are cached by input in `llm_cache`.
"""

import hashlib
import logging
import statistics
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import bindparam, exists, func, literal, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from adacar.ai import vision
from adacar.ai.client import BadAnswer, LlmClient, LlmError, ServerDown
from adacar.ai.extract import FIELDS, PROMPT_VERSION, Extraction, extract, validate
from adacar.ai.manager import Busy, ModelManager, NoModel
from adacar.core import utcnow
from adacar.db.models import Car, CarImage, LlmCache, table
from adacar.net.http import make_client
from adacar.normalize import cars
from adacar.normalize.text import fold

log = logging.getLogger(__name__)

MIN_DESCRIPTION = 150  # shorter texts (a listing card's line) have nothing more to read
AI_CHUNK = 10  # cars per hold of the AI slot, so user-facing work can step in
WAIT_SECONDS = 60
SLOW_SECONDS = 20.0  # above this per ad, only cars matching the profile are read
COVER_PHOTOS = 4  # photos looked at per car, at most
COVERS_PER_RUN = 300
MAX_PHOTO_BYTES = 15_000_000
# A price far below similar cars (same make, model and year; at least SIMILAR_MIN of them):
PRICE_LOW = 0.5  # of their median price, unless the car has more km than they have
WORN = 1.2  # "more km": above this share of their median km
PRICE_LOW_FROM = 100_000  # cheaper cars (old ones) differ too much in price to compare
SIMILAR_MIN = 6
MIN_KM = 1000  # less: a source's "not given" (0, 1, 100)
IMAGE_TEXT_HEADER = "Text z obrázku:"

ProfileCars = Callable[[Session], set[int] | None]  # None: no profile, every car alike


@dataclass
class EnrichProgress:
    running: bool = False
    phase: str = ""  # rules | pictures | ai | covers | waiting | done | failed
    done: int = 0
    total: int = 0
    wait_reason: str = ""  # i18n key while waiting
    message: str = ""


class Enricher:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        manager: ModelManager,
        profile_cars: ProfileCars = lambda _s: None,
        uploads_dir: Path | None = None,
        wait_seconds: float = WAIT_SECONDS,
        http_factory: Callable[[], httpx.Client] = lambda: make_client(timeout=30),
    ) -> None:
        self.sessions = sessions
        self.manager = manager
        self.profile_cars = profile_cars
        self.uploads_dir = uploads_dir
        self.wait_seconds = wait_seconds
        self.http_factory = http_factory
        self.progress = EnrichProgress()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._again = False  # started again during a run: one more run after it
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Starts a run; during a run, one more follows it (the run took its cars already)."""
        with self._lock:
            if self.progress.running:
                self._again = True
                return False
            self._stop.clear()
            self._again = False
            self.progress = EnrichProgress(running=True, phase="rules")
            self._thread = threading.Thread(target=self._run, name="enrich", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._again = False
            self._stop.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            self._run_once()
            with self._lock:
                if not self._again:
                    self.progress.running = False
                    return
                self._again = False
                self._stop.clear()  # a stop was meant for the run before (its cars were cleared)
                self.progress = EnrichProgress(running=True, phase="rules")

    def _run_once(self) -> None:
        p = self.progress
        try:
            self.run_rules()
            if self.manager.ready and not self._stop.is_set():
                self.run_pictures()
                self.run_ai()
                self.run_covers()
                if not self.manager.tier_still_right():
                    self.manager.start_download()
            p.phase = "done"
        except Exception as exc:  # shown in the UI
            if self._stop.is_set():  # the app quits and stops the AI server mid-answer
                log.info("enrichment stopped: %s", exc)
                p.phase = "done"
                return
            log.exception("enrichment failed")
            p.phase, p.message = "failed", str(exc)[:300]

    # --- 1. rules -----------------------------------------------------------------------

    def run_rules(self) -> int:
        """Warning signs of every active car, from its words and price; returns how many
        cars have one."""
        with self.sessions() as session:
            rows = session.execute(
                select(
                    Car.id, Car.title, Car.description, Car.make, Car.model, Car.year,
                    Car.mileage_km, Car.price, Car.price_currency, Car.crashed, Car.condition,
                    Car.warnings, Car.extra,
                ).where(Car.status == "active", Car.is_offer.is_(True))
            ).all()  # fmt: skip
            prices, kms = _similar_medians(rows)
            changes = []
            warned = 0
            for r in rows:
                found = cars.warnings_in_text(r.title, r.description)
                if _price_low(r, prices, kms):
                    found.append("price_low")
                found += (r.extra or {}).get("ai_warnings") or []
                warnings = list(dict.fromkeys(found))
                warned += bool(warnings)
                if warnings != (r.warnings or []):
                    changes.append({"car_id": r.id, "new": warnings})
            if changes:
                cars_t = table(Car)
                session.execute(
                    update(cars_t)
                    .where(cars_t.c.id == bindparam("car_id"))
                    .values(warnings=bindparam("new")),
                    changes,
                )
                session.commit()
        return warned

    # --- 2. added pictures --------------------------------------------------------------

    def run_pictures(self) -> int:
        """Writes out the text of pictures the user added; returns how many were read."""
        if self.uploads_dir is None or not self._vision():
            return 0
        with self.sessions() as session:
            ids = list(
                session.scalars(
                    select(CarImage.id).where(CarImage.kind == "upload", CarImage.text.is_(None))
                )
            )
        return self._each(ids, "pictures", self._read_picture, _picture_unread, vision=True)

    def _read_picture(self, session: Session, client: LlmClient, image_id: int) -> None:
        image = session.get(CarImage, image_id)
        if image is None or self.uploads_dir is None:
            return
        path = self.uploads_dir / image.url.rsplit("/", 1)[-1]
        session.commit()  # no transaction open while the model reads
        text = vision.transcribe(client, path.read_bytes()).data if path.exists() else ""
        image.text = text
        add_image_text(image.car)

    # --- 3. AI --------------------------------------------------------------------------

    def queue(self, session: Session) -> list[int]:
        """Car ids for the model, profile matches first, newest first. On slow PCs only those."""
        columns = [getattr(Car, name) for name in FIELDS]
        stmt = select(Car.id, Car.seller_kind, *columns).where(
            Car.status == "active",
            Car.is_offer.is_(True),
            or_(Car.ai_hash.is_(None), Car.ai_hash != Car.content_hash),
            func.length(Car.description) >= MIN_DESCRIPTION,
        )
        wanted = self.profile_cars(session)
        matching, rest = [], []
        for row in session.execute(stmt.order_by(Car.id.desc())):
            asks = row.seller_kind != "dealer" or any(v is None for v in row[2:])
            if asks:
                (matching if wanted is None or row.id in wanted else rest).append(row.id)
        speed = self.manager.measured().get(getattr(self.manager.active_model(), "id", ""))
        slow = speed is not None and speed.seconds_per_ad() > SLOW_SECONDS
        return matching if slow else matching + rest

    def run_ai(self) -> int:
        with self.sessions() as session:
            ids = self.queue(session)
        return self._each(ids, "ai", self._read_ad, _ad_read)

    def _read_ad(self, session: Session, client: LlmClient, car_id: int) -> None:
        car = session.get(Car, car_id)
        if car is None:
            return
        fields = missing_fields(car)
        untrusted = car.seller_kind != "dealer"
        text = ad_text(car)
        spec = None if self.manager.custom_server else self.manager.active_model()
        model = spec.id if spec else client.model
        key = _cache_key(model, text, fields, untrusted)
        cached = session.get(LlmCache, key)
        if cached is not None:
            result = validate(cached.result, text)
        elif not fields and not untrusted:
            result = Extraction()
        else:
            # No transaction stays open while the model reads (seconds): a search may write
            # meanwhile, and SQLite then refuses a write from an older read transaction.
            session.commit()
            result = extract(client, text, fields, untrusted)
            self.manager.record(result.timing)
            session.add(
                LlmCache(
                    key=key,
                    model=model[:40],
                    prompt_version=PROMPT_VERSION,
                    result=result.raw,
                    created_at=utcnow(),
                )
            )
        apply(car, result)

    # --- 4. cover photos ----------------------------------------------------------------

    def cover_queue(self, session: Session) -> list[int]:
        """Cars with sites' photos none of which was looked at, profile matches only, newest
        first."""
        photo = select(literal(1)).where(CarImage.car_id == Car.id)
        stmt = (
            select(Car.id)
            .where(
                Car.status == "active",
                Car.is_offer.is_(True),
                exists(photo),
                # the user's added picture (merged with a site's ad) is not looked at
                ~exists(photo.where(CarImage.kind.is_not(None), CarImage.kind != "upload")),
            )
            .order_by(Car.id.desc())
        )
        wanted = self.profile_cars(session)
        ids = (i for i in session.scalars(stmt) if wanted is None or i in wanted)
        return [i for _, i in zip(range(COVERS_PER_RUN), ids, strict=False)]

    def run_covers(self) -> int:
        spec = None if self.manager.custom_server else self.manager.active_model()
        if spec is not None and not spec.photos:
            return 0  # it would put a side view or a banner first as often as not
        with self.sessions() as session:
            ids = self.cover_queue(session)
        with self.http_factory() as http:
            look = lambda s, c, car_id: self._find_cover(s, c, car_id, http)  # noqa: E731
            return self._each(ids, "covers", look, _cover_failed, vision=True)

    def _find_cover(
        self, session: Session, client: LlmClient, car_id: int, http: httpx.Client
    ) -> None:
        """Looks at the car's first photos in order until one shows it from the front."""
        car = session.get(Car, car_id)
        photos = [i for i in car.images if i.kind is None][:COVER_PHOTOS] if car else []
        session.commit()
        for image in photos:
            data = _download(http, image.url)
            try:
                image.kind = vision.classify(client, data).data if data else "failed"
            except OSError:  # not a picture (an error page served as the photo)
                image.kind = "failed"
            session.commit()
            if image.kind == "front":
                return

    # --- the loop shared by the model's work ---------------------------------------------

    def _vision(self) -> bool:
        """The model can look at pictures (an own server is tried)."""
        spec = None if self.manager.custom_server else self.manager.active_model()
        return spec is None or spec.mmproj is not None

    def _each(
        self,
        ids: list[int],
        phase: str,
        handle: Callable[[Session, LlmClient, int], None],
        give_up: Callable[[Session, int], None],
        vision: bool = False,
    ) -> int:
        """Runs `handle` on each id with the model, a few at a time; waits while the PC is
        busy. An id the model fails on is given up (`give_up`), so no run stops on it."""
        p = self.progress
        p.phase, p.done, p.total = phase, 0, len(ids)
        done = 0
        while ids and not self._stop.is_set():
            chunk, ids = ids[:AI_CHUNK], ids[AI_CHUNK:]
            try:
                with self.manager.session(vision=vision) as client, self.sessions() as session:
                    p.phase, p.wait_reason = phase, ""
                    for item in chunk:
                        if self._stop.is_set():
                            break
                        try:
                            handle(session, client, item)
                        except ServerDown:
                            raise  # every next item would fail the same way
                        except LlmError as exc:
                            if vision and not isinstance(exc, BadAnswer):
                                log.warning("%s stopped: %s", phase, exc)  # e.g. no images
                                return done
                            log.warning("%s: item %s skipped: %s", phase, item, exc)
                            session.rollback()
                            give_up(session, item)
                        except Exception:  # skipped, or every run would stop on this item
                            log.exception("%s failed on item %s, skipped", phase, item)
                            session.rollback()
                            give_up(session, item)
                        session.commit()
                        done += 1
                        p.done += 1
            except NoModel:
                break
            except Busy as exc:
                ids = chunk + ids
                p.phase, p.wait_reason = "waiting", str(exc)
                self._stop.wait(self.wait_seconds)
        return done


def _similar(row) -> tuple | None:
    """Similar cars: same make, model ("Octavia" of "Octavia Combi") and year."""
    if not (row.make and row.model and row.year):
        return None
    return row.make, fold(row.model).split(" ")[0], row.year


def _price_low(row, prices: dict[tuple, float], kms: dict[tuple, float]) -> bool:
    """Far below similar cars, and the car is not more worn than they are."""
    key = _similar(row)
    median = prices.get(key) if key else None
    if median is None or median < PRICE_LOW_FROM or row.price_currency != "CZK":
        return False  # the medians are of CZK prices
    if row.price is None or row.price < cars.MIN_PRICE:
        return False  # "1 Kč", "0": no price given
    if row.crashed or row.condition == "damaged":
        return False  # cheap for a reason the ad states
    km, mileage = (kms.get(key) if key else None), row.mileage_km
    worn = km is not None and mileage is not None and mileage >= MIN_KM and mileage > WORN * km
    return row.price < PRICE_LOW * median and not worn


def _similar_medians(rows) -> tuple[dict[tuple, float], dict[tuple, float]]:
    """Median price (CZK) and median km of each group of similar cars."""
    prices: dict[tuple, list[int]] = defaultdict(list)
    kms: dict[tuple, list[int]] = defaultdict(list)
    for r in rows:
        if (key := _similar(r)) is None:
            continue
        if r.price and r.price >= cars.MIN_PRICE and r.price_currency == "CZK":
            prices[key].append(r.price)
        if r.mileage_km is not None and r.mileage_km >= MIN_KM:
            kms[key].append(r.mileage_km)

    def medians(groups: dict[tuple, list[int]]) -> dict[tuple, float]:
        return {k: statistics.median(v) for k, v in groups.items() if len(v) >= SIMILAR_MIN}

    return medians(prices), medians(kms)


def _download(http: httpx.Client, url: str) -> bytes | None:
    try:
        resp = http.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.info("photo %s not downloaded: %s", url, exc)
        return None
    if resp.status_code != 200 or len(resp.content) > MAX_PHOTO_BYTES:
        return None
    return resp.content


def _ad_read(session: Session, car_id: int) -> None:
    car = session.get(Car, car_id)
    if car is not None:
        car.ai_hash = car.content_hash


def _picture_unread(session: Session, image_id: int) -> None:
    image = session.get(CarImage, image_id)
    if image is not None:
        image.text = ""


def _cover_failed(session: Session, car_id: int) -> None:
    """Marks the first photo, so the car leaves the queue; it keeps its place in the list."""
    image = session.scalar(
        select(CarImage)
        .where(CarImage.car_id == car_id, CarImage.kind.is_(None))
        .order_by(CarImage.position)
        .limit(1)
    )
    if image is not None:
        image.kind = "failed"


def add_image_text(car: Car) -> bool:
    """Adds the text of the car's added pictures to its description, once. The car is then
    read by the AI again."""
    texts = [i.text for i in car.images if i.text]
    if not texts or IMAGE_TEXT_HEADER in (car.description or ""):
        return False
    pictures = IMAGE_TEXT_HEADER + "\n" + "\n\n".join(texts)
    car.description = "\n\n".join(x for x in (car.description, pictures) if x)
    extra = dict(car.extra or {})
    if extra.pop("title_from_picture", False):  # an ad the user added as a picture only
        car.title = next(line.strip() for t in texts for line in t.split("\n") if line.strip())[:90]
        car.extra = extra
    car.ai_hash = None
    _mark(car, ["description"], "image")
    return True


def ad_text(car: Car) -> str:
    head = [car.title, car.seller_name or "", car.place_text or ""]
    return "\n".join(x for x in head if x) + "\n\n" + (car.description or "")


def missing_fields(car: Car) -> tuple[str, ...]:
    """Fields (extract.FIELDS) the car does not have yet."""
    return tuple(name for name in FIELDS if getattr(car, name) is None)


def _cache_key(model: str, text: str, fields: tuple[str, ...], untrusted: bool) -> str:
    raw = f"{model}\n{PROMPT_VERSION}\n{untrusted}\n{','.join(fields)}\n{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mark(car: Car, fields: list[str], origin: str) -> None:
    extra = dict(car.extra or {})
    origins = dict(extra.get("origin") or {})
    origins.update(dict.fromkeys(fields, origin))
    extra["origin"] = origins
    car.extra = extra  # a new dict, so SQLAlchemy sees the change
    car.updated_at = utcnow()  # search facets are cached until a car changes


def apply(car: Car, result: Extraction) -> list[str]:
    """Fills the car's empty fields from a checked model answer; returns what was filled."""
    filled = [name for name, value in result.values.items() if getattr(car, name) is None]
    for name in filled:
        setattr(car, name, result.values[name])
    ai_warnings = list(result.warnings) + ([] if result.is_offer else ["not_offer"])
    extra = dict(car.extra or {})
    if ai_warnings or extra.get("ai_warnings"):
        old = set(extra.get("ai_warnings") or [])
        extra["ai_warnings"] = ai_warnings
        car.extra = extra
        kept = [w for w in car.warnings or [] if w not in old]
        car.warnings = list(dict.fromkeys(kept + ai_warnings))
        car.updated_at = utcnow()
    if filled:
        _mark(car, filled, "ai")
    car.ai_hash = car.content_hash
    return filled
