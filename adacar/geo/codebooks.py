"""MPSV open-data codebooks of municipalities, districts and regions: which okres and kraj
each municipality belongs to (geo/geocode.py builds the municipality table from it)."""

import json
from pathlib import Path
from typing import Any

import httpx

from adacar.net.http import download_if_changed

CODEBOOK_URL = "https://data.mpsv.cz/od/soubory/ciselniky/{name}.json"
CODEBOOKS = ("obce", "okresy", "kraje")


class Codebooks:
    """id -> item dicts of the codebooks above."""

    def __init__(self, books: dict[str, list[dict[str, Any]]]) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        for items in books.values():
            for item in items:
                self._by_id[item["id"]] = item

    @classmethod
    def load_dir(cls, directory: Path) -> "Codebooks":
        books = {}
        for name in CODEBOOKS:
            path = directory / f"{name}.json"
            if path.exists():
                books[name] = json.loads(path.read_text(encoding="utf-8"))["polozky"]
        return cls(books)

    @classmethod
    def fetch(cls, client: httpx.Client, directory: Path) -> "Codebooks":
        for name in CODEBOOKS:
            download_if_changed(client, CODEBOOK_URL.format(name=name), directory / f"{name}.json")
        return cls.load_dir(directory)

    def okres_map(self) -> dict[str, tuple[str, str, str | None]]:
        """Municipality code -> (okres code, okres name, kraj name)."""
        result = {}
        for item in self._by_id.values():
            if item["id"].startswith("Obec/") and item.get("okres"):
                okres = self.get(item["okres"])
                if okres:
                    result[item["kod"]] = (
                        okres["kod"],
                        okres["nazev"]["cs"],
                        self.name(okres.get("kraj")),
                    )
        return result

    def get(self, ref: dict[str, Any] | str | None) -> dict[str, Any] | None:
        if not ref:
            return None
        return self._by_id.get(ref["id"] if isinstance(ref, dict) else ref)

    def name(self, ref: dict[str, Any] | str | None) -> str | None:
        item = self.get(ref)
        return item["nazev"]["cs"] if item else None
