"""Build the AI golden set of car ads (PLAN §6) from a crawled database.

Labels are the fields the seller filled in on the site's form (annonce.cz, the dealers'
pages), which the ad's own text may or may not repeat; the model reads only the title and
the text. Ads with a text of at least MIN_TEXT characters, spread over makes. E-mails and
phone numbers are removed.

Usage: uv run python tools/make_golden.py <adacar.db> [out.jsonl]
"""

import json
import random
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from adacar.ai.extract import FIELDS
from adacar.db.models import Car, CarAd

OUT = Path(__file__).parents[1] / "tests" / "fixtures" / "eval" / "car_ads.jsonl"
SOURCES = {"annonce": 40, "dasweltauto": 15, "autojarov": 5}  # ads per source
MIN_TEXT = 150  # as ai/enrich.py reads
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(\+?420[\s/]?)?\b\d{3}[\s/-]?\d{3}[\s/-]?\d{3}\b")


def anonymize(text: str) -> str:
    return PHONE.sub("[telefon]", EMAIL.sub("[e-mail]", text))


def spread(cars: list[Car], n: int, rng: random.Random) -> list[Car]:
    """`n` cars, as many makes as possible."""
    by_make: dict[str, list[Car]] = {}
    for car in rng.sample(cars, len(cars)):
        by_make.setdefault(car.make or "", []).append(car)
    picked: list[Car] = []
    while len(picked) < n and any(by_make.values()):
        for group in by_make.values():
            if group and len(picked) < n:
                picked.append(group.pop())
    return picked


def main(db: str, out: str | None = None) -> int:
    rng = random.Random(5)
    out_path = Path(out) if out else OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with Session(create_engine(f"sqlite:///{db}")) as session:
        for source_id, n in SOURCES.items():
            stmt = (
                select(Car, CarAd.external_id)
                .join(CarAd, (CarAd.car_id == Car.id) & CarAd.is_primary.is_(True))
                .where(
                    CarAd.source_id == source_id,
                    Car.is_offer.is_(True),
                    func.length(Car.description) >= MIN_TEXT,
                )
            )
            found = {car.id: (car, ext) for car, ext in session.execute(stmt)}
            for car in spread([car for car, _ in found.values()], n, rng):
                rows.append(
                    {
                        "id": f"{source_id}:{found[car.id][1]}",
                        "text": anonymize(f"{car.title}\n\n{car.description.strip()}"),
                        "labels": {name: getattr(car, name) for name in FIELDS},
                    }
                )
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(rows)} ads -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
