# AdaCar

Desktop app that collects cars for sale in Czechia from 16 car portals, dealers and classifieds
into one list, with filters for make, price, year, mileage, fuel and more, and the travel time
to each seller. A fork of AdaWork (job search).

See [PLAN.md](PLAN.md) for the plan and milestones, [ARCHITECTURE.md](ARCHITECTURE.md) for
how the code is organized.

## Development

```powershell
uv sync                 # create .venv with Python 3.12 and all dependencies
uv run --with zstandard python tools/fetch_motis.py  # MOTIS + osmium into vendor/ (map, travel times)
uv run adacar           # open the app window + tray icon (log: %LOCALAPPDATA%\AdaCar\Logs)
uv run python -m adacar --browser  # run only the local web server and open it in the browser
uv run pytest
uv run ruff check .
uv run pyright           # types
uv run lint-imports      # layers: a package imports only from those below it
uv run python -m adacar probe     # can every source still be read? (live, one listing + one ad each)
powershell -ExecutionPolicy Bypass -File packaging\build.ps1  # dist\AdaCar-Setup.exe
```

Set `ADACAR_DATA` to another folder to try the app with a fresh database and settings.

## Release

The app updates itself from this repository's latest release (`adacar/desktop/update.py`):
the asset must be named `AdaCar-Setup.exe`, the tag `v` + `adacar.__version__`.

```powershell
# raise __version__ in adacar/__init__.py, commit, then:
git push                # the release tag is made on the pushed commit
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
gh release create v1.0.0 dist\AdaCar-Setup.exe --title "AdaCar 1.0.0" --notes "..."
```

## License

MIT (`LICENSE`). The bundled programs and libraries keep their own licenses:
`packaging/THIRD-PARTY-NOTICES.txt`, and in the app Settings → Zobrazit licence.
