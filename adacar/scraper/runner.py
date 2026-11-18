"""Runs scraping in a background thread and reports progress to the UI."""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable, Collection, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from adacar.config import Profile
from adacar.core import ScrapeQuery, Source, utcnow
from adacar.db.models import Car, CarAd, Municipality, Run, SourceState
from adacar.geo import ruian
from adacar.geo.codebooks import Codebooks
from adacar.geo.geocode import geocode_locations, rebuild_municipalities
from adacar.geo.places import PlaceResolver
from adacar.geo.scope import build_query
from adacar.net.http import make_client
from adacar.net.polite import MAX_DELAY, PoliteFetcher, Stopped
from adacar.scraper.check import RECENT, AdCheck
from adacar.scraper.crawl import MAX_DETAILS, CrawlSource, crawl
from adacar.scraper.dedup import DedupIndex
from adacar.scraper.sources import LABELS, SOURCES
from adacar.scraper.store import CarStore
from adacar.search.saved import mark_all_seen
from adacar.search.service import Filters, batch_starts, start_batch

log = logging.getLogger(__name__)

# Profile fields that decide which cars a run finds: changing one starts over.
SCOPE_FIELDS = {"home_address", "travel_mode", "max_minutes", "makes", "max_price"}
BLOCKED_PAUSE = timedelta(hours=12)  # a source that refused us is left alone this long
SCOPE_ID = "search"  # SourceState row: what the stored cars were searched for
# Sites read at the same time. Each has its own polite pace, so a full check takes about as
# long as the slowest site instead of the sum of all.
PARALLEL_SOURCES = 4
# Seconds between requests (robots.txt Crawl-delay) from which a site is read beside the
# parallel slots: it mostly waits, so it would hold a slot while doing almost nothing.
SLOW_PACE = 5.0


@dataclass
class Progress:
    running: bool = False
    source_id: str | None = None
    # clear | check | listing | details | addresses (download, MB) | geocode | done | failed
    phase: str = ""
    cleared: bool = False  # this run deleted the cars found before
    after_id: int = 0  # the cars this run stores get higher ids
    # A run that keeps the cars found before checks their pages (scraper/check.py): those of
    # `checking` sources not seen since `checked_since` are not checked yet.
    checked_since: datetime | None = None
    checking: tuple[str, ...] = ()
    restarting: bool = False  # stops to start over (`start_over`)
    processed: int = 0
    message: str = ""
    results: dict[str, dict[str, int]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # source id -> reason
    active: dict[str, tuple[str, int]] = field(default_factory=dict)  # source -> phase, count

    def active_labels(self) -> list[tuple[str, str, int]]:
        return [(LABELS.get(s, s), phase, n) for s, (phase, n) in list(self.active.items())]

    @property
    def source_label(self) -> str:
        return LABELS.get(self.source_id or "", self.source_id or "")

    def totals(self) -> dict[str, int]:
        keys = ("new", "merged", "updated", "removed")
        return {k: sum(r.get(k, 0) for r in self.results.values()) for k in keys}

    def error_labels(self) -> str:
        return ", ".join(LABELS.get(s, s) for s in self.errors)


@dataclass
class RunHooks:
    """What other parts of the app do around a search (services.py connects them)."""

    before_clear: Callable[[], object] = lambda: None  # e.g. stop the AI reading ads
    after_source: Callable[[], object] = lambda: None  # e.g. check the new cars' sellers
    after_run: Callable[[], object] = lambda: None  # e.g. start the AI reading ads


class ScrapeRunner:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cache_dir: Path,
        client_factory=make_client,
        geocode: bool = True,
        profile: Callable[[], Profile | None] | None = None,
        sources: Sequence[Source] = SOURCES,
        fetcher_factory=PoliteFetcher,
        max_details: int = MAX_DETAILS,
        source_choices: Callable[[], dict[str, bool]] | None = None,
        hooks: RunHooks | None = None,
        kept_sources: Collection[str] = (),  # their cars stay when the found ones are cleared
    ) -> None:
        self.sessions = sessions
        self.cache_dir = cache_dir
        self.client_factory = client_factory
        self.geocode = geocode
        self.profile = profile or (lambda: None)
        self.source_choices = source_choices or dict  # source id -> user's on/off choice
        self.sources = sources
        self.fetcher_factory = fetcher_factory
        self.max_details = max_details
        self.hooks = hooks or RunHooks()
        self.kept_sources = tuple(kept_sources)
        self.progress = Progress()
        self._lock = threading.Lock()
        self._writes = threading.RLock()  # one database writer at a time (parallel sources)
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()  # every site stops at its next page
        self._again = False  # start over when this run stops
        self._scope: dict | None = None
        # The filters the user's list was last shown with: its ads are checked first.
        self.focus: Filters | None = None

    def start(self, fresh: bool = False) -> bool:
        """Start a run in the background. Returns False if one is already running.

        `fresh`, or a search scope other than the stored cars were found for: the run first
        deletes them and finds everything again.
        """
        with self._lock:
            if self.progress.running:
                return False
            scope = self._scope = self.scope()
            with self.sessions() as session:
                state = self._state(session, SCOPE_ID)
                before = (state.state or {}).get("scope")
                state.state = {"scope": scope, "started_at": utcnow().isoformat()}
                session.commit()
                clear = fresh or (before is not None and before != scope)
                kept = select(func.max(CarAd.car_id)).where(CarAd.source_id.in_(self.kept_sources))
                start_batch(session, (session.scalar(kept) or 0) if clear else None)
                after_id = batch_starts(session)[-1]
            self.progress = Progress(
                running=True,
                phase="clear" if clear else "start",
                after_id=after_id,
                checked_since=None if clear else utcnow() - RECENT,
            )
            self._stopping.clear()
            self._thread = threading.Thread(
                target=self._run_all, args=(clear,), name="scrape", daemon=True
            )
            self._thread.start()
            return True

    def start_over(self) -> None:
        """Starts a fresh run; a running one first stops at its sites' next requests."""
        with self._lock:
            if self.progress.running:
                self._again = self.progress.restarting = True
                self._stopping.set()
                return
        self.start(fresh=True)

    def scope_changed(self) -> None:
        with self._lock:
            if self.progress.running and self.scope() != self._scope:
                self._again = self.progress.restarting = True
                self._stopping.set()

    def scope(self) -> dict:
        """What decides which cars a run finds."""
        profile = self.profile()
        choices = self.source_choices()
        return {
            "profile": profile.model_dump(include=SCOPE_FIELDS) if profile else None,
            "sources": {s.id: choices.get(s.id, s.default_enabled) for s in SOURCES},
        }

    def last_start(self) -> datetime | None:
        """When the last run started (UTC), also one from an earlier session. Runs from
        before this was kept tell by their first source."""
        with self.sessions() as session:
            state = session.get(SourceState, SCOPE_ID)
            started = ((state and state.state) or {}).get("started_at")
            if started:
                return datetime.fromisoformat(started)
            return session.scalar(select(func.max(Run.started_at)))

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def stop(self, timeout: float = 10) -> bool:
        """Ends a running search at its sites' next requests; what it found so far stays.
        False when it still runs after `timeout` seconds (a download that cannot stop)."""
        with self._lock:
            self._again = False  # the app closes
        self._stopping.set()
        self.wait(timeout)
        return not (self._thread and self._thread.is_alive())

    def _run_all(self, clear: bool = False) -> None:
        """Every source on its own: one failing source does not stop the others."""
        p = self.progress
        ran: list[str] = []
        try:
            if clear:
                self.clear_cars()
                p.cleared = True
            with self.client_factory() as client:
                try:
                    self._read_sources(client, ran)
                finally:  # what was imported gets its places even when a step failed
                    if self.geocode:
                        self.run_geocode(client)
            if clear:
                with self.sessions() as session:
                    mark_all_seen(session)
            failed_all = bool(ran) and all(s in p.errors for s in ran)
            p.phase = "failed" if failed_all else "done"
            if failed_all:
                p.message = "; ".join(p.errors[s] for s in ran)
        except Exception as exc:  # reported in the UI
            log.exception("scrape failed")
            p.phase = "failed"
            p.message = str(exc)
        finally:
            with self._lock:
                p.running = False
                again, self._again = self._again, False
        if again:
            self.start(fresh=True)
            return
        if p.phase == "done" and not self._stopping.is_set():
            try:
                self.hooks.after_run()
            except Exception:
                log.exception("after-run step failed")

    def _read_sources(self, client: httpx.Client, ran: list[str]) -> None:
        """Reads every source switched on; `ran` collects the ids of those tried."""
        p = self.progress
        sources = self._enabled_of(CrawlSource)
        if not sources:
            return
        if self.geocode:
            # The place filters and the map need municipalities before the first cars.
            self._guard("ruian", lambda: self.run_geocode(client))
        fetcher = self.fetcher_factory(client)
        fetcher.stop = self._stopping
        # The ads the list shows are checked before new ones are looked for.
        self._guard("check", partial(self.run_check, sources, fetcher, everything=False))
        with self.sessions() as session:
            places = PlaceResolver(session)
            dedup = DedupIndex.load(session, self.sessions)
        query = build_query(places, self.profile())
        ran += [s.id for s in sources]
        groups, slow = self.schedule(sources, query, fetcher)
        args = (fetcher, places, dedup, query)
        with (
            ThreadPoolExecutor(len(slow) or 1, thread_name_prefix="slow") as beside,
            ThreadPoolExecutor(PARALLEL_SOURCES, thread_name_prefix="crawl") as pool,
        ):
            for group in slow:
                beside.submit(self._run_group, group, *args)
            for group in groups:
                pool.submit(self._run_group, group, *args)
        p.active.clear()
        if not self._stopping.is_set():
            self._guard("check", partial(self.run_check, sources, fetcher, everything=True))

    def schedule(
        self, sources: Sequence[CrawlSource], query: ScrapeQuery, fetcher: PoliteFetcher
    ) -> tuple[list[list[CrawlSource]], list[list[CrawlSource]]]:
        """(groups for the parallel slots, longest first; slow groups to read beside them).

        Sources on one host form a group read one after another: their requests take turns
        anyway, so side by side they would only hold several slots. A site whose robots.txt
        asks for a long pause between requests mostly waits, so it gets a thread of its own
        instead of a slot. The longest groups start first, so the slowest site does not run
        alone at the end."""
        groups: dict[str, list[CrawlSource]] = defaultdict(list)
        first_urls: dict[str, str] = {}
        for source in sources:
            url = first_url(source, query)
            host = urlsplit(url).netloc if url else source.id
            groups[host].append(source)
            if url and source.obey_robots:  # not read for a source enabled despite robots.txt
                first_urls[host] = url
        pace = site_paces(fetcher, first_urls)
        for host, group in groups.items():  # also a source's own pause (not in robots.txt)
            pace[host] = max(pace.get(host, fetcher.delay), *(s.delay or 0 for s in group))
        with self.sessions() as session:
            took = last_durations(session, [s.id for s in sources])

        def length(host: str) -> float:
            return sum(
                took.get(s.id) or estimate(s, self.max_details, pace[host]) for s in groups[host]
            )

        hosts = sorted(groups, key=length, reverse=True)
        slow = [groups[h] for h in hosts if pace[h] >= SLOW_PACE]
        return [groups[h] for h in hosts if pace[h] < SLOW_PACE], slow

    def _run_group(self, group: list[CrawlSource], *args) -> None:
        for source in group:
            self._guard(source.id, partial(self.run_crawl, source, *args))

    def _guard(self, source_id: str, step: Callable[[], object]) -> None:
        try:
            step()
        except Stopped:
            log.info("%s stopped: the app closes", source_id)
        except Exception as exc:  # also recorded in the runs table
            log.exception("%s failed", source_id)
            self.progress.errors[source_id] = str(exc)[:300]
        self.hooks.after_source()

    def _enabled_of[T: Source](self, kind: type[T]) -> list[T]:
        return [s for s in self.sources if isinstance(s, kind) and self._enabled(s)]

    def _enabled(self, source: Source) -> bool:
        """The user's choice (else the source's default), unless the site refused us lately."""
        if not self.source_choices().get(source.id, source.default_enabled):
            return False
        with self.sessions() as session:
            state = session.get(SourceState, source.id)
        until = ((state and state.state) or {}).get("blocked_until")
        if until and datetime.fromisoformat(until) > utcnow():
            log.info("%s paused until %s", source.id, until)
            return False
        return True

    @contextmanager
    def _record(
        self, session: Session, source_id: str, make_store: Callable[[], CarStore]
    ) -> Iterator[tuple[Run, CarStore]]:
        """The source's row in the runs table and its store: the row is finished on the way
        out, with the store's counts, or failed with the error the step raised."""
        with self._writes:
            run = Run(source_id=source_id, started_at=utcnow())
            session.add(run)
            session.commit()
            store = make_store()
            session.commit()  # ends the read transaction of loading the known cars
        try:
            yield run, store
        except Exception as exc:
            with self._writes:
                session.rollback()
                if isinstance(exc, Stopped):  # the app closes
                    run.status = "stopped"
                else:
                    run.status, run.error = "failed", str(exc)[:2000]
            raise
        finally:
            with self._writes:
                run.finished_at = utcnow()
                run.new_count = store.stats.new
                run.updated_count = store.stats.updated
                run.removed_count = store.stats.expired
                session.add(run)
                session.commit()
            self.progress.results[source_id] = store.stats.counts()
            self.progress.active.pop(source_id, None)

    def run_crawl(
        self,
        source: CrawlSource,
        fetcher: PoliteFetcher,
        places: PlaceResolver,
        dedup: DedupIndex,
        query: ScrapeQuery,
    ) -> Run:
        p = self.progress
        p.source_id, p.phase, p.processed = source.id, "listing", 0
        p.active[source.id] = ("listing", 0)

        def progress(phase: str, n: int) -> None:
            p.phase, p.processed = phase, n
            p.active[source.id] = (phase, n)

        def make_store() -> CarStore:
            return CarStore(session, source.id, dedup=dedup, write_lock=self._writes)

        with (
            self.sessions() as session,
            self._record(session, source.id, make_store) as (run, store),
        ):
            unfinished = self._unfinished(source.id)
            try:
                result = crawl(
                    source, fetcher, store, places, query, self.max_details, progress, unfinished
                )
            finally:  # also when the app closes mid-walk
                self._save_unfinished(source.id, unfinished)
            with self._writes:
                state = self._state(session, source.id)
                state.last_run_at = utcnow()
                run.status = "ok" if result.complete else "partial"
                if result.unreachable:
                    error = f"site not answering: {result.unreachable}"
                    run.error = p.errors[source.id] = error
                if result.blocked:
                    run.status, run.error = "blocked", result.blocked
                    until = (utcnow() + BLOCKED_PAUSE).isoformat()
                    state.state = (state.state or {}) | {"blocked_until": until}
                    p.errors[source.id] = result.blocked
            return run

    def run_check(
        self, sources: Sequence[CrawlSource], fetcher: PoliteFetcher, everything: bool
    ) -> None:
        """Checks the pages of the portals' ads found before (not after a clear): those the
        list shows, and with `everything` the rest too (scraper/check.py)."""
        p = self.progress
        if p.checked_since is None or not sources:
            return
        p.checking = tuple(s.id for s in sources)
        p.source_id, p.phase, p.processed = None, "check", 0
        checked: dict[str, int] = {}

        def progress(source_id: str, n: int) -> None:
            checked[source_id] = n
            p.processed = sum(checked.values())

        since, focus = p.checked_since, lambda: self.focus  # the filters of the moment
        check = AdCheck(self.sessions, fetcher, self._writes, since, focus, progress)
        expired = check.run(sources, everything)
        counts = p.results.setdefault("check", {})
        counts["removed"] = counts.get("removed", 0) + expired

    def run_geocode(self, client: httpx.Client) -> None:
        """Coordinates for new locations. Failure here never loses the cars found."""
        p = self.progress
        p.source_id, p.phase, p.processed = None, "geocode", 0
        try:
            directory = self.cache_dir / "ruian"
            file = directory / ruian.FILE_NAME
            before = file.stat().st_mtime_ns if file.exists() else None
            # A first download is ~63 MB from a server that can be slow: the user sees it move.
            p.phase = "addresses"
            path = ruian.download(
                client, directory, progress=lambda n: setattr(p, "processed", n >> 20)
            )
            p.phase, p.processed = "geocode", 0
            changed = before != path.stat().st_mtime_ns
            addresses = ruian.RuianAddresses(path)
            try:
                with self.sessions() as session:
                    empty = not session.scalar(select(func.count(Municipality.code)))
                    if changed or empty:
                        codebooks = Codebooks.fetch(client, self.cache_dir / "codebooks")
                        rebuild_municipalities(
                            session,
                            addresses,
                            codebooks.okres_map(),
                            progress=lambda n: setattr(p, "processed", n),
                        )
                    geocode_locations(session, addresses)
            finally:
                addresses.close()
        except Exception as exc:
            log.exception("geocoding failed")
            p.message = f"geocoding: {exc}"

    def clear_cars(self) -> None:
        """Deletes the found cars, so they are all found again. Cars of `kept_sources`,
        pauses, daily limits and cached downloads stay."""
        self.hooks.before_clear()
        with self.sessions() as session:
            kept = select(CarAd.car_id).where(CarAd.source_id.in_(self.kept_sources))
            # Their ads and photos go with them (ON DELETE CASCADE).
            session.execute(delete(Car).where(Car.id.not_in(kept)))
            session.commit()

    def _unfinished(self, source_id: str) -> set[str]:
        """Listings of the source that no run has read to the end yet (crawl.py)."""
        with self.sessions() as session:
            state = session.get(SourceState, source_id)
            return set(((state and state.state) or {}).get("unfinished", []))

    def _save_unfinished(self, source_id: str, unfinished: set[str]) -> None:
        with self._writes, self.sessions() as session:
            state = self._state(session, source_id)
            state.state = (state.state or {}) | {"unfinished": sorted(unfinished)}
            session.commit()

    @staticmethod
    def _state(session: Session, source_id: str) -> SourceState:
        """The source's state row (created when missing)."""
        state = session.get(SourceState, source_id)
        if state is None:
            state = SourceState(source_id=source_id)
            session.add(state)
        return state


def first_url(source: CrawlSource, query: ScrapeQuery) -> str | None:
    """The source's first listing page (None when it lists nothing for this query). Only a
    hint for planning: a source that cannot tell is still read, and its crawl reports why."""
    try:
        return source.listings(query)[0][1](1)
    except Exception:
        log.warning("%s: no first listing page", source.id, exc_info=True)
        return None


def site_paces(fetcher: PoliteFetcher, urls: dict[str, str]) -> dict[str, float]:
    """Host -> seconds between its requests, after reading every site's robots.txt at once
    (a site slow to answer it does not hold up the others)."""

    def pace(url: str) -> float:
        try:
            crawl_delay = fetcher.robots(url).crawl_delay or 0
        except Exception:  # refused or unreachable: the crawl itself reports it
            crawl_delay = 0
        return max(fetcher.delay, min(crawl_delay, MAX_DELAY))

    with ThreadPoolExecutor(8, thread_name_prefix="robots") as pool:
        return dict(zip(urls, pool.map(pace, urls.values()), strict=True))


def last_durations(session: Session, source_ids: Sequence[str]) -> dict[str, float]:
    """Seconds the last finished run of each source took."""
    rows = session.execute(
        select(Run.source_id, Run.started_at, Run.finished_at)
        .where(
            Run.source_id.in_(source_ids),
            Run.status.in_(("ok", "partial")),
            Run.finished_at.is_not(None),
        )
        .order_by(Run.started_at)
    )
    return {source_id: (end - start).total_seconds() for source_id, start, end in rows if end}


def estimate(source: CrawlSource, max_details: int, pace: float) -> float:
    """Seconds a source without a finished run will take: its detail pages at its pace."""
    return source.detail_limit(max_details) * max(pace, source.delay or 0)
