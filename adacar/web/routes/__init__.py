"""The pages and endpoints of the web interface, one module per area."""

from adacar.web.routes import commute, maps, saved, search, settings, sources, wizard

ROUTERS = [m.router for m in (search, maps, commute, saved, sources, wizard, settings)]
