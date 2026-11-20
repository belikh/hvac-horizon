"""Build fit samples from aligned history, and check forecasts on held-out cool-downs.

Input is a wide CSV, one row per interval of equal length, with columns:

    time        ISO 8601 timestamp with its UTC offset
    room_c      room temperature, degrees C (fused reading)
    outdoor_c   outdoor temperature, degrees C
    power_w     mean AC power over the interval, watts

Empty cells mean no reading. Rows with any empty cell are skipped.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .forecast import forecast
from .model import Fit, Sample


@dataclass(frozen=True)
class Row:
    time: str
    room_c: float
    outdoor_c: float
    power_w: float


def read_rows(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open(newline="") as fh:
        for rec in csv.DictReader(fh):
            try:
                rows.append(
                    Row(
                        time=rec["time"],
                        room_c=float(rec["room_c"]),
                        outdoor_c=float(rec["outdoor_c"]),
                        power_w=float(rec["power_w"]),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue  # empty or non-numeric cell: skip the row
    return rows


def samples_from_rows(rows: list[Row], step_min: float) -> list[Sample]:
    """One sample per pair of consecutive rows. The row at i gives the start state of interval i."""
    samples = []
    for a, b in zip(rows, rows[1:]):
        samples.append(
            Sample(
                minutes=step_min,
                temp_c=a.room_c,
                temp_next_c=b.room_c,
                outdoor_c=a.outdoor_c,
                power_kw=a.power_w / 1000.0,
            )
        )
    return samples


@dataclass(frozen=True)
class Cooldown:
    """One cool-down: power on from `start` until the room reaches `target_c`."""

    start: int  # index of the first sample
    end: int  # index of the sample where the room first reaches the target
    start_c: float
    target_c: float
    actual_min: float
    mean_power_kw: float
    mean_outdoor_c: float


def find_cooldowns(samples: list[Sample], target_c: float, margin_c: float = 1.0) -> list[Cooldown]:
    """Find AC-on runs that start at least `margin_c` above the target and reach it."""
    found = []
    i = 0
    n = len(samples)
    while i < n:
        s = samples[i]
        if s.power_kw <= 0 or s.temp_c < target_c + margin_c:
            i += 1
            continue
        j = i
        powers = []
        outdoors = []
        reached = None
        while j < n and samples[j].power_kw > 0:
            powers.append(samples[j].power_kw)
            outdoors.append(samples[j].outdoor_c)
            if samples[j].temp_next_c <= target_c:
                reached = j
                break
            j += 1
        if reached is not None:
            minutes = sum(samples[x].minutes for x in range(i, reached + 1))
            found.append(
                Cooldown(
                    start=i,
                    end=reached,
                    start_c=s.temp_c,
                    target_c=target_c,
                    actual_min=minutes,
                    mean_power_kw=sum(powers) / len(powers),
                    mean_outdoor_c=sum(outdoors) / len(outdoors),
                )
            )
        i = max(j, i + 1)
    return found


@dataclass(frozen=True)
class Check:
    start: int
    actual_min: float
    predicted_min: float | None
    error_min: float | None  # predicted minus actual, None when unreachable
    reachable: bool


def check_cooldowns(fit: Fit, cooldowns: list[Cooldown]) -> list[Check]:
    """Predict each cool-down from its start state and compare the minutes with the measured run."""
    out = []
    for cd in cooldowns:
        f = forecast(
            fit,
            temp_c=cd.start_c,
            target_c=cd.target_c,
            outdoor_c=cd.mean_outdoor_c,
            running_power_kw=cd.mean_power_kw,
            prices=[],
        )
        if f.reachable and f.minutes is not None:
            out.append(Check(cd.start, cd.actual_min, f.minutes, f.minutes - cd.actual_min, True))
        else:
            out.append(Check(cd.start, cd.actual_min, None, None, False))
    return out
