import time
from datetime import timedelta
from functools import partial
from urllib.parse import urljoin

import httpx
import pytest
from selectolax.lexbor import LexborHTMLParser
from sqlalchemy import func, select, update

from adacar.config import Profile
from adacar.core import ParsedCar, ScrapeQuery, utcnow
from adacar.db.models import Car, CarAd, Location, Run, SourceState
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.geo.scope import build_query
from adacar.net.polite import PoliteFetcher
from adacar.scraper.crawl import CrawlSource, Lead, ListingPage, crawl, has_page_link
from adacar.scraper.probe import probe
from adacar.scraper.runner import RunHooks, ScrapeRunner
from adacar.scraper.store import CarStore
from adacar.search.service import Filters

PRAHA = Profile(home_address="Praha", max_minutes=10, travel_mode="walk")
DETAIL = """<html><body><p>Prodám po servisu, garážováno, nebourané.</p>
<address data-street="Sulická 5">Praha</address></body></html>"""


class Portal(CrawlSource):
    """A car portal read by region; dealers' cards link to their own sites (card only)."""

    label = "Portál"

    def __init__(self, id="portal", host="www.portal.test", max_details=None):
        self.id, self.base, self.max_details = id, f"https://{host}", max_details

    def listing_url(self, region_slug, page):
        return f"{self.base}/{region_slug}/" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html, page, places):
        tree = LexborHTMLParser(html)
        leads = []
        for a in tree.css("a.ad"):
            url = urljoin(self.base, a.attributes["href"])
            place = a.attributes["data-place"]
            card = ParsedCar(a.id, a.text(), url=url, make="skoda", model="Octavia",
                             price=int(a.attributes["data-price"]), place_text=place,
                             location=places.resolve(place),
                             images=[f"https://img.test/{a.id}.jpg"])  # fmt: skip
            leads.append(Lead(a.id, url, url if url.startswith(self.base) else None, card))
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html, lead, places):
        tree = LexborHTMLParser(html)
        text, place = tree.css_first("p"), tree.css_first("address")
        if text is None or place is None:
            return None
        location = places.resolve(place.text(), street=place.attributes["data-street"])
        return ParsedCar(lead.external_id, "", description=text.text(), location=location)


SOURCE = Portal()


def card(ad, title=None, price=250_000, place="Praha", href=None):
    return (f'<a class="ad" id="{ad}" href="{href or "/inzerat/" + ad}" data-price="{price}" '
            f'data-place="{place}">{title or "Škoda Octavia " + ad}</a>')  # fmt: skip


def page(*cards, more=False):
    return "<html><body>" + "".join(cards) + ('<a href="?page=2">2</a>' if more else "")


# 24 ads of the portal and 6 of dealers, then one on page 2.
FIRST_PAGE = page(
    *(card(f"{i:02}", href=None if i % 5 else f"https://dealer.test/auto/{i}") for i in range(30)),
    more=True,
)
LAST_PAGE = page(card("extra-1", "Škoda Fabia"))
OTHER_LAST_PAGE = LAST_PAGE.replace("extra-1", "extra-2")


class Site:
    """The portal as a mock: listing pages of Praha, detail pages, other answers by URL."""

    def __init__(self) -> None:
        self.first_page = FIRST_PAGE
        self.last_page = LAST_PAGE
        self.status = 200
        self.detail_status = 200
        self.drop_details = 0  # detail requests answered by a dropped connection
        self.requests: list[str] = []
        self.answers: dict[str, httpx.Response] = {}  # by URL: other regions, redirects

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if answer := self.answers.get(str(request.url)):
            return httpx.Response(answer.status_code, headers=answer.headers,
                                  content=answer.content)  # fmt: skip
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if self.status != 200:
            return httpx.Response(self.status)
        if path == "/hlavni-mesto-praha/":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, text=self.last_page)
            return httpx.Response(200, text=self.first_page)
        if path.startswith("/inzerat/"):
            if self.drop_details:
                self.drop_details -= 1
                raise httpx.RemoteProtocolError("Server disconnected", request=request)
            if self.detail_status != 200:
                return httpx.Response(self.detail_status)
            return httpx.Response(200, text=DETAIL)
        return httpx.Response(404)

    def details(self) -> int:
        return sum("/inzerat/" in url for url in self.requests)


@pytest.fixture
def sessions(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
    yield sessions
    engine.dispose()


def run(
    sessions,
    tmp_path,
    site: Site,
    max_details: int = 5,
    home: Profile = PRAHA,
    sources=(SOURCE,),
    choices=None,
) -> ScrapeRunner:
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: home,
        sources=sources,
        fetcher_factory=lambda client: PoliteFetcher(client, delay=0, sleep=lambda s: None),
        max_details=max_details,
        source_choices=lambda: choices or {},
    )
    site.requests.clear()
    runner.start()
    runner.wait(60)
    return runner


def test_cars_are_listed_while_the_site_is_still_read(sessions, tmp_path):
    listed = []

    class Watched(Site):
        def __call__(self, request):
            if request.url.params.get("page") == "2":
                with sessions() as s:
                    listed.append(s.scalar(select(func.count()).select_from(Car)))
            return super().__call__(request)

    run(sessions, tmp_path, Watched())
    assert listed == [30]  # page 1's cards before page 2 is asked for


def test_crawl_reads_cards_then_details_then_expires(sessions, tmp_path):
    site = Site()
    runner = run(sessions, tmp_path, site)
    p = runner.progress
    assert p.phase == "done", (p.message, p.errors)
    assert p.results["portal"]["new"] == 31  # 30 cards on page 1, one on page 2
    assert site.details() == 5  # the per-run cap
    assert not any("dealer.test" in url for url in site.requests)  # dealers' sites: card only
    with sessions() as s:
        ads = s.scalars(select(CarAd)).all()
        assert sum(ad.detail_at is not None for ad in ads) == 5
        dealer = s.scalar(select(Car).join(CarAd).where(CarAd.url.like("%dealer.test%")))
        assert dealer.title == "Škoda Octavia 00" and dealer.price == 250_000
        assert dealer.location.key == "obec:554782"
        assert dealer.ads[0].url == "https://dealer.test/auto/0"
        detailed = s.scalar(select(Car).join(CarAd).where(CarAd.detail_at.is_not(None)))
        assert detailed.description.startswith("Prodám") and detailed.make == "skoda"
        assert detailed.location.key == "addr:554782:sulicka:5"  # from the detail page
        assert s.scalar(select(Run.status)) == "ok"

    # Next run: nothing new, the next five card-only cars get their details.
    runner = run(sessions, tmp_path, site)
    assert runner.progress.results["portal"] == {
        "new": 0,
        "merged": 0,
        "updated": 5,
        "removed": 0,
    }
    assert site.details() == 5
    with sessions() as s:
        assert s.scalar(select(func.count()).where(CarAd.detail_at.is_not(None))) == 10

    # The ad on page 2 is gone, another one took its place.
    site.last_page = OTHER_LAST_PAGE
    runner = run(sessions, tmp_path, site, max_details=0)
    assert runner.progress.results["portal"]["removed"] == 1
    with sessions() as s:
        gone = s.scalar(select(Car).join(CarAd).where(CarAd.external_id == "extra-1"))
        assert gone.status == "expired"


def test_empty_pass_and_other_regions_do_not_expire(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=0)
    with sessions() as s:
        car = s.scalar(select(Car).join(CarAd).where(CarAd.external_id == "extra-1"))
        car.location = Location(key="obec:554791", precision="municipality", kraj="Plzeňský kraj")
        s.commit()

    # Only Praha was crawled, so the car placed in Plzeň is not known to be gone.
    site.last_page = OTHER_LAST_PAGE
    runner = run(sessions, tmp_path, site, max_details=0)
    assert runner.progress.results["portal"]["removed"] == 0

    # A listing without a single ad (changed layout) proves nothing either.
    site.first_page = "<html><body></body></html>"
    runner = run(sessions, tmp_path, site, max_details=0)
    assert runner.progress.results["portal"]["removed"] == 0
    with sessions() as s:
        assert s.scalar(select(func.count()).where(Car.status == "expired")) == 0
        assert s.scalar(select(Run.status).order_by(Run.id.desc()).limit(1)) == "partial"


def test_a_listing_that_breaks_off_expires_nothing(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=0)

    # Page 1 promised a page 2, which came empty (an error page) or redirected to page 1.
    site.last_page = "<html><body></body></html>"
    run(sessions, tmp_path, site, max_details=0)
    page_2 = "https://www.portal.test/hlavni-mesto-praha/?page=2"
    site.answers[page_2] = httpx.Response(302, headers={"Location": "/hlavni-mesto-praha/"})
    run(sessions, tmp_path, site, max_details=0)
    assert page_2 in site.requests
    assert site.requests[-1] == "https://www.portal.test/hlavni-mesto-praha/"
    with sessions() as s:
        assert s.scalar(select(func.count()).where(Car.status == "expired")) == 0
        assert s.scalars(select(Run.status).order_by(Run.id)).all() == ["ok", "partial", "partial"]


def test_an_empty_region_expires_nothing_there(sessions, tmp_path):
    site = Site()
    region = "https://www.portal.test/stredocesky-kraj/"
    site.answers[region] = httpx.Response(200, text=page(card("extra-2", place="Kolín")))
    near_praha = Profile(home_address="Praha", max_minutes=30, travel_mode="car")
    run(sessions, tmp_path, site, max_details=0, home=near_praha)  # and Středočeský kraj
    assert region in site.requests

    # Středočeský kraj lists nothing; Praha, read to the end, lost one ad.
    site.answers[region] = httpx.Response(200, text="<html><body></body></html>")
    site.last_page = page(card("extra-3"))
    runner = run(sessions, tmp_path, site, max_details=0, home=near_praha)
    assert runner.progress.results["portal"]["removed"] == 1
    with sessions() as s:
        status = dict(
            s.execute(
                select(CarAd.external_id, CarAd.status).where(
                    CarAd.external_id.in_(["extra-1", "extra-2"])
                )
            ).all()
        )
        assert status == {"extra-1": "expired", "extra-2": "active"}
        assert s.scalar(select(Run.status).order_by(Run.id.desc()).limit(1)) == "partial"


class Banded(Portal):
    """Each region's listing split in two by price."""

    def listings(self, query):
        return [
            (kraj, partial(self.listing_url, f"{self.region_slugs[kraj]}/{band}"))
            for kraj in query.kraje
            for band in ("levne", "drahe")
        ]


def test_a_region_is_read_to_the_end_once_all_its_listings_are(sessions, tmp_path):
    source, base = Banded(), "https://www.portal.test"
    pages = {
        "/hlavni-mesto-praha/levne/": page(card("a1"), card("a2")),
        "/hlavni-mesto-praha/drahe/": page(card("b1")),
        "/plzensky-kraj/levne/": page(card("c1", place="Plzeň")),
        "/plzensky-kraj/drahe/": page(card("d1", place="Plzeň")),
    }

    def crawl_once():
        site = Site()
        site.answers = {base + path: httpx.Response(200, text=html) for path, html in pages.items()}
        client = httpx.Client(transport=httpx.MockTransport(site))
        fetcher = PoliteFetcher(client, delay=0, sleep=lambda s: None)
        query = ScrapeQuery(kraje=["Hlavní město Praha", "Plzeňský kraj"])
        with sessions() as s:
            for ad in s.scalars(select(CarAd)):  # clearly before this run
                ad.last_seen_at -= timedelta(minutes=1)
            s.commit()
            return crawl(source, fetcher, CarStore(s, source.id), PlaceResolver(s), query, 0)

    assert crawl_once().complete
    # Praha's cheap listing lost a2, but its other listing broke off; Plzeň lost d1.
    pages["/hlavni-mesto-praha/levne/"] = page(card("a1"))
    pages["/hlavni-mesto-praha/drahe/"] = page(card("b1"), more=True)
    pages["/hlavni-mesto-praha/drahe/?page=2"] = page()
    pages["/plzensky-kraj/drahe/"] = page(card("d2", place="Plzeň"))
    assert not crawl_once().complete
    with sessions() as s:
        expired = s.scalars(select(CarAd.external_id).where(CarAd.status == "expired")).all()
        assert expired == ["d1"]

    # Both of Praha's listings read to the end.
    pages["/hlavni-mesto-praha/drahe/?page=2"] = page(card("b2"))
    assert crawl_once().complete
    with sessions() as s:
        expired = s.scalars(select(CarAd.external_id).where(CarAd.status == "expired")).all()
        assert sorted(expired) == ["a2", "d1"]


class Answered(Banded):
    """Says when a listing has nothing, as an API with a total does."""

    def parse_listing(self, html, page_no, places):
        found = super().parse_listing(html, page_no, places)
        found.none_listed = "Nic nenalezeno" in html
        return found


def test_a_listing_with_nothing_in_it_still_completes_its_region(sessions):
    source, base = Answered(), "https://www.portal.test"
    pages = {
        "/hlavni-mesto-praha/levne/": page(card("a1"), card("a2")),
        "/hlavni-mesto-praha/drahe/": "<html><body>Nic nenalezeno</body></html>",
    }

    def crawl_once():
        site = Site()
        site.answers = {base + path: httpx.Response(200, text=html) for path, html in pages.items()}
        client = httpx.Client(transport=httpx.MockTransport(site))
        fetcher = PoliteFetcher(client, delay=0, sleep=lambda s: None)
        with sessions() as s:
            for ad in s.scalars(select(CarAd)):  # clearly before this run
                ad.last_seen_at -= timedelta(minutes=1)
            s.commit()
            query = ScrapeQuery(kraje=["Hlavní město Praha"])
            return crawl(source, fetcher, CarStore(s, source.id), PlaceResolver(s), query, 0)

    assert crawl_once().complete
    pages["/hlavni-mesto-praha/levne/"] = page(card("a1"))
    assert crawl_once().complete
    with sessions() as s:
        expired = s.scalars(select(CarAd.external_id).where(CarAd.status == "expired")).all()
        assert expired == ["a2"]
    # An empty page that says nothing is an error page: the region is not read to the end.
    pages["/hlavni-mesto-praha/drahe/"] = "<html><body></body></html>"
    pages["/hlavni-mesto-praha/levne/"] = page()
    assert not crawl_once().complete


def test_old_detail_pages_are_read_again(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=100)
    with sessions() as s:
        old = s.scalar(select(CarAd).where(CarAd.detail_at.is_not(None)).limit(1))
        old.detail_at = utcnow() - timedelta(days=31)
        s.commit()
    run(sessions, tmp_path, site, max_details=100)
    assert site.details() == 1
    run(sessions, tmp_path, site, max_details=100)
    assert site.details() == 0


def test_a_listing_that_lost_most_ads_expires_nothing(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=0)
    site.first_page = OTHER_LAST_PAGE  # one ad, no next page: 31 of 31 unseen
    runner = run(sessions, tmp_path, site, max_details=0)
    results = runner.progress.results["portal"]
    assert (results["new"], results["removed"]) == (1, 0)
    with sessions() as s:
        assert s.scalar(select(Run.status).order_by(Run.id.desc()).limit(1)) == "partial"


def test_known_card_only_cars_follow_their_card(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=0)
    site.last_page = LAST_PAGE.replace(">Škoda Fabia<", ">Škoda Fabia – sleva<")
    runner = run(sessions, tmp_path, site, max_details=0)
    assert runner.progress.results["portal"]["updated"] == 1
    with sessions() as s:
        car = s.scalar(select(Car).join(CarAd).where(CarAd.external_id == "extra-1"))
        assert car.title == "Škoda Fabia – sleva"


def test_failing_detail_pages_keep_the_cards(sessions, tmp_path):
    site = Site()
    site.detail_status = 500
    runner = run(sessions, tmp_path, site)
    assert runner.progress.phase == "done", runner.progress.errors
    assert runner.progress.results["portal"]["new"] == 31
    with sessions() as s:
        assert s.scalar(select(Run.status)) == "ok"


def test_a_dropped_connection_loses_one_ad_not_the_run(sessions, tmp_path):
    site = Site()
    site.drop_details = 3  # every attempt at the first detail page
    runner = run(sessions, tmp_path, site)
    assert runner.progress.phase == "done" and not runner.progress.errors
    assert site.details() == 3 + 4  # the dropped page's attempts, then the next four
    with sessions() as s:
        assert s.scalar(select(Run.status)) == "ok"
        assert s.scalar(select(func.count()).where(CarAd.detail_at.is_not(None))) == 4


def test_a_site_that_stops_answering_stops_its_source(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=0)
    site.drop_details = 10**6
    runner = run(sessions, tmp_path, site)
    assert "not answering" in runner.progress.errors["portal"]
    assert site.details() == 3 * 3  # three pages in a row, each tried three times
    with sessions() as s:
        run_row = s.scalar(select(Run).order_by(Run.id.desc()).limit(1))
        assert run_row.status == "partial" and "Server disconnected" in run_row.error
        assert s.scalar(select(func.count()).where(Car.status == "expired")) == 0


def test_stop_ends_a_running_search_at_once(sessions, tmp_path):
    site, finished = Site(), []
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: PRAHA,
        sources=[SOURCE],
        fetcher_factory=lambda client: PoliteFetcher(client, delay=30),  # real waits
        hooks=RunHooks(after_run=lambda: finished.append(True)),
    )
    runner.start()
    deadline = time.monotonic() + 10
    while not site.requests and time.monotonic() < deadline:
        time.sleep(0.05)
    started = time.monotonic()
    assert runner.stop(timeout=10)  # the next listing page was 30 s away
    assert time.monotonic() - started < 5
    assert not runner.progress.running and not finished


def test_a_refresh_checks_the_listed_ads_first_and_the_rest_after_the_search(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=0)
    with sessions() as s:
        s.execute(update(CarAd).values(last_seen_at=utcnow() - timedelta(days=1)))
        s.commit()
        listed = s.scalars(select(CarAd).where(CarAd.url.like("%portal.test/inzerat/%")))
        first = listed.first()
        assert first is not None
        url, title = first.url, s.get_one(Car, first.car_id).title
    site.answers[url] = httpx.Response(404)  # the listed ad is gone
    site.first_page = LAST_PAGE  # most ads are not listed any more
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: PRAHA,
        sources=[SOURCE],
        fetcher_factory=lambda client: PoliteFetcher(client, delay=0, sleep=lambda s: None),
        max_details=0,
    )
    runner.focus = Filters(q=title)  # what the user's list shows
    site.requests.clear()
    runner.start()
    runner.wait(60)
    p = runner.progress
    assert p.phase == "done" and p.checking == ("portal",)
    listing = next(i for i, u in enumerate(site.requests) if "/hlavni-mesto-praha/" in u)
    assert site.requests.index(url) < listing  # checked before the search
    others = [i for i, u in enumerate(site.requests) if "/inzerat/" in u and u != url]
    assert others and min(others) > listing  # the rest after it
    assert p.results["check"]["removed"] >= 1
    with sessions() as s:
        car = s.scalar(select(Car).join(CarAd).where(CarAd.url == url))
        assert car is not None and car.status == "expired"
        assert p.checked_since is not None
        due = [CarAd.status == "active", CarAd.last_seen_at < p.checked_since]
        assert s.scalar(select(func.count()).select_from(CarAd).where(*due)) == 0


def test_starting_over_stops_the_running_search_first(sessions, tmp_path):
    site, finished = Site(), []
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: PRAHA,
        sources=[SOURCE],
        fetcher_factory=lambda client: PoliteFetcher(client, delay=30),  # real waits
        hooks=RunHooks(after_run=lambda: finished.append(True)),
    )
    runner.start()
    deadline = time.monotonic() + 10
    while not site.requests and time.monotonic() < deadline:
        time.sleep(0.05)
    runner.start_over()
    assert runner.progress.restarting
    while not runner.progress.cleared and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runner.progress.running and runner.progress.cleared  # the fresh run
    assert runner.stop(timeout=10)
    assert not finished  # neither run finished


def test_a_changed_search_scope_restarts_the_running_search(sessions, tmp_path):
    site, finished = Site(), []
    transport = httpx.MockTransport(site)
    profile, choices = PRAHA, {}
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: profile,
        source_choices=lambda: choices,
        sources=[SOURCE],
        fetcher_factory=lambda client: PoliteFetcher(client, delay=30),  # real waits
        hooks=RunHooks(after_run=lambda: finished.append(True)),
    )
    runner.start()
    deadline = time.monotonic() + 10
    while not site.requests and time.monotonic() < deadline:
        time.sleep(0.05)
    runner.scope_changed()
    assert not runner.progress.restarting  # the same scope: the run goes on
    profile, choices = PRAHA.model_copy(update={"depart_time": "05:00"}), {"sauto": False}
    runner.scope_changed()
    assert not runner.progress.restarting  # the departure and a default made explicit
    profile = PRAHA.model_copy(update={"makes": ["skoda"]})
    runner.scope_changed()
    assert runner.progress.restarting
    while not runner.progress.cleared and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runner.progress.running and runner.progress.cleared  # the run with the new scope
    assert runner.stop(timeout=10)
    assert not finished


def test_runs_are_planned_longest_first_and_slow_sites_beside_the_slots(sessions, tmp_path):
    big, mid, small = (
        Portal("big"),
        Portal("mid", "www.mid.test", 100),
        Portal("small", "s.test", 60),
    )
    slow = [Portal("slow_a", "www.slow.test"), Portal("slow_b", "www.slow.test")]
    sources = [small, *slow, big, mid]

    def robots(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.slow.test":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 20\nAllow: /\n")
        return httpx.Response(404)

    fetcher = PoliteFetcher(httpx.Client(transport=httpx.MockTransport(robots)))
    runner = ScrapeRunner(sessions, tmp_path, sources=sources)
    query = ScrapeQuery(kraje=["Hlavní město Praha"])
    groups, beside = runner.schedule(sources, query, fetcher)
    # The slow site asks for 20 s between requests: both its sources in one thread of their
    # own beside the slots; the rest longest first (by the detail pages each reads).
    assert beside == [slow]
    assert groups == [[big], [mid], [small]]

    # Once a source has run, its last duration counts.
    with sessions() as s:
        start = utcnow()
        s.add(Run(source_id="small", started_at=start, finished_at=start + timedelta(hours=2),
                  status="ok"))  # fmt: skip
        s.commit()
    assert runner.schedule(sources, query, fetcher)[0][0] == [small]


def test_a_source_without_a_first_page_url_is_still_planned(sessions, tmp_path):
    class Broken(Portal):
        def listings(self, query):
            return [(None, ("not", "callable"))]

    fetcher = PoliteFetcher(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    )
    runner = ScrapeRunner(sessions, tmp_path, sources=[Broken("broken")])
    query = ScrapeQuery(kraje=["Hlavní město Praha"], makes=["skoda"])
    groups, slow = runner.schedule([Broken("broken"), SOURCE], query, fetcher)
    assert sorted(map(len, groups)) == [1, 1] and slow == []


def test_next_page_link_is_exact():
    tree = LexborHTMLParser('<a href="/nabidky/?page=20">20</a><a href="?x=1&amp;page=3">3</a>')
    assert not has_page_link(tree, "page", 2)
    assert has_page_link(tree, "page", 3) and has_page_link(tree, "page", 20)


def test_blocked_source_is_paused(sessions, tmp_path):
    site = Site()
    site.status = 429
    runner = run(sessions, tmp_path, site)
    assert runner.progress.phase == "failed" and "portal" in runner.progress.errors
    with sessions() as s:
        assert s.scalar(select(Run.status)) == "blocked"
        state = s.scalar(select(SourceState).where(SourceState.source_id == "portal"))
        assert "blocked_until" in state.state

    site.status = 200
    run(sessions, tmp_path, site)
    assert site.requests == []  # still paused


class Board(Portal):
    """Classifieds the user switches on: one nationwide list, newest first; an ad unseen
    for three weeks is gone."""

    default_enabled = False
    newest_first = True
    nationwide = True
    expire_after_days = 21

    def listings(self, query):
        return [(None, partial(self.listing_url, "auta"))]


def test_a_newest_first_source_only_after_opt_in(sessions, tmp_path):
    site, board = Site(), Board("board")
    ads = page(*(card(f"b{i}") for i in range(10)), more=True)
    for url in ("https://www.portal.test/auta/", "https://www.portal.test/auta/?page=2"):
        site.answers[url] = httpx.Response(200, text=ads)
    run(sessions, tmp_path, site, sources=[board])
    assert site.requests == []  # off by default

    runner = run(sessions, tmp_path, site, max_details=300, sources=[board], choices={"board": 1})
    assert runner.progress.phase == "done", runner.progress.errors
    assert runner.progress.results["board"]["new"] == 10
    # Page 2 repeats page 1 (nothing new), so the newest-first listing stops there.
    assert sum("/auta/" in u for u in site.requests) == 2
    assert site.details() == 10

    # Three weeks unseen: gone.
    site.answers = {}
    with sessions() as s:
        for ad in s.scalars(select(CarAd)):
            ad.last_seen_at -= timedelta(days=22)
        s.commit()
    runner = run(sessions, tmp_path, site, sources=[board], choices={"board": 1})
    assert runner.progress.results["board"]["removed"] == 10


def test_a_newest_first_listing_cut_short_is_read_on_next_time(sessions, tmp_path):
    site, board = Site(), Board("board")
    site.answers["https://www.portal.test/auta/"] = httpx.Response(
        200, text=page(*(card(f"b{i}") for i in range(10)), more=True)
    )
    site.answers["https://www.portal.test/auta/?page=2"] = httpx.Response(
        200, text=page(*(card(f"c{i}") for i in range(10)))
    )
    older = site.answers["https://www.portal.test/auta/?page=2"]
    site.answers["https://www.portal.test/auta/?page=2"] = httpx.Response(503)
    run(sessions, tmp_path, site, sources=[board], choices={"board": 1})
    site.answers["https://www.portal.test/auta/?page=2"] = older
    # Page 1 holds nothing new now, yet the walk goes on to the older ads it never read.
    runner = run(sessions, tmp_path, site, sources=[board], choices={"board": 1})
    assert runner.progress.results["board"]["new"] == 10
    assert sum("/auta/" in u for u in site.requests) == 2
    run(sessions, tmp_path, site, sources=[board], choices={"board": 1})
    assert sum("/auta/" in u for u in site.requests) == 1  # read to the end: newest only


def test_query_from_profile(sessions):
    with sessions() as s:
        places = PlaceResolver(s)
    profile = Profile(home_address="Kolín", max_minutes=30, makes=["skoda"], max_price=300_000)
    query = build_query(places, profile)
    assert query.home == "Kolín" and query.radius_km == 39
    assert (query.makes, query.max_price) == (["skoda"], 300_000)
    assert query.kraje[0] == "Středočeský kraj"
    anywhere = build_query(places, Profile(home_address="Kolín"))  # no travel limit
    towns = {m.kraj for m in places.municipalities if m.kraj and m.address_count >= 200}
    assert anywhere.kraje[0] == "Středočeský kraj" and set(anywhere.kraje) == towns
    nowhere = build_query(places, Profile())
    assert nowhere.home is None and len(nowhere.kraje) == 14


def test_probe_tells_a_readable_portal_from_a_changed_one(sessions):
    def fetcher(site: Site) -> PoliteFetcher:
        client = httpx.Client(transport=httpx.MockTransport(site))
        return PoliteFetcher(client, delay=0, sleep=lambda s: None)

    with sessions() as s:
        places = PlaceResolver(s)
    found = probe(SOURCE, fetcher(Site()), places)
    assert found.ok and found.leads > 10 and found.detail
    assert "title" not in found.empty and "price" not in found.empty
    changed = Site()
    changed.first_page = "<html><body>Nový vzhled</body></html>"
    found = probe(SOURCE, fetcher(changed), places)
    assert not found.ok and "no ads" in found.error
