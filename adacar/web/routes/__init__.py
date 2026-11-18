"""The pages and endpoints of the web interface, one module per area."""

from adacar.web.routes import (
    add,
    ai,
    checks,
    commute,
    maps,
    saved,
    search,
    settings,
    sources,
    visits,
    wizard,
)

ROUTERS = [
    m.router
    for m in (search, maps, commute, visits, saved, sources, wizard, settings, ai, add, checks)
]
