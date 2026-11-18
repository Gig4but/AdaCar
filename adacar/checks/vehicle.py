"""A car's checks by its VIN (PLAN §4.5): the VIN decoded, the register's record, and the
mileage history the app saw itself.

No free source has the inspections' mileage readings without a CAPTCHA, so the history is
the ads': every km reading of the VIN the app read, kept when the ad is gone, so the same car
offered again later with fewer km is caught.
"""

from typing import Any

from adacar.checks import register, vin

ROLLBACK_KM = 2000  # a drop smaller than this may be a typo fixed, not a rollback...
ROLLBACK_SHARE = 0.05  # ...or this share of the higher reading
KEEP_READINGS = 30

# Every warning code this package gives; the rest of car.warnings is the enricher's.
CODES = (
    "vin_make", "vin_year", "km_rollback", "reg_seized", "reg_status", "reg_make", "reg_year",
    "reg_power", "reg_imported", "reg_owners",
)  # fmt: skip


def record_km(
    seen: list[dict[str, Any]], km: int, on: str, car: int | None = None
) -> list[dict[str, Any]]:
    """The readings with `km` read `on` (an ISO date) from `car` added, unless that km was
    read before (two ads of one VIN at once would else add theirs in turn forever). A car's
    new reading replaces its own earlier one: a seller fixing a typo in the ad is no
    rollback; the same VIN offered again (another car row) with fewer km is."""
    if any(r["km"] == km for r in seen):
        return seen
    if car is not None:
        seen = [r for r in seen if r.get("car") != car]
    return [*seen, {"on": on, "km": km, "car": car}][-KEEP_READINGS:]


def rolled_back(seen: list[dict[str, Any]]) -> bool:
    """A reading clearly lower than one before it."""
    highest = 0
    for reading in seen:
        km = reading["km"]
        if highest - km >= max(ROLLBACK_KM, highest * ROLLBACK_SHARE):
            return True
        highest = max(highest, km)
    return False


def warnings(car, code: str, seen: list[dict[str, Any]], record: dict[str, Any]) -> list[str]:
    """The check warnings of a car (a Car or a row of its columns) with the VIN `code`;
    `record`: the register's facts, {} when not read."""
    found = vin.mismatches(code, car.make, car.year, car.title or "")
    if rolled_back(seen):
        found.append("km_rollback")
    if record:
        found += register.mismatches(record, car)
    return found
