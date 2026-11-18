# Architecture

How the code is put together, for a programmer new to it. What the app does and why is in
[PLAN.md](PLAN.md). AdaCar is a fork of AdaWork: the layers, threads and conventions are the
same.

## Layers

A package imports only from the layers below it; `uv run lint-imports` checks this
(`[tool.importlinter]` in `pyproject.toml`).

| Layer | Packages | What lives there |
|---|---|---|
| entry | `cli` | starts the window, the tray or a developer tool (`probe`, `eval`) |
| web | `web` | FastAPI app, one router per area in `web/routes/`, Jinja templates, HTMX |
| wiring | `services` | builds every long-lived part once and connects them (the only place that does) |
| features | `desktop`, `scheduler` | window and tray; automatic searches and alerts |
| | `scraper` | reading the sources, storing and merging cars |
| | `ai`, `checks` | the local model: choice, downloads, `llama-server`; reading ads, photos, questions · vehicle and seller checks: VIN, register, km history, dealers (ARES, ISIR, VAT, Google) |
| | `search`, `routing` | queries, filters and facets; travel times (MOTIS), the viewing trip's order |
| shared | `geo`, `db`, `normalize` | places; the SQLite schema; text, car vocabulary and free-text rules |
| base | `core`, `net`, `system`, `config`, `i18n`, `worker`, … | parsed cars, HTTP, this PC, settings |

`core.py` holds what every source produces (`ParsedCar`) and the values of the coded text
columns (`CarStatus`, `Precision`, …), so a typo is a type error (`uv run pyright`).
`normalize/vocab.py` holds the app's own codes for makes, fuels, gearboxes, bodies, drives,
conditions and seller kinds, with their Czech and English labels.

## Data

A `Car` is one car for sale; each site's ad of it is a `CarAd` (its URL, its price, when it
was last seen). The first site to report a car owns its fields; others fill what it lacks
(`scraper/store.py`). Photos are `CarImage` rows with the site's URL (`kind`: what the AI saw
on it; `Car.photos` puts the car from the front first). `cars_fts` is the full-text index
(title, make, model, place, description). `Car.warnings` holds the warning signs (the checks
write theirs to `Car.check_warnings`, which the rules pass merges in) and
`Car.extra["origin"]` the fields the AI filled in; `llm_cache` keeps the model's answers.
`vehicle_checks` keeps per VIN the km the app saw and the register's record; a `Dealer` is a
dealer's name as the ads write it, with the company behind it (`cars.dealer_id`).
`Car.visit_at` marks the cars of the viewing trip (`routing/tour.py`, `web/routes/visits.py`).

## One request

`web/routes/<area>.py` → a route takes `svc: Svc` (the `Services` of `services.py`) → calls a
service → `pages.render()` fills a template. Routes hold no logic beyond reading the form and
choosing the template; what two pages share is a function next to them (e.g. `source_rows`).

## One search

`ScrapeRunner` (`scraper/runner.py`) on its own thread:
1. places for what is stored so far (`geo/`, RÚIAN and the MPSV municipality codebook);
2. the ads the user's list shows, each checked on its own page (`scraper/check.py`; not after
   the stored cars were cleared);
3. every source switched on (`CrawlSource`, `scraper/crawl.py`) on four parallel slots, one
   host per slot, slow hosts beside them. The query (`geo/scope.py`) carries the regions within
   reach of home, the profile's makes and highest price;
4. every other ad not seen in this run checked; then `RunHooks.after_run`, which starts the
   `Enricher` (`ai/enrich.py`): warning signs by rules, then with the model (when there is
   one) added pictures, the ads' texts and the cover photos, the profile's cars first; and
   wakes `CheckService` (`checks/service.py`): km readings and VIN warnings of every car,
   dealers linked by name, then (the profile's first) the register and the dealers' checks.
   Its warnings reach `Car.warnings` through the enricher's rules pass (`on_changed`).

Every source writes through `CarStore`, which merges duplicates (`scraper/dedup.py`: VIN, or
make + year + district + mileage + model + price) and expires what a source no longer lists. A
run of a source is a row in `runs`.

## Background threads

| Thread | Owner | Runs | Stops |
|---|---|---|---|
| `scheduler`, `routing-data`, `updates`, `checks` | `Worker` subclasses (`worker.py`) | a round, then rests; `wake()` starts the next now | `stop()` (the app's shutdown) |
| `scrape` | `ScrapeRunner.start` | one search | `stop()`: each site at its next request |
| `enrich`, `model-download` | `Enricher.start`, `ModelManager.start_download` | one round after each search; one download | `stop()`; with the app |
| `llama-idle` | `LlamaServer` | stops the model after 5 idle minutes | with the server |
| `commute`, `routing-update` | the service that starts them | one job | end with it |
| `web-server` | `desktop/server.py` | uvicorn | `server.stop()` |

Heavy work (a routing import, the model) takes a slot of `system/resources.py`, which lets one
run at a time on 8 GB and more on bigger PCs; a question asked in a sentence shares a loaded
model at once. MOTIS and `llama-server` are separate processes; `system/winjob.py` ends them
with the app.

## Adding things

- **A source:** a `CrawlSource` subclass in `scraper/sources/<site>.py` (listing URLs, how to
  read a listing page and a detail page into `ParsedCar`), saved pages in
  `tests/fixtures/sources/<site>/`, a test in `tests/unit/test_sources.py`, and the source in
  `CRAWL_SOURCES` (`scraper/sources/__init__.py`). The Sources page, labels and switches
  follow from that list. A site whose robots.txt disallows us: `default_enabled = False`,
  `needs_consent = True`, `obey_robots = False`, a `sources.<id>.warning` text. Check it live
  with `uv run python -m adacar probe <id>`.
- **A filter:** a field on `Filters` and in `filters_from` (`search/service.py`); a value
  facet goes in `VALUE_FIELDS`, a range in `RANGES`, a switch in `CHECKS`; the panel is
  `_facets.html`.
- **A page:** a route in the area's `web/routes/` module (or a new module in
  `web/routes/__init__.py`), a template, texts in `i18n.py` in both languages (a test checks).
- **A setting:** a field in `config.py` (pydantic, with a default), its form in the Settings
  section's route and template.
- **A model prompt:** a module in `ai/` with its JSON schema (the grammar keeps the answer
  in shape) and a check of every value against the input; `uv run adacar eval` measures the
  extraction on the golden ads (`tools/make_golden.py`, `tests/fixtures/eval/`).
- **A column:** the model in `db/models.py` and a migration (`uv run python
  tools/make_migration.py`); the app migrates on start after copying the database to
  `adacar.db.bak`. A new `ParsedCar` field gets a column of the same name (a test checks).

## Checks

`uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports
&& uv run pytest` — all green on every commit.
