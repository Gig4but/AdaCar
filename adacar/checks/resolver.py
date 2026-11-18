"""Who is the dealer: an ad's dealer name -> IČO (ported from AdaWork's employer resolver).

No car site gives the dealer's IČO, so the name is searched in ARES with a few variants and
the candidates ranked:
- name similarity without the legal form (token_sort_ratio: token_set_ratio scores every
  name that merely contains the query as 100);
- still active, not a union or a foundation;
- registered for the car trade (CZ-NACE 45);
- registered seat where the dealer's cars are (a bonus only: chains sit in Prague).

A clear winner resolves the name; close candidates are shown on the card for the user to
pick ("nejisté přiřazení"). A name that only persons in business (sole traders) carry is
not resolved: the app keeps no data on persons ("person").
"""

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from adacar.checks.names import GENERIC, core_words, legal_form_in, variants, words
from adacar.checks.official import NOT_SELLERS, SOLE_TRADERS, Ares, Company, TooMany

MAX_QUERIES = 4
MAX_HITS = 200  # more hits than this: ordered by IČO, the right one may be missing
SURE = 0.85  # score of a clear winner...
MARGIN = 0.08  # ...ahead of the next one by this much...
SAME_NAME = 0.92  # ...and named (almost) the same
CANDIDATE = 0.6  # lower scores are not worth showing
SHOWN = 5


@dataclass
class Candidate:
    company: Company
    score: float
    similarity: float = 0.0

    def as_dict(self) -> dict:
        c = self.company
        return {"ico": c.ico, "name": c.name, "address": c.address, "active": c.active}


@dataclass
class Resolution:
    ico: str | None  # None: not resolved
    candidates: list[Candidate] = field(default_factory=list)
    method: str = "ares"  # ares | uncertain | none | person

    @property
    def companies(self) -> dict[str, Company]:
        return {c.company.ico: c.company for c in self.candidates}


def similarity(query: str, name: str) -> float:
    """Of the names without legal forms and country words; the same legal form too adds a
    little."""
    a, b = " ".join(core_words(query)), " ".join(core_words(name))
    if not a or not b:
        return 0.0
    score = fuzz.token_sort_ratio(a, b) / 100
    if a == b and legal_form_in(query):
        score += 0.05 if " ".join(words(query)) == " ".join(words(name)) else 0.0
    return min(score, 1.0)


def best_similarity(queries: list[str], name: str) -> float:
    return max((similarity(q, name) for q in queries), default=0.0)


def score(queries: list[str], c: Company, town: str | None) -> float:
    s = best_similarity(queries, c.name)
    if c.legal_form in NOT_SELLERS:
        s -= 0.5
    if not c.active:
        s -= 0.3
    if c.sells_cars:
        s += 0.05
    if town and c.municipality_code == town:
        s += 0.05
    return s


def decide(
    ranked: list[Candidate], whole: list[str] | None = None, town: str | None = None
) -> Resolution:
    """`whole`: the queries with every word of the ad's name. A company named like a shorter
    part only ("Plus s.r.o." for "Autobazar Plus"), or with a one-word name outside the car
    trade ("Baxa" is many firms), is sure only with its seat in the town of the dealer's
    cars."""
    shown = [c for c in ranked if c.score >= CANDIDATE][:SHOWN]
    if not shown:
        return Resolution(None, [], "none")
    best = shown[0]
    runner_up = shown[1].score if len(shown) > 1 else 0.0
    sure = best.score >= SURE and best.similarity >= SAME_NAME and best.score - runner_up >= MARGIN
    c = best.company
    partial = whole is not None and best_similarity(whole, c.name) < SAME_NAME
    common = len(core_words(c.name)) == 1 and not c.sells_cars
    if sure and (partial or common):
        sure = town is not None and c.municipality_code == town
    return Resolution(best.company.ico if sure else None, shown, "ares" if sure else "uncertain")


def resolve(
    ares: Ares, name: str, town: str | None = None, places: frozenset[str] = frozenset()
) -> Resolution:
    """At most MAX_QUERIES ARES searches, stopping at the first variant with 1–200 hits.
    `town`: the municipality code of the dealer's cars; `places`: folded words of their
    towns, dropped from the name in one of the variants."""
    pool: dict[str, Company] = {}
    used: list[str] = []
    for query in variants(name, places)[:MAX_QUERIES]:
        used.append(query)
        try:
            total, found = ares.search(query, MAX_HITS)
        except TooMany:
            continue
        if not found:
            continue
        for c in found:
            pool.setdefault(c.ico, c)
        if total <= MAX_HITS:
            break
    if not pool:
        return Resolution(None, [], "none")
    persons = [ico for ico, c in pool.items() if c.legal_form in SOLE_TRADERS]
    for ico in persons:
        del pool[ico]  # never stored, never shown as a candidate
    if not pool:
        return Resolution(None, [], "person")
    ranked = [
        Candidate(c, score(used, c, town), best_similarity(used, c.name)) for c in pool.values()
    ]
    ranked.sort(key=lambda c: (-c.score, c.company.ico))
    # A branch's town is not part of the company's name ("Louda Auto Teplice", "AAA AUTO -
    # Plzeň", "Praha 3"), nor are generic words: the name is whole without them. Other words
    # are ("FORTEX - AGS" is not "FORTEX").
    droppable = places | GENERIC

    def own(text: str) -> int:
        return len([w for w in core_words(text) if w not in droppable and not w.isdigit()])

    whole = [q for q in used if own(q) >= own(name)]
    return decide(ranked, whole, town)
