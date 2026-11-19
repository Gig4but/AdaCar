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
3. **Vehicle and seller checks**: the VIN against the ad and the vehicle register (with
   the user's free key), the car's mileage history as the app saw it, and the dealer behind
   an ad (company register, insolvency, VAT reliability, ratings).
4. **Automation and alerts**: scheduled searches, saved searches with new-match counts and
   notifications, price history and price-drop alerts, the list as CSV or Excel.

Shared services: a small **local AI model** (reads free-text ads, flags suspicious ones,
puts the photo of the car from the front first, turns a question in a sentence into filters)
and a **local routing engine** (MOTIS: travel times, routes, offline map).

Status: **M0–M6 done** (2026-11-19). Next: M7 (installer and delivery). How the code is
organized: [ARCHITECTURE.md](ARCHITECTURE.md).

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

### 2.4 Vehicle and seller checks (M4 spike, verified 2026-11-18)

| Check | Source | Found |
|---|---|---|
| First registration, in Czechia since (import), owners, status, make, kW, seized papers | Ministry of Transport's register API (`api.dataovozidlech.cz/api/vehicletechnicaldata/v2?vin=`) | free key per person (name + e-mail at dataovozidlech.cz/registraceapi), 27 calls a minute; no mileage. The monthly open-data extract has every vehicle but is several GB (750 MB were 1.3 M of its rows): not for a desktop app |
| Mileage at inspections (STK) | kontrolatachometru.cz | behind a CAPTCHA; the open datasets of inspections have no km → the card links there; the app keeps its own km history of each VIN from the ads |
| Stolen vehicles | Policie ČR "pátrání po vozidlech" | behind reCAPTCHA, no open data → link-out |
| Dealer identity | ARES (by name, then IČO), ISIR, VAT reliability (MF list, ~4 500 payers) | no car site gives the dealer's IČO: the name is resolved in ARES (33 of the 40 biggest dealers sure, 2 uncertain to pick from) |
| Dealer reputation | Google Places (user's key) | ratings; Firmy.cz, Mapy.com as links |
| Paid history reports | Cebia, carVertical | not used |

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
with tiles served by MOTIS; llama.cpp `llama-server` bundled (the AI model).

### 3.2 Hardware: dynamic adaptation, 8 GB as the minimum
As in AdaWork: a `HardwareProfile` measured at every start (`system/hardware.py`) decides what
runs and how big.

#### 3.2.1 What is decided dynamically
- **Routing area**: whole Czechia with enough RAM and disk, else ~150 km around home.
- **Parallel heavy jobs** (`system/resources.py`): one at a time on 8 GB (routing import, AI
  model, browser), more on bigger PCs; heavy work waits while the user is active and free RAM
  is low.
- **AI model tier**: from RAM, GPU and measured speed.

#### 3.2.2 Memory budget on 8 GB
Windows ~3–4 GB, the app ~0.5 GB, MOTIS server 0.5–1.5 GB on demand, MOTIS import only alone,
AI model 1–2 GB on demand.

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
geocoding (RÚIAN) → warning signs and AI (`ai/enrich.py`) → checks (M4) → travel times →
saved-search counts and alerts.

### 4.5 Vehicle and seller checks (M4)
`checks/` package, run by `CheckService` after each search and hourly, and at once for an
opened car or dealer (not again within an hour of a failed try). Its warnings go to
`cars.check_warnings`, which the enricher's rules pass merges into `car.warnings`; a dealer's
warnings show on its cars (`Car.all_warnings`). Filters: "Bez varování", "Prověřený prodejce", "Uvedené VIN".

**VIN against the ad.** Decoded offline (`checks/vin.py`): the maker from the first three
characters (a WMI table of the common makers) against the ad's make or the make its title
names (a site may keep the make of an ad its dealer rewrote for another car), and for the VW
group the model year (10th character) against the ad's year. With the user's register key
(`checks/register.py`, the profile's cars in the background, any car when opened, again after
30 days): make, kW and first registration against the ad; imported against "koupeno v ČR" /
"původ ČR" (negation-aware); 3+ owners against "1. majitel"; seized papers; a car not in use.
There is no free source for the service book, oil changes or accidents: the card says those
are the seller's claims.

**Mileage history.** Every km reading of a VIN the app reads is kept (`vehicle_checks`), also
after its ads are gone; a reading 2 000 km and 5 % below an earlier one is "km_rollback". The
card links to kontrolatachometru.cz (STK readings), the police search and the register.

**Dealer card** (`checks/dealers.py`, ported from AdaWork's employer card, smaller): dealers'
cars are linked by the name the ads give (`Dealer.name_key`); the name is resolved in ARES
(`checks/resolver.py`: variants without the branch's town, without a dash part or the last
word; a clear winner whose name is the whole name but for towns and generic words, a one-word
name only in the car trade or in the dealer's town; else candidates for the user to pick, or
an IČO typed in); then ARES, ISIR when flagged, the VAT list (downloaded daily), Google
ratings with the user's key (the background at most 25 a day), again after 30 days. Private
sellers get no card, and a sole trader (a person) is never looked up or stored.

### 4.6 Trip to the seller (`routing/` + `search/travel.py`)
From AdaWork: travel times from the start point (profile home or typed) to every car
location, for car / transit / walk / bike and "any", leaving at the profile's time on a
weekday, kept so the slider only filters; straight-line estimates until MOTIS data is
imported; route window with deep links; reachable-area overlay on the map.

For cars (M5): by car and public transport up to 6 hours (a car is worth a trip across the
country; walk and bike stay at 2 h), shown as "3 h 15 min"; the slider and the profile's
longest trip go to 6 h. The trip window picks the viewing's day (the next working day,
Saturday or Sunday), says how long the drive back in the bought car takes, and marks the car
"Na prohlídku". The marked cars make one viewing trip (`routing/tour.py`, "Prohlídky" in the
travel bar): from the start point to each and back in the order with the least driving
(Held-Karp over MOTIS car times between all the places, exact for up to 9 cars, the most a
Google Maps link takes; the times between two places are kept per map version, so taking a
car off asks MOTIS nothing), with the minutes of each drive and the whole trip in Mapy.com or
Google Maps; cars without a place or with only a region stay off the route. The upgrade drops
the travel times stored with the 2-hour limit (they are computed again when used).

---

## 5. Data model (SQLite)

`cars` (one per car; normalized fields, FTS5 `cars_fts` over title, make, model, place,
description), `car_ads` (one per source ad: url, price, status, detail read time, primary),
`car_images` (photo URLs), `locations`, `municipalities`, `travel_sets` / `travel_times`,
`saved_searches`, `sources` (state per source), `runs`, `llm_cache` (the model's answers by
input). `cars.warnings` (warning signs), `cars.ai_hash` (the version the AI read),
`car_images.kind` (what the AI saw on a photo). `vehicle_checks` (by VIN: km readings, the
register's record), `dealers` (a name as the ads write it, its company and checks),
`cars.dealer_id`, `cars.check_warnings`, `cars.visit_at` (marked for the viewing trip).
`price_history` (each change of a car's price), `cars.price_was` and `cars.price_dropped_at`
(the price before it dropped, M6).

---

## 6. AI module (M3)
AdaWork's `ai/` runtime, model manager, catalog, advisor and downloads, ported unchanged
(without the job-title embedder); the car work runs in `ai/enrich.py` after each search:
- **Extraction** (`ai/extract.py`) for ads with a text of 150+ characters that lack fields:
  make, model, year, km, fuel, gearbox, power, body, VIN, service book, first owner,
  accident. Only the missing fields are asked; numbers, the make, the model's name and the VIN
  must be written in the ad (km and kW with their unit); the fuel, body, gearbox and the
  seller's claims need their words, not negated ("bez servisní knížky"; small models guess
  them otherwise). Private sellers' ads are also asked whether they sell a
  car at all (kept only with a buyer's or parts seller's words) and for warning signs.
- **Warning signs** (`Car.warnings`): by rules on every car for sale (`normalize/cars.py`:
  "záloha předem", a seller abroad, mileage "not guaranteed", none of them negated; a price
  below half the median of 6+ cars of the same make, model and year, in CZK, unless the car
  has more km than they have), and by the AI in other words (kept only with the sign's words
  in the ad: on real dealers' ads a 2B model flagged "nezaručené km" everywhere). Mileage
  against the car's age flagged hundreds of honest low-km cars on the real data: rolled-back
  odometers wait for the STK readings (M4).
- **Ask in a sentence** ("dieselové kombi do 300 tisíc do hodiny od Kolína") → filters
  (`ai/ask.py` → `search/service.py: filters_to_query`), the place (only one the question
  names) located as a start point. A question shares the loaded model at once.
- **Manual add** (`/add`): paste an ad's text (read by bazos' rules at once, the price after
  "cena" or the largest amount; the AI later) or a screenshot (kept in the data folder; the AI
  writes out its text).
- **Cover photo**: some ads (dealers especially) open with a logo, banner or interior shot.
  A model that tells photos apart (`ModelSpec.photos`: Qwen3-VL, the Excellent tier) looks at
  the first photos (up to 4) of the profile's newest cars, 300 per run, until one shows the car
  from the front; the photos are downloaded for that only, not kept. `Car.photos` shows that
  one first, then other outside views. The Qwen3.5 models can not tell them apart (2–4 of 6
  test photos: a side view, a rear view, a banner all "front"): with them the sites' order
  stays.

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
4. **Artificial intelligence**: Auto (by hardware), a tier, or off; downloads meanwhile.
5. Sources (those whose robots.txt disallows us are off, with their warning).
6. Automatic searching and updates (off by default).
7. Offline map and timetables (downloaded only when asked).
8. Start: the first search runs, cars appear as each listing page is read.

### 7.2 Main pages
Search (list / map, filters with counts, travel bar, saved searches, hidden ads, live finds
while a search runs), car detail (photos, specs, equipment, text, every site's ad and price,
trip window, the car's checks and its dealer's card), Sources, Settings (appearance, where I
start from, models and price, AI, checks with the user's keys, automation, map, updates,
licences), Add an ad.

### 7.3 Alerts and export (M6)
- **Price history.** A change of the price the list shows (the primary ad's own price; other
  sites' prices are shown beside it, and an ad that becomes primary or a price another site
  filled in is no change) is kept in `price_history` (the car's detail: "Vývoj ceny").
  A lower price marks the car "zlevněno z …" (from the highest price of the drops in a row)
  until the price is back up there; the filter "Jen zlevněná" lists only such cars. No price
  ("dohodou") or another currency is no change to keep and ends the mark.
- **Notifications** after an automatic check: the cars marked "Na prohlídku" (not hidden)
  that got cheaper first, then per saved search that notifies its new and cheaper cars
  ("Octavia: nová auta 1, zlevněná 2"), or the number of new cars when none is saved; at most
  4 lines, of which the cars to view leave 2 for the rest.
- **Export.** "Stáhnout seznam: CSV · Excel" above the list: every car matching the filters
  in the list's order, with the make, model, year, km, price and the price before a drop,
  fuel, gearbox, power, body, place, region, the seller (a dealer's name; private sellers'
  names left out, §12), VIN, date, sites and the link. Numbers are numbers in Excel; CSV
  with semicolons and a BOM as Excel with Czech settings opens it; text is never a formula.

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

### M3 — Local AI — done (2026-11-18)
`ai/` ported (runtime, manager, catalog, advisor, downloads; wizard step and Settings → AI),
car extraction with checks, warning signs, manual add, ask in a sentence, cover photo (§6);
golden set of 60 real ads with the sellers' form fields (`tools/make_golden.py`,
`adacar eval`).
**Done when:** checks green (378 tests); `adacar eval` on the golden ads (form fields the
text may not repeat; answered after the checks / wrong):

| model | make | model | year | kW | fuel | body | s/ad |
|---|---|---|---|---|---|---|---|
| Qwen3.5 0.8B (Simple) | 44/0 | 53/1 | 41/1 | 50/0 | 31/2 | 19/2 | 3.7 |
| Qwen3.5 2B (Good) | 45/0 | 47/0 | 40/0 | 46/0 | 28/1 | 23/3 | 5.7 |
| Qwen3-VL 4B (Excellent) | 39/0 | 39/0 | 39/0 | 48/0 | 27/1 | 21/3 | 10.3 |

(Core Ultra 7 255U, integrated graphics; most "wrong" are form labels: SUV vs off-road,
hatchback vs liftback.) The enricher on a copy of the real database: rules 1.5 s for 53k cars
(212 prices far below similar cars), covers of 28 cars from 6 sources in 70 s, 8 classifieds'
texts in 63 s, no false warnings.

### M4 — Vehicle and seller checks — done (2026-11-18)
Spike of the §2.4 sources (STK mileage and stolen cars are behind CAPTCHAs: links, and the
app's own km history instead); VIN decoded against the ad; the register with the user's key
against the ad and its claims (§4.5); dealer card (ARES by name, ISIR, VAT list, Google);
Settings → Prověrky; filters and card warnings.
**Done when:** checks green (421 tests); live: the VAT list (4 497 payers), ISIR, the register
refusing a wrong key; on a copy of the real database: 5 233 VINs, 6 cars warned (Crafters of
model year 2022 sold as 2025, an Octavia of 2012 as 2009, a "Ford Transit Connect" with a
Golf's VIN, …), 8 056 dealer cars linked to 95 dealers, the 40 biggest resolved in 22 s (32
sure, 4 to pick, e.g. "Porsche České Budějovice", which is not the importer Porsche Česká
republika; 4 not in ARES under that name, e.g. "Porsche Praha Smíchov"). The VIN check found
ads a dealer had rewritten for another car while the site kept the old make.

### M5 — Trip to the seller, polished — done (2026-11-18)
Verify MOTIS import and times in AdaCar; trip window texts for viewings; "visit several cars"
order (nice to have).
**Done when:** checks green (433 tests); the app's own import of the whole country worked (the
map 4 min 37 s after the downloads); on a copy of it, from home: Praha 80 min by car (117
km), Brno 3 h 13 min, Ostrava 4 h 48 min (482 km; before M5 the window found no car route over
2 h and the list said "more than 2 hours" for most of the country); by car within 6 h 6 224 of
the 6 256 municipalities in 7 s, public transport 0.4 s a departure; a viewing trip through 5
towns ordered in 5.8 s, again without one of them at once. Found on the real data: of 53 516 cars only 8 556 have a place to
route to: cars.cz (33 029) names the place only on the ad's page, which the one run so far
had not read, and autanet (11 804) only the region; the places come as the pages are read.

### M6 — Automation and alerts — done (2026-11-19)
Price history, price-drop marks and notifications, "new for my saved search" alerts (from
AdaWork), export CSV/Excel (ported, §7.3).
**Done when:** checks green (437 tests); on a copy of the real database: the upgrade 0.6 s,
all 53 516 cars exported in 3.5 s (CSV 12 MB, Excel 5.8 MB; openpyxl reads year, km and
price as numbers). The real cars have no history yet: the sites give only today's price, so
the drops show from the next searches on.

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
- The register API and its key terms may change; without a key the checks still work offline
  (VIN, km history) and for dealers.

## 14. Decisions
1. Fork AdaWork instead of a fresh app (2026-11-17): fastest route; job modules removed and
   re-ported per milestone.
2. Czechia only; makes and price narrow what is downloaded.
3. sauto.cz opt-in because of its robots.txt (same pattern as AdaWork's LinkedIn).
4. bazos.cz read nationwide newest first; the region is a filter, not a crawl limit.
5. Photos are shown from the sites' URLs, not downloaded (the cover check downloads the first
   few and keeps only what it saw).
6. The AI's answers are kept only where the ad's own words support them (M3 eval: small models
   guess fuel, body, gearbox and warning signs); a wrong filter value is worse than none.
7. Mileage against the car's age is not a warning sign (hundreds of honest low-km cars on the
   real data); rolled-back odometers are M4's.
8. No free source gives STK mileage or stolen cars without a CAPTCHA (M4 spike): the app does
   not get around it; it keeps its own km history of each VIN and links to those pages.
9. The vehicle register only with the user's own key (free, personal), asked for the profile's
   cars and opened ones; never with a shared key or through the website's internal API.
