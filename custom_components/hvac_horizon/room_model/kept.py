"""The last good value of each learned model, with the time of the newest reading it was fitted from.

The readings cover 30 days. A device that stays off for longer leaves no reading to fit its model from. The kept
value serves until a new fit replaces it.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from .corrections import Corrections
from .mode_models import RateFit
from .model import Fit
from .tpi import TpiFit

TYPES: dict[str, type] = {"fit": Fit, "tpi": TpiFit, "ac_heat": RateFit, "dry": RateFit, "corrections": Corrections}
NUMBERS = ("heat_rate", "ac_heat_kw", "dry_kw")
NOT_NEGATIVE = ("k", "c", "rate")
LIMIT = 1e6  # a stored coefficient beyond this is damage

Kept = dict[str, tuple[Any, float]]  # name -> (value, time of the newest reading behind it, seconds)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > LIMIT:
        return None
    return float(value)


def _build(cls: type, raw: Any) -> Any:
    """The dataclass from its stored dict, or None when a field is missing, of the wrong type or out of range."""
    if not isinstance(raw, dict):
        return None
    values: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        number = _number(raw.get(field.name))
        if number is None:
            if raw.get(field.name) is not None:
                return None
            if "None" not in field.type:
                if field.default is dataclasses.MISSING:
                    return None
                continue  # the field takes its default
        elif (field.name in NOT_NEGATIVE or field.type == "int") and number < 0:
            return None
        values[field.name] = int(number) if number is not None and field.type == "int" else number
    return cls(**values)


def to_store(kept: Kept) -> dict[str, Any]:
    return {
        name: {"t": t, "value": dataclasses.asdict(value) if dataclasses.is_dataclass(value) else value}
        for name, (value, t) in kept.items()
    }


def from_store(raw: Any) -> Kept:
    """The kept values from the store. A damaged item is left out."""
    out: Kept = {}
    if not isinstance(raw, dict):
        return out
    for name in (*TYPES, *NUMBERS):
        item = raw.get(name)
        t = item.get("t") if isinstance(item, dict) else None
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0:
            continue
        value = _build(TYPES[name], item.get("value")) if name in TYPES else _number(item.get("value"))
        if value is None or (name in NUMBERS and value <= 0) or (name == "corrections" and not value.terms):
            continue
        out[name] = (value, float(t))
    return out
