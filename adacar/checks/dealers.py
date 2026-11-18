"""Dealer cards (PLAN §4.5, ported from AdaWork's employer cards, smaller): cars are linked to
a dealer by the name their ads give, the name is resolved to an IČO in ARES (a name not found
or uncertain is tried again after 30 days), and the company is checked in ARES, ISIR and the
VAT list, again after 30 days; Google ratings with the user's key. Private sellers get no
card, and a dealer that is a person (a sole trader) is not looked up: the app keeps no data
on persons.
"""

import logging
import re
import threading
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy import bindparam, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from adacar.checks import google, official, resolver
from adacar.checks.names import name_key, words
from adacar.db.models import Car, Dealer, Location, SourceState, table
from adacar.normalize.text import fold

log = logging.getLogger(__name__)

DUE = timedelta(days=30)  # checked and rated again after this; not found: resolved again
STATE_ID = "checks"  # SourceState: the Google quota
GOOGLE_PER_DAY = 25  # details the background may spend a day (950 a month in all)


def warnings_of(d: Dealer) -> list[str]:
    found = []
    if d.match in ("ares", "user") and d.ico and d.checked_at and not d.legal_name:
        found.append("not_found")  # an IČO ARES does not know (any more)
    if d.ended:
        found.append("ended")
    if any(
        not fold(str(p.get("state", ""))).startswith(official.ENDED_STATES)
        for p in d.insolvency or []
    ):
        found.append("insolvency")
    if d.vat_unreliable:
        found.append("vat")
    return found


def fill(d: Dealer, c: official.Company) -> None:
    """The register's facts on the dealer."""
    d.legal_name, d.legal_form, d.founded, d.ended = c.name, c.legal_form, c.founded, c.ended
    d.address, d.dic = c.address, c.dic


def forget(d: Dealer) -> None:
    """The company behind the dealer changed or is unknown: none of its facts stay."""
    d.legal_name = d.legal_form = d.founded = d.ended = d.address = d.dic = None
    d.insolvency, d.vat_unreliable, d.checked_at = [], None, None
    d.rating = d.rating_count = d.maps_url = d.reviews_at = None
    d.reviews = []


class Dealers:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        vat: official.VatList,
        google_key: Callable[[], str | None],
        clock: Callable[[], datetime],
        stopping: Callable[[], bool],
    ) -> None:
        self.sessions = sessions
        self.vat = vat
        self.google_key = google_key
        self.clock = clock
        self.stopping = stopping
        self.lock = threading.Lock()  # one writer of dealers: the pass or an opened card
        self.quota_lock = threading.Lock()  # one spender of the Google quota

    # --- cars -> dealers ----------------------------------------------------------------------

    def link(self) -> int:
        """Every active dealer's car gets its dealer by name (a new name, a new dealer); a car
        no longer offered by a dealer (now private, no name) loses it. Returns how many cars
        changed dealer."""
        with self.lock, self.sessions() as s:
            known = dict(s.execute(select(Dealer.name_key, Dealer.id)).all())
            is_dealer = (Car.seller_kind == "dealer") & Car.seller_name.is_not(None)
            rows = s.execute(
                select(Car.id, Car.seller_name, Car.dealer_id, is_dealer).where(
                    Car.status == "active", or_(is_dealer, Car.dealer_id.is_not(None))
                )
            ).all()
            for _, name, _, dealer in rows:
                key = name_key(name)
                if dealer and key and key not in known:
                    d = Dealer(name_key=key, name=(name or "")[:200])
                    s.add(d)
                    s.flush()
                    known[key] = d.id
            changes = [
                {"car_id": car_id, "dealer": target}
                for car_id, name, was, dealer in rows
                if (target := known.get(name_key(name)) if dealer else None) != was
            ]
            if changes:
                cars = table(Car)
                s.execute(
                    update(cars)
                    .where(cars.c.id == bindparam("car_id"))
                    .values(dealer_id=bindparam("dealer")),
                    changes,
                )
            s.commit()
        return len(changes)

    def wanted(self, s: Session, profile: set[int] | None) -> list[int]:
        """Dealers of active cars, the profile's first, then by how many cars they offer."""
        rows = s.execute(
            select(Car.id, Car.dealer_id).where(Car.status == "active", Car.dealer_id.is_not(None))
        )
        counts: Counter[int] = Counter()
        mine: set[int] = set()
        for car_id, did in rows:
            if did is not None:
                counts[did] += 1
                if profile is not None and car_id in profile:
                    mine.add(did)
        return sorted(counts, key=lambda d: (d not in mine, -counts[d]))

    # --- name -> IČO ---------------------------------------------------------------------------

    def resolve_due(self, ids: list[int], ares: official.Ares, limit: int, force=False) -> int:
        """Names never resolved, or not found / uncertain 30 days ago."""
        old = self.clock() - DUE
        with self.sessions() as s:
            stmt = select(Dealer.id).where(Dealer.id.in_(ids), Dealer.match != "user")
            if not force:
                tried = ("none", "uncertain", "person", "error")
                again = Dealer.match.in_(tried) & (Dealer.resolved_at < old)
                stmt = stmt.where(or_(Dealer.match == "", again))
            due = set(s.scalars(stmt))
        done = 0
        for did in [i for i in ids if i in due][:limit]:
            if self.stopping():
                break
            try:
                self.resolve(did, ares)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    raise  # ARES is down: the next pass
                # ARES refuses this name (an odd one): the others go on, it waits 30 days.
                log.info("ARES refused the name of dealer %s: %s", did, exc)
                self._mark(did, "error")
            done += 1
        return done

    def _mark(self, did: int, match: str) -> None:
        with self.lock, self.sessions() as s:
            d = s.get(Dealer, did)
            if d is not None and d.match != "user":
                d.match, d.resolved_at, d.ico, d.candidates = match, self.clock(), None, []
                forget(d)
                d.warnings = warnings_of(d)
                s.commit()

    def _places(self, s: Session, did: int) -> tuple[str | None, frozenset[str]]:
        """The town (code) most of the dealer's cars are in, and the folded words of all
        their towns, which may follow the dealer's name in the ads."""
        rows = s.execute(
            select(Location.municipality_code, Location.municipality, Location.okres)
            .join(Car, Car.location_id == Location.id)
            .where(Car.dealer_id == did)
            .limit(200)
        ).all()
        codes = Counter(code for code, _, _ in rows if code)
        town = codes.most_common(1)[0][0] if codes else None
        places = {w for _, *names in rows for n in names if n for w in words(n)}
        return town, frozenset(places)

    def resolve(self, did: int, ares: official.Ares) -> None:
        with self.sessions() as s:
            d = s.get(Dealer, did)
            if d is None:
                return
            name = d.name
            town, places = self._places(s, did)
        res = resolver.resolve(ares, name, town, places)
        with self.lock, self.sessions() as s:
            d = s.get(Dealer, did)
            if d is None or d.match == "user":  # the user picked one meanwhile
                return
            if res.ico != d.ico:
                forget(d)  # another company, or none: checked anew
            d.ico, d.match, d.resolved_at = res.ico, res.method, self.clock()
            d.candidates = [c.as_dict() for c in res.candidates] if res.ico is None else []
            company = res.companies.get(res.ico or "")
            if company is not None:
                fill(d, company)
            d.warnings = warnings_of(d)
            s.commit()

    def correct(self, did: int, ico: str, ares: official.Ares) -> str | None:
        """The user's pick: the company with this IČO (an old 6–7 digit one with its leading
        zeros). None when taken; "bad_ico" when ARES does not know it, "person" for a sole
        trader (the app keeps no data on persons)."""
        if re.fullmatch(r"\d{6,7}", ico):
            ico = ico.zfill(8)
        if not re.fullmatch(r"\d{8}", ico):
            return "bad_ico"
        companies = ares.companies([ico])
        if ico not in companies:
            return "bad_ico"
        if companies[ico].legal_form in official.SOLE_TRADERS:
            return "person"
        with self.lock, self.sessions() as s:
            d = s.get(Dealer, did)
            if d is None:
                return "bad_ico"
            d.ico, d.match, d.candidates, d.resolved_at = ico, "user", [], self.clock()
            forget(d)
            fill(d, companies[ico])
            d.warnings = warnings_of(d)
            s.commit()
        return None

    # --- the official checks ----------------------------------------------------------------------

    def check_due(self, ids: list[int], client: httpx.Client, force: bool = False) -> int:
        """ARES (and ISIR when flagged, the VAT list) for dealers with an IČO, never checked or
        checked 30 days ago."""
        old = self.clock() - DUE
        with self.sessions() as s:
            stmt = select(Dealer.id, Dealer.ico).where(Dealer.id.in_(ids), Dealer.ico.is_not(None))
            if not force:
                stmt = stmt.where(or_(Dealer.checked_at.is_(None), Dealer.checked_at < old))
            due = {did: ico for did, ico in s.execute(stmt) if ico}
        order = [i for i in ids if i in due]
        ares = official.Ares(client)
        vat = self.vat.fresh()
        checked = 0
        for i in range(0, len(order), official.BATCH):
            if self.stopping():
                break
            chunk = {due[did]: did for did in order[i : i + official.BATCH]}
            companies = ares.companies(list(chunk))
            proceedings, unknown = {}, set()
            for ico, c in companies.items():
                if c.insolvency_flag:
                    try:
                        proceedings[ico] = official.insolvency(client, ico)
                    except (httpx.HTTPError, ValueError) as exc:
                        log.info("ISIR %s failed: %s", ico, exc)
                        unknown.add(ico)
            now = self.clock()
            with self.lock, self.sessions() as s:
                for ico, did in chunk.items():
                    d = s.get(Dealer, did)
                    if d is None or d.ico != ico:
                        continue
                    c = companies.get(ico)
                    if c is not None:
                        fill(d, c)
                        if ico in unknown:  # the flag alone warns until ISIR answers
                            d.insolvency = d.insolvency or [{"state": "", "url": ""}]
                        else:
                            d.insolvency = proceedings.get(ico, [])
                    else:
                        d.legal_name = None
                    d.vat_unreliable = vat.unreliable_since(d.dic)
                    if ico not in unknown:  # else checked again in the next pass
                        d.checked_at = now
                    d.warnings = warnings_of(d)
                    checked += 1
                s.commit()
        return checked

    def apply_vat_list(self) -> int:
        """The daily VAT list changed: every checked dealer is looked up in it again."""
        vat = self.vat.fresh()
        changed = 0
        with self.lock, self.sessions() as s:
            for d in s.scalars(select(Dealer).where(Dealer.dic.is_not(None))):
                since = vat.unreliable_since(d.dic)
                if since != d.vat_unreliable:
                    d.vat_unreliable = since
                    d.warnings = warnings_of(d)
                    changed += 1
            s.commit()
        return changed

    # --- Google ratings ---------------------------------------------------------------------

    def _quota(self, s: Session) -> google.Quota:
        row = s.get(SourceState, STATE_ID)
        return google.Quota.load((row.state or {}).get("google") if row else None, self.clock())

    def google_quota(self) -> google.Quota:
        with self.sessions() as s:
            return self._quota(s)

    def _spend(self, quota: google.Quota) -> None:
        with self.sessions() as s:
            row = s.get(SourceState, STATE_ID) or SourceState(source_id=STATE_ID)
            row.state = {**(row.state or {}), "google": quota.dump()}
            s.add(row)
            s.commit()

    def rate_due(
        self, ids: list[int], client: httpx.Client, limit: int, force=False, background=False
    ) -> int:
        """Google ratings of dealers with a company, not rated or rated 30 days ago; the
        background pass at most GOOGLE_PER_DAY a day, so opened cards keep the month's
        free details."""
        key = self.google_key()
        if not key:
            return 0
        old = self.clock() - DUE
        with self.sessions() as s:
            stmt = select(Dealer.id).where(Dealer.id.in_(ids), Dealer.ico.is_not(None))
            if not force:
                stmt = stmt.where(or_(Dealer.reviews_at.is_(None), Dealer.reviews_at < old))
            due = set(s.scalars(stmt))
        done = 0
        for did in [i for i in ids if i in due][:limit]:
            if self.stopping():
                break
            with self.sessions() as s:
                d = s.get(Dealer, did)
                if d is None:
                    continue
                town = s.scalar(
                    select(Location.municipality)
                    .join(Car, Car.location_id == Location.id)
                    .where(Car.dealer_id == did, Location.municipality.is_not(None))
                    .limit(1)
                )
                query = f"{d.name} {town or ''}".strip()
            with self.quota_lock:
                quota = self.google_quota()
                if background and quota.background >= GOOGLE_PER_DAY:
                    break
                try:
                    place_id = google.find_place(client, key, query)
                    if place_id and background:
                        quota.background += 1
                    place = google.details(client, key, place_id, quota) if place_id else None
                except google.QuotaUsed:
                    break
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    log.info("Google rating of %s failed: %s", query, exc)
                    continue  # asked again in the next pass
                finally:
                    self._spend(quota)
            with self.lock, self.sessions() as s:
                d = s.get(Dealer, did)
                if d is None:
                    continue
                d.rating = place.rating if place else None
                d.rating_count = place.count if place else None
                d.maps_url = place.url if place else None
                d.reviews = place.reviews if place else []
                d.reviews_at = self.clock()
                s.commit()
            done += 1
        return done
