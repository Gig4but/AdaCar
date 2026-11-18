"""Portals read page by page: listings → leads → detail pages (PLAN §4.1).

A source adapter only knows its own pages: listing URLs, how to read a listing card and a
detail page. `crawl` does the rest the same way for every portal:
- walks each listing (a region, or a search near home) until the last page;
- cars it already knows are only marked as still offered;
- reads detail pages of new cars (at most `max_details` per run, the rest stay card-only
  and are completed on later runs);
- reads detail pages again when they are older than `refresh_after_days` (after new ones);
- expires cars the source no longer lists: after a complete pass over a region's listing
  (only in that region; everywhere for a nationwide listing), or, for
  searches that show only the newest ads, when unseen for some days.

A source may split a region's listing into several (e.g. by price, when the portal pages
only so deep): a region counts as read to the end once all of its listings are.

A newest-first listing stops at the first page with nothing new, so a walk cut short (the
app closing, the site not answering) would leave its older ads unread for good: such a
listing is in `unfinished` until a later run has walked it to its end or its `max_pages`.
"""

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from functools import partial
from operator import attrgetter
from urllib.parse import urlsplit, urlunsplit

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from adacar.core import ParsedCar, ScrapeQuery, SourceKind, utcnow
from adacar.geo.places import PlaceResolver
from adacar.net.polite import PoliteFetcher, SourceBlocked
from adacar.normalize.text import clean_spaces
from adacar.scraper.store import CarStore

log = logging.getLogger(__name__)

# Region names in listing URLs (sauto.cz; most portals write them alike).
KRAJ_SLUGS = {
    "Hlavní město Praha": "hlavni-mesto-praha",
    "Středočeský kraj": "stredocesky-kraj",
    "Jihočeský kraj": "jihocesky-kraj",
    "Plzeňský kraj": "plzensky-kraj",
    "Karlovarský kraj": "karlovarsky-kraj",
    "Ústecký kraj": "ustecky-kraj",
    "Liberecký kraj": "liberecky-kraj",
    "Královéhradecký kraj": "kralovehradecky-kraj",
    "Pardubický kraj": "pardubicky-kraj",
    "Kraj Vysočina": "kraj-vysocina",
    "Jihomoravský kraj": "jihomoravsky-kraj",
    "Olomoucký kraj": "olomoucky-kraj",
    "Moravskoslezský kraj": "moravskoslezsky-kraj",
    "Zlínský kraj": "zlinsky-kraj",
}
MAX_DETAILS = 300  # detail pages per source and run (~8 min at the polite pace)
MAX_FAILED_DETAILS = 10  # in a row: the page layout probably changed
# Pages in a row that still fail after the fetcher's retries (dropped connections, timeouts,
# server errors): the site is down, so the source stops; a single one is skipped.
MAX_UNREACHABLE = 3
MAX_RECHECKS = 20  # pages of long-unseen ads read again per run (`recheck_after_days`)
REFRESH_DAYS = 30  # a listed ad's detail page is read again after this many days


# (region, page number -> URL); region None: the listing is not one region's.
Listing = tuple[str | None, Callable[[int], str]]


@dataclass
class Lead:
    external_id: str
    url: str  # the page shown to the user
    fetch_url: str | None = None  # detail page to read; None = the card is all we get
    card: ParsedCar | None = None
    modified: datetime | None = None  # the ad's last change: read again when newer than our copy
    priority: int = 0  # lower first when detail pages are read


@dataclass
class ListingPage:
    leads: list[Lead]
    has_next: bool
    # The site said it has nothing here (an API's total of 0): an empty page that is an
    # answer, not an error page or a new layout.
    none_listed: bool = False


class CrawlSource:
    id: str
    label: str
    max_pages: int = 300  # per listing
    region_slugs: dict[str, str] = KRAJ_SLUGS  # region -> its name in the listing URLs
    default_enabled: bool = True  # False: only after the user opts in
    obey_robots: bool = True  # False only for a source the user enabled despite robots.txt
    delay: float | None = None  # seconds between requests, when slower than the default
    max_leads: int | None = None  # cards per run
    max_details: int | None = None  # detail pages per run, when lower than the run's limit
    newest_first: bool = False  # stop a listing at the first page with nothing new
    empty_last_page: bool = False  # a "load more" listing ends with an empty page
    expire_after_days: int | None = None  # None: expire after a complete pass instead
    nationwide: bool = False  # the listings cover the whole country, not regions
    kind: SourceKind = "portal"
    needs_consent = False
    # Newest-first listings that stop early: an ad unseen this many days may still be listed
    # further back, so its page is read again (a few per run) and only a gone page expires it.
    recheck_after_days: int | None = None
    # Listed ads whose detail page is older are read again (text and pay change), after the
    # new ones. Not for leads with `modified`: that tells when they changed.
    refresh_after_days: int | None = REFRESH_DAYS

    def detail_limit(self, max_details: int) -> int:
        return min(max_details, self.max_details or max_details)

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        """The listings to walk. Default: one per region in scope."""
        return [(kraj, partial(self.listing_url, self.region_slugs[kraj])) for kraj in query.kraje]

    def listing_url(self, region_slug: str, page: int) -> str:
        raise NotImplementedError

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        raise NotImplementedError

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedCar | None:
        raise NotImplementedError

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        """False for a lead outside the user's regions (when its URL or card tells)."""
        return True

    def gone(self, html: str) -> bool:
        """The ad's page says the ad is over (portals that keep such pages up, HTTP 200)."""
        return False

    def check_url(self, url: str) -> str:
        """Where to check that the ad shown at `url` is still offered (`gone` reads it)."""
        return url

    def prepare(self, query: ScrapeQuery, places: PlaceResolver) -> None:
        """Called once before a run, e.g. to learn which towns are near home."""


@dataclass
class CrawlResult:
    complete: bool = False
    pages: int = 0
    details: int = 0
    failed_details: int = 0
    blocked: str | None = None
    unreachable: str | None = None  # why the source stopped early: the site did not answer


def clean_url(url: str) -> str:
    """Without tracking parameters (?rps=, ?search_id=) and fragments."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def other_page(asked: str, got: str) -> bool:
    """A redirect led away from the page asked for (page 87 to page 1, to the home page), not
    just to another spelling of it (https, www, a trailing slash)."""
    a, b = urlsplit(asked), urlsplit(got)
    return (a.path.rstrip("/"), a.query) != (b.path.rstrip("/"), b.query)


def node_value(node: LexborNode | None) -> str | None:
    """First text of a node; cards often repeat a value for the mobile and desktop layouts."""
    if node is None:
        return None
    for piece in node.text(separator="\n").split("\n"):
        if piece := clean_spaces(piece):
            return piece
    return None


def has_page_link(tree: LexborHTMLParser, param: str, page: int) -> bool:
    link = re.compile(rf"[?&]{re.escape(param)}={page}(?![0-9])")
    return any(
        link.search((a.attributes.get("href") or "").replace("&amp;", "&"))
        for a in tree.css("a[href]")
    )


def merge_card(car: ParsedCar, card: ParsedCar | None) -> ParsedCar:
    """Detail page values win; the card fills what the detail page lacks (e.g. photos)."""
    if card is None:
        return car
    empty = (None, "", [], {})
    for f in fields(ParsedCar):
        if f.name in ("external_id", "url", "price_currency"):
            continue
        if getattr(car, f.name) in empty and getattr(card, f.name) not in empty:
            setattr(car, f.name, getattr(card, f.name))
            if f.name == "price":
                car.price_currency = card.price_currency
    return car


_PRIORITY = attrgetter("priority")


def crawl(
    source: CrawlSource,
    fetcher: PoliteFetcher,
    store: CarStore,
    places: PlaceResolver,
    query: ScrapeQuery,
    max_details: int = MAX_DETAILS,
    progress: Callable[[str, int], None] = lambda phase, n: None,
    unfinished: set[str] | None = None,
) -> CrawlResult:
    """One run of a source, as the module docstring tells. `unfinished`: the first pages of
    listings not yet read to the end, updated as they are walked."""
    crawl = _Crawl(source, fetcher, store, places, query, max_details, progress)
    if unfinished is not None:
        crawl.unfinished = unfinished
    return crawl.run()


class _Crawl:
    """The state of one `crawl`: the leads seen so far, sorted by what their pages need."""

    def __init__(
        self,
        source: CrawlSource,
        fetcher: PoliteFetcher,
        store: CarStore,
        places: PlaceResolver,
        query: ScrapeQuery,
        max_details: int,
        progress: Callable[[str, int], None],
    ) -> None:
        self.source, self.fetcher, self.store = source, fetcher, store
        self.places, self.query, self.progress = places, query, progress
        self.get = partial(fetcher.get, obey_robots=source.obey_robots, min_delay=source.delay)
        self.limit = source.detail_limit(max_details)
        days = source.refresh_after_days
        self.refresh_before = utcnow() - timedelta(days=days) if days is not None else None
        self.result = CrawlResult()
        self.seen: set[str] = set()
        self.new: list[Lead] = []
        self.incomplete: list[Lead] = []  # known, but only from a card so far
        self.changed: list[Lead] = []  # the source says the ad changed since it was read
        self.stale: list[Lead] = []  # known, with a detail page read long ago
        self.done_kraje: list[str] = []  # regions whose listing was read to the end
        self.failures = 0  # pages in a row the site did not answer
        self.unfinished: set[str] = set()  # see the module docstring

    def run(self) -> CrawlResult:
        source, result = self.source, self.result
        source.prepare(self.query, self.places)
        try:
            whole = True  # every listing was read to the end
            listings = source.listings(self.query)
            left = Counter(kraj for kraj, _ in listings if kraj is not None)  # unread, per region
            for kraj, listing_url in listings:
                if result.unreachable:
                    whole = False
                    break
                complete = self.walk(listing_url)
                whole = whole and complete
                if complete and kraj is not None:
                    left[kraj] -= 1
                    if not left[kraj]:
                        self.done_kraje.append(kraj)
            self.read_details()
            result.complete = whole and bool(self.seen) and not result.unreachable
        except SourceBlocked as exc:
            result.blocked = str(exc)
            log.warning("%s blocked: %s", source.id, exc)
        self.expire()
        if source.recheck_after_days is not None and not (
            result.complete or result.unreachable or result.blocked
        ):
            recheck_unseen(source, self.fetcher, self.store, result)
        return result

    def full(self) -> bool:
        return self.source.max_leads is not None and len(self.seen) >= self.source.max_leads

    def unanswered(self, url: str, exc: httpx.HTTPError) -> bool:
        """Notes a page the site did not answer; True once the site looks down. An error
        page (HTTP 5xx after the retries) is an answer: only that page is skipped."""
        log.warning("%s: no page at %s: %s", self.source.id, url, exc)
        self.failures = self.failures + 1 if isinstance(exc, httpx.TransportError) else 0
        if self.failures >= MAX_UNREACHABLE:
            self.result.unreachable = str(exc) or type(exc).__name__
        return self.result.unreachable is not None

    def walk(self, listing_url: Callable[[int], str]) -> bool:
        """Reads a listing page by page; True when it was read to its end."""
        key = listing_url(1)
        resume = key in self.unfinished
        self.unfinished.add(key)  # stays when the walk is cut short, also by an exception
        ended, complete = self._walk(listing_url, resume)
        if ended:
            self.unfinished.discard(key)
        return complete

    def _walk(self, listing_url: Callable[[int], str], resume: bool) -> tuple[bool, bool]:
        """(the walk ended where the listing or its new ads end, it was read to its end).
        `resume`: an earlier walk was cut short, so known pages do not end this one."""
        source = self.source
        page = 1
        while True:
            if page > source.max_pages or self.full():
                return True, False  # as far as the source reads, by its own choice
            url = listing_url(page)
            try:
                resp = self.get(url)
            except httpx.HTTPError as exc:
                self.unanswered(url, exc)
                return False, False  # the rest of this listing waits for the next run
            self.failures = 0
            if resp is None:
                return True, page > 1  # robots.txt or gone
            if resp.history and other_page(str(resp.history[0].url), str(resp.url)):
                return True, False  # the listing ends before its last page
            self.result.pages += 1
            listing = source.parse_listing(resp.text, page, self.places)
            fresh = 0
            with self.store.writing():
                for lead in listing.leads:
                    fresh += self.sort(lead)
            self.progress("listing", len(self.seen))
            # No ads on a page: an error page or a new layout, not an empty region.
            empty = (
                not listing.leads
                and not listing.none_listed
                and source.expire_after_days is None
                and not (source.empty_last_page and page > 1)
            )
            if not listing.has_next or not listing.leads:
                return True, not empty
            if source.newest_first and not fresh and not resume:
                return True, False  # the rest is known already
            if (
                source.newest_first
                and len(self.new) >= self.limit
                and not any(x.card for x in self.new)
            ):
                return True, False  # enough to read this run; listings give no cards to keep
            page += 1

    def sort(self, lead: Lead) -> bool:
        """Notes a listed lead where it belongs; True for an ad not known before."""
        store, external_id = self.store, lead.external_id
        if external_id in self.seen or self.full():
            return False
        self.seen.add(external_id)
        if not store.knows(external_id):
            if not self.source.wanted(lead, self.query, self.places):
                return False
            self.new.append(lead)
            if lead.card is not None:  # listed at once, not after the whole site was read
                store.add(lead.card, detail=False)
            return True
        if lead.card is not None and not store.has_detail(external_id):
            store.add(lead.card, detail=False)  # updated when it changed
        else:
            store.touch(external_id)
        if not lead.fetch_url:
            return False
        if not store.has_detail(external_id):
            self.incomplete.append(lead)
        elif lead.modified:
            if store.detail_older_than(external_id, lead.modified):
                self.changed.append(lead)
        elif self.refresh_before and store.detail_older_than(external_id, self.refresh_before):
            self.stale.append(lead)
        return False

    def read_details(self) -> None:
        """Detail pages of new ads first, then changed ones, then the longest unread."""
        result, store = self.result, self.store
        unread = sorted((x for x in self.new + self.incomplete if x.fetch_url), key=_PRIORITY)
        self.stale.sort(key=lambda lead: store.detail_read_at(lead.external_id))
        queue = (unread + sorted(self.changed, key=_PRIORITY) + self.stale)[: self.limit]
        failed_in_row = 0
        for lead in [] if result.unreachable else queue:
            url = lead.fetch_url
            assert url is not None  # queued for its detail page
            try:
                resp = self.get(url)
                self.failures = 0
            except httpx.HTTPError as exc:
                resp = None  # a dropped connection loses this ad, not the run
                if self.unanswered(url, exc):
                    result.failed_details += 1
                    return
            try:
                car = self.source.parse_detail(resp.text, lead, self.places) if resp else None
            except Exception:
                log.exception("%s: cannot read %s", self.source.id, url)
                car = None
            if car is None:
                result.failed_details += 1
                failed_in_row += 1
                if failed_in_row >= MAX_FAILED_DETAILS:
                    log.error(
                        "%s: %d detail pages in a row unreadable", self.source.id, failed_in_row
                    )
                    return
                continue
            failed_in_row = 0
            car.external_id, car.url = lead.external_id, lead.url
            with store.writing():
                store.add(merge_card(car, lead.card), detail=True)
            result.details += 1
            self.progress("details", result.details)

    def expire(self) -> None:
        """Ads the source no longer lists are gone: after a listing read to its end (in its
        region only), or when unseen for `expire_after_days`."""
        source, result, store = self.source, self.result, self.store
        with store.lock:
            if result.unreachable:
                return  # ads unseen because the site was down are not gone
            if source.expire_after_days is not None:
                store.expire_unseen(source.expire_after_days)
            elif result.complete or self.done_kraje:
                # A region read to the end counts even when another one's listing broke off.
                kraje: list[str] | None = self.done_kraje
                if result.complete:
                    kraje = None if source.nationwide else self.query.kraje
                if store.mostly_unseen(kraje):
                    result.complete = False
                    log.warning("%s: most known ads not listed any more: none expired", source.id)
                else:
                    store.expire_missing(kraje)


def recheck_unseen(
    source: CrawlSource, fetcher: PoliteFetcher, store: CarStore, result: CrawlResult
) -> None:
    """Reads the pages of ads unseen for `recheck_after_days` again, the longest unseen
    first: a page that is gone (404/410) or says so expires its ad, a live one counts as seen."""
    gone, live = [], []
    for external_id, url in store.unseen_for(source.recheck_after_days or 0, MAX_RECHECKS):
        try:
            over = page_gone(source, fetcher, url)
        except SourceBlocked as exc:
            result.blocked = str(exc)
            break
        except httpx.HTTPError as exc:
            log.warning("%s: cannot check %s: %s", source.id, url, exc)
            break
        if over is not None:
            (gone if over else live).append(external_id)
    with store.writing():
        for external_id in live:
            store.touch(external_id)
    with store.lock:
        store.expire_gone(gone)


def page_gone(source: CrawlSource, fetcher: PoliteFetcher, url: str) -> bool | None:
    """The ad's own page is gone (404/410) or says the ad is over; None when robots.txt
    does not let us read it (so it is not known to be gone). Raises what the fetcher raises."""
    url = source.check_url(url)
    if source.obey_robots and not fetcher.allowed(url):
        return None
    resp = fetcher.get(url, obey_robots=source.obey_robots, min_delay=source.delay)
    return resp is None or source.gone(resp.text)
