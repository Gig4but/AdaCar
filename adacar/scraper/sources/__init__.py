"""Every car source, and what the app derives from their list."""

from adacar.core import Source
from adacar.scraper.sources import (
    aaaauto,
    annonce,
    autanet,
    autobazareu,
    autojarov,
    bazar_cz,
    bazos,
    carscz,
    dasweltauto,
    hyperinzerce,
    inzeruj,
    mototechna,
    ojeteauto,
    sauto,
    tipcars,
    toyota,
)

CRAWL_SOURCES = (
    # Portals and dealers
    sauto.SOURCE,
    tipcars.SOURCE,
    autobazareu.SOURCE,
    carscz.SOURCE,
    autanet.SOURCE,
    aaaauto.SOURCE,
    mototechna.SOURCE,
    autojarov.SOURCE,
    dasweltauto.SOURCE,
    toyota.SOURCE,
    # Classifieds
    bazos.SOURCE,
    bazar_cz.SOURCE,
    ojeteauto.SOURCE,
    hyperinzerce.SOURCE,
    annonce.SOURCE,
    inzeruj.SOURCE,
)
# All of them, in the order the Sources page lists them.
SOURCES: tuple[Source, ...] = CRAWL_SOURCES

MANUAL = "manual"  # source id of ads the user pasted in
LABELS = {MANUAL: "Vloženo ručně", **{s.id: s.label for s in SOURCES}}
# Sources the user can switch on and off, in display order: (id, on by default).
SWITCHABLE = tuple((s.id, s.default_enabled) for s in SOURCES)
KINDS = {s.id: s.kind for s in SOURCES}
NEEDS_CONSENT = frozenset(s.id for s in SOURCES if s.needs_consent)
