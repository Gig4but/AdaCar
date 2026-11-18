# AdaCar — Implementation Plan

Local-first desktop app that finds cars for sale in Czechia (used and new, every make) on all
the big sites, in one list. A fork of AdaWork (`../AdaWork`, job search): the same app shell,
crawler, store, search, travel times, automation and installer, with the job domain replaced
by cars.

Four main modules:
1. **Scraper**: collects car ads from the portals (sauto.cz, …) and classifieds (bazos.cz, …),
   merging the same car offered on several sites.
2. **Web interface**: search and filters (make, price, year, km, fuel, gearbox, body, …), map,
   car detail, and a **trip window**: how long it takes to get to the seller by car / public
   transport, with a travel-time slider that also filters the list.
3. **Vehicle and seller checks** (planned, M4): mileage history and checks by VIN from
   official open data, warning signs, and the dealer behind an ad (company register,
   insolvency, reviews).
4. **Automation and alerts**: scheduled searches, saved searches with new-match counts and
   notifications; planned: price-drop tracking.

Shared services: a small **local AI model** (planned, M3: reads free-text ads, flags
suspicious ones) and a **local routing engine** (MOTIS: travel times, routes, offline map).

Status: **M0 and M1 done** (2026-11-17). Next: M2 (more sources). How the code is organized:
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Goals and non-goals

### Goals
- Collect cars for sale from the main Czech car portals and classifieds, for **any make**,
  normalized into one schema so filters work across sources.
- **As simple as possible for the user:** one installer, no Python, no command line; one
  **first-run wizard** asking only what the app cannot work out (home, which makes and up to
  what price, which sources); scheduled searching **off by default**, one switch to turn on.
- Prefer, in this order: official/free APIs and open data → the JSON a site's own pages use →
  HTML parsing → AI for free text.
- Show **how long the trip to the seller takes** and filter by "reachable within N minutes".
- Flag **warning signs** of an ad and of the car (planned: mileage rollback, crash, stolen,
  unreliable dealer).
- No paid services required. Data stays on the computer.

### Non-goals (v1)
- Buying, financing, contacting sellers from the app, multi-user hosting.
- Bypassing CAPTCHAs, logins, paywalls or security checks; storing passwords.
- Paid VIN reports (Cebia and similar) — linked to, never scraped.

---

## 2. Research findings (verified 2026-11-17)

### 2.1 Car portals

| Source | How | Size | robots.txt | Status |
|---|---|---|---|---|
| **sauto.cz** | JSON API its pages use: `GET /api/v1/items/search?category_id=838&limit=…&offset=…` (filters `region_seo`, `price_from`/`price_to`, `manufacturer_model_seo=skoda\|volkswagen`, `sort=price`), detail `GET /api/v1/items/<id>` (VIN, description, equipment, body, drive, seller). Offset capped at **10 000** → read per region in price bands. Gone ad: 404 or `status != active`. | ~110k passenger cars; 89 % dealers | **`User-agent: *` / `Disallow: /`** (only named search engines) | **M1, opt-in** (§12) |
| **auto.bazos.cz** | HTML; one category per make (`/skoda/`, `/<offset>/`, 40 per page, newest first). Card: title, price, place + PSČ, date, start of text; detail: whole text, seller name, photos, map link. No structured car fields. | Škoda alone ~31k | category pages allowed; search/locality/price filters disallowed | **M1, on by default** |
| tipcars.com | HTML, one listing, last changed first, ~1 MB pages → 50 pages a run | ~52k | allowed except `/cz`, `/*/api/` | **M2** |
| autobazar.eu | Next.js `__NEXT_DATA__`, one listing per Czech region, newest first; many dealers price in EUR (no CZK price then) | ~18k in CZ | `page=2`–`50` only | **M2** |
| cars.cz | HTML, one price-sorted listing (settings zlib+hex in `?p=`), full pass | ~33k | allowed | **M2** |
| autanet.cz | HTML, one listing per make, newest first | ~25k | allowed (no `/?`) | **M2** |
| aaaauto.cz, mototechna.cz | Angular `ng-state` JSON in the page; branch id → town | ~11k, ~1k | allowed | **M2** |
| autojarov.cz | HTML, full pass | ~740 | allowed (no queries) | **M2** |
| dasweltauto.cz | JSON API of its pages (GET), full pass | ~5.6k | allowed | **M2** |
| proverenevozy.toyota.cz | HTML, newest first | ~3.7k | none (404) | **M2** |
| bazar.cz, ojeteauto.cz, hyperinzerce.cz, annonce.cz, inzeruj.cz | classifieds, HTML, newest first (inzeruj: full pass); annonce asks 20 s between requests | ~6k, ~8k, ~1.3k, ?, ~90 | allowed | **M2** |
| autoesa.cz | HTML, ~5.7k | — | allowed | skipped: no car says where it stands |
| skodaplus.cz | GraphQL POST only | — | allowed | later: needs POST in the crawler |
| hyundai-promise.cz | HTML, 9 per page, session-held paging | ~1.3k | allowed | later |
| sbazar.cz; avizo.cz, autobazar-online.cz, autoinzerce.cz, yauto.cz | — | — | sbazar disallows all; the rest show a bot challenge | not read |
| carvago.com | cars abroad | — | — | not Czech |
| Facebook Marketplace | Edge + Playwright session (AdaWork `social/` reader) | — | forbidden by Meta's terms | opt-in with warnings, later |

### 2.2 Free text and duplicates
- Private sellers write year, km, engine and fuel in the text ("r.v. 2015, najeto 183 xxx km,
  2.0 TDI 110 kW"): rules in `normalize/cars.py` read clear statements; the AI (M3) the rest.
- Classifieds carry ads that sell no car (buyers, "výkup", parts): kept, never listed.
- The same car is listed on several sites (dealers everywhere, private sellers on sauto and
  bazos): merged by VIN, else by make + year + district + mileage ± 2 % + model + price ± 15 %
  (`scraper/dedup.py`). Each site's price is kept on its ad.

### 2.3 Sources by priority
1. sauto.cz (largest, structured, VIN) — opt-in because of robots.txt.
2. bazos.cz (largest private market).
3. Dealer aggregators: tipcars.com, autobazar.eu, cars.cz, autanet.cz.
4. Dealer chains and makers' programs: AAA Auto, Mototechna, Auto Jarov, Das WeltAuto, Toyota.
5. Smaller classifieds: bazar.cz, ojeteauto.cz, hyperinzerce.cz, annonce.cz, inzeruj.cz.
6. Facebook Marketplace (opt-in).

### 2.4 Vehicle and seller checks (to verify in the M4 spike)

| Check | Candidate source | Notes |
|---|---|---|
| Mileage history, inspections (STK) | Ministry of Transport open data (registr silničních vozidel, technické prohlídky) on data.gov.cz | by VIN; mileage readings at inspections → rollback flag |
| First registration, import, owners | same register | origin vs. what the ad says |
| Stolen vehicles | Policie ČR "pátrání po vozidlech" | by VIN / plate; check terms |
| Dealer identity | ARES (IČO), ISIR (insolvency), VAT reliability | port of AdaWork `employers/official.py` |
| Dealer reputation | Google Places (user's key), Firmy.cz | port of AdaWork `employers/google.py` |
| Paid history reports | Cebia, carVertical | link-out only (sauto shows Cebia's verification flag) |

### 2.5 Routing, public transport, maps
Unchanged from AdaWork (spike 2026-10): **MOTIS** (MIT) bundled for car, public transport,
walking and bike, one-to-many times and offline vector tiles; **OpenStreetMap** Czech extract
(Geofabrik); **Spojenka GTFS** nationwide timetables (non-commercial use only → the app stays
personal and free, §12); **osmium-tool** clips the map to ~150 km around home on smaller PCs;
**RÚIAN** address points (ČÚZK) and the MPSV codebook of municipalities place every ad; deep
links to Mapy.com, IDOS and Google Maps.

---

## 3. Technology choices

### 3.1 What the user gets
Inno Setup installer around a PyInstaller build; pywebview window (Edge WebView2); pystray
tray icon with optional start with Windows and notifications; MOTIS bundled; MapLibre GL JS
with tiles served by MOTIS; llama.cpp `llama-server` bundled (used from M3).

### 3.2 Hardware: dynamic adaptation, 8 GB as the minimum
As in AdaWork: a `HardwareProfile` measured at every start (`system/hardware.py`) decides what
runs and how big.

#### 3.2.1 What is decided dynamically
- **Routing area**: whole Czechia with enough RAM and disk, else ~150 km around home.
- **Parallel heavy jobs** (`system/resources.py`): one at a time on 8 GB (routing import, AI
  model, browser), more on bigger PCs; heavy work waits while the user is active and free RAM
  is low.
- **AI model tier** (M3): from RAM, GPU and measured speed.

#### 3.2.2 Memory budget on 8 GB
Windows ~3–4 GB, the app ~0.5 GB, MOTIS server 0.5–1.5 GB on demand, MOTIS import only alone,
AI model 1–2 GB on demand (M3).

### 3.3 Inside the app
Python 3.12, uv, httpx, selectolax, SQLite + SQLAlchemy 2 + Alembic (auto-migrate, backup
first), FTS5, pyproj, Pillow, FastAPI + Jinja2, HTMX + Pico.css + MapLibre (vendored),
pydantic v2; ruff, pyright, import-linter, pytest. Background work on threads.

---

## 4. Architecture

Layers and threads: [ARCHITECTURE.md](ARCHITECTURE.md).

### 4.1 Source contract
A `CrawlSource` (`scraper/crawl.py`) gives its listings (one or more per region), how to read
a listing page into leads (with a card each) and a detail page into a `ParsedCar`, and its
pace and limits. `crawl()` does the rest the same way for every source: new ads, known ones
only touched, detail pages within a per-run limit, re-reads of old details, expiry after a
complete pass (a region counts once **all** its listings were read to the end), or after N
days unseen for newest-first sources.

### 4.2 Shared HTTP layer
`net/polite.py`: robots.txt, Crawl-delay and a pause between requests per host, retries,
redirects hop by hop, a refusal pauses the source for 12 h. A source may skip robots.txt only
when the user switched it on after a warning (`needs_consent`, `obey_robots = False`).

### 4.3 Social and browser sources
Facebook Marketplace (M2) reuses AdaWork's Edge reader: opt-in, slow, daily limits, never logs
in for the user, personal data deleted 14 days after last seen.

### 4.4 Pipeline
listing → leads → (detail) → `ParsedCar` → `CarStore` (dedup, gap filling, per-ad price) →
geocoding (RÚIAN) → AI (M3) → checks (M4) → travel times → saved-search counts and alerts.

### 4.5 Vehicle and seller checks (M4)
`checks/` package: VIN-keyed register lookups cached locally, a car card section with mileage
history and warnings, dealer card (port of AdaWork's employer card). Filters: "no warnings",
"verified dealer", "mileage history consistent".

### 4.6 Trip to the seller (`routing/` + `search/travel.py`)
Unchanged from AdaWork: travel times from the start point (profile home or typed) to every
car location, for car / transit / walk / bike and "any", leaving at the profile's time on a
weekday, kept up to 120 minutes so the slider only filters; straight-line estimates until
MOTIS data is imported; route window with deep links; reachable-area overlay on the map.

---

## 5. Data model (SQLite)

`cars` (one per car; normalized fields, FTS5 `cars_fts` over title, make, model, place,
description), `car_ads` (one per source ad: url, price, status, detail read time, primary),
`car_images` (photo URLs), `locations`, `municipalities`, `travel_sets` / `travel_times`,
`saved_searches`, `sources` (state per source), `runs`. Planned: `price_history` (M6),
`vehicle_checks`, `dealers` (M4), `llm_cache` (M3).

---

## 6. AI module (M3)
Port AdaWork's `ai/` runtime, model manager, catalog and downloads unchanged; new prompts:
- **Extraction** for free-text ads: make, model, year, km, fuel, gearbox, power, body, price
  terms, VIN when written, "dovoz", "na díly".
- **Warning signs**: price far below similar cars, "záloha předem", foreign seller asking for
  a deposit, rolled-back mileage claims.
- **Ask in a sentence** ("dieselové kombi do 300 tisíc do hodiny od Kolína") → filters
  (`search/service.py: filters_to_query`).
- **Manual add**: paste an ad's text or a screenshot.

### 6.6 Model choice
As in AdaWork: wizard step and Settings → AI; Auto by hardware, tiers Simple / Good /
Excellent, or an own OpenAI-compatible server.

---

## 7. User interface

### 7.1 First-run wizard
1. Language and colour scheme.
2. **Where you start from**: home, how you travel (default: every way, as in AdaWork),
   longest trip, usual departure.
3. **Models and price**: brands (none = all), highest price.
4. Sources (those whose robots.txt disallows us are off, with their warning).
5. Automatic searching and updates (off by default).
6. Offline map and timetables (downloaded only when asked).
7. Start: the first search runs, cars appear as each listing page is read.

### 7.2 Main pages
Search (list / map, filters with counts, travel bar, saved searches, hidden ads, live finds
while a search runs), car detail (photos, specs, equipment, text, every site's ad and price,
trip window), Sources, Settings (appearance, where I start from, models and price,
automation, map, updates, licences).

---

## 8. Project structure
See [ARCHITECTURE.md](ARCHITECTURE.md). Sources: `adacar/scraper/sources/<site>.py`, fixtures
in `tests/fixtures/sources/<site>/`.

---

## 9. Milestones

### M0 — Fork and skeleton — done (2026-11-17)
AdaWork copied without its git history, build output and spike data; package renamed
`adawork` → `adacar` (own data folder `%LOCALAPPDATA%\AdaCar`); job-only modules removed
(employers, social, AI pipeline, MPSV, 20 job sources, export, manual add); kept: desktop shell,
web shell, crawler, store, search, routing/travel, scheduler, saved searches, updater,
packaging.

### M1 — Car data core, two sources, search, wizard — done (2026-11-17)
- `ParsedCar`, car vocabularies, `cars` / `car_ads` / `car_images` schema (fresh initial
  migration), `CarStore`, car dedup, free-text rules (`normalize/cars.py`).
- sauto.cz (JSON API, region × price bands, opt-in) and bazos.cz (makes, newest first).
- Car filters, facets and sorting; car card and detail; wizard and Settings with makes and
  highest price; sauto consent.
- **Done when:** `ruff`, `pyright`, `lint-imports`, `pytest` green (221 tests); `adacar probe`
  reads both sources live (2026-11-17: sauto 100 cards + detail, bazos 40 cards + detail).
- Not done: a full first search on a real profile (long: polite pace), the installer build.

### M2 — More sources — done (2026-11-17)
14 more sources (16 in all, table in §2.1): portals, dealer chains, makers' used-car programs
and classifieds. Cards are stored as each listing page is read; a newest-first listing cut
short (offline, app closed) is read on to its end next time.
**Done when:** each source has fixtures + tests and passes `adacar probe` (2026-11-17: all
16 ok). Left for later: Škoda Plus (POST), Hyundai Promise, Facebook Marketplace (opt-in),
sauto incremental runs (newest first per band, `edit_date` → `Lead.modified`).

### M3 — Local AI
Port `ai/` (runtime, manager, catalog, advisor), car extraction prompt + golden set
(`tools/make_golden.py` for cars), warning signs, manual add, ask in a sentence.

### M4 — Vehicle and seller checks
Spike §2.4 sources; VIN mileage history and rollback flag; stolen check; dealer card (port
employer checks: ARES, ISIR, VAT, reviews); filters and card warnings.

### M5 — Trip to the seller, polished
Verify MOTIS import and times in AdaCar; trip window texts for viewings; "visit several cars"
order (nice to have).

### M6 — Automation and alerts
Price history per ad, price-drop marks and notifications, "new for my saved search" alerts
(exist), export CSV/Excel (port).

### M7 — Installer and delivery
Build `AdaCar-Setup.exe`; Czech guide `packaging/navod.html` for cars (still AdaWork's);
GitHub repository for updates (`desktop/update.py: REPO`).

---

## 10. Testing strategy
Saved real pages per source (`tests/fixtures/sources/`), parsers tested against them; the
crawl framework, store, dedup, search and web tested with fakes; `adacar probe` for live
checks; all checks green on every commit.

## 11. Setup and disk usage
App ~150 MB; database ~100–300 MB for 100k cars; map and timetables 1–5 GB (only if asked);
AI model 1–3 GB (M3).

## 12. Legal and ethical notes
- Read only public pages, slowly, identified by the app's User-Agent; obey robots.txt.
- **sauto.cz** disallows generic crawlers in robots.txt: off by default; the user switches it
  on after a warning; then 3 s between requests and a pause on the first refusal. Re-evaluate
  before a public release (ask Seznam.cz for permission or drop the source).
- Personal data (seller names, phone numbers) stays on the PC; social posts are deleted 14
  days after last seen.
- Spojenka timetables: non-commercial use only → the app stays personal and free.

## 13. Risks
- Sites change layout or block: probe + fixtures catch it; a refusal pauses the source.
- sauto.cz API is internal and may change or close.
- bazos volume: newest-first walks keep runs short; the first run is long.
- Dedup false merges: conservative rules (no mileage → no merge).

## 14. Decisions
1. Fork AdaWork instead of a fresh app (2026-11-17): fastest route; job modules removed and
   re-ported per milestone.
2. Czechia only; makes and price narrow what is downloaded.
3. sauto.cz opt-in because of its robots.txt (same pattern as AdaWork's LinkedIn).
4. bazos.cz read nationwide newest first; the region is a filter, not a crawl limit.
5. Photos are shown from the sites' URLs, not downloaded.
