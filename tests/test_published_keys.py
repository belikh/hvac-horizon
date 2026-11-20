"""Every key the coordinator writes to its result is shown on an entity or read back by the coordinator.

A key counts as shown when sensor.py lists it in STATUS_KEYS or returns it from extra_state_attributes.
A key counts as read when coordinator.py reads it from result. ALLOWED holds internal keys, each with a reason.
"""

import ast
from pathlib import Path

import pytest

from custom_components.hvac_horizon.sensor import STATUS_KEYS

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "hvac_horizon"

# Internal keys that no entity shows. Each needs a reason.
ALLOWED: dict[str, str] = {}


def _tree(name: str) -> ast.Module:
    return ast.parse((PKG / name).read_text(), filename=name)


def _is_result(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "result"


def _written_keys() -> set[str]:
    """Keys the coordinator adds to a result: result["k"] = ..., a result dict literal, and result.update(k=...)."""
    keys = set()
    for n in ast.walk(_tree("coordinator.py")):
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store) and _is_result(n.value):
            if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                keys.add(n.slice.value)
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Dict):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(_is_result(t) for t in targets):
                keys |= {k.value for k in n.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "update" and _is_result(n.func.value):
            keys |= {kw.arg for kw in n.keywords if kw.arg}
            for arg in n.args:
                if isinstance(arg, ast.Dict):
                    keys |= {k.value for k in arg.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def _read_keys() -> set[str]:
    keys = set()
    for n in ast.walk(_tree("coordinator.py")):
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and _is_result(n.value):
            if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                keys.add(n.slice.value)
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get"
            and _is_result(n.func.value)
            and n.args
            and isinstance(n.args[0], ast.Constant)
        ):
            keys.add(n.args[0].value)
    return keys


def _attribute_keys() -> set[str]:
    """String literals that sensor.py reads for its entity values and attributes."""
    keys = set()
    for n in ast.walk(_tree("sensor.py")):
        if isinstance(n, ast.FunctionDef) and n.name in ("native_value", "extra_state_attributes"):
            for c in ast.walk(n):
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    keys.add(c.value)
    return keys


WRITTEN = sorted(_written_keys())
SHOWN = set(STATUS_KEYS) | _attribute_keys()
READ = _read_keys()


def test_key_scan_finds_the_published_keys():
    assert len(WRITTEN) >= 30


@pytest.mark.parametrize("key", WRITTEN)
def test_published_key_is_shown_or_read(key):
    if key in ALLOWED:
        pytest.skip(ALLOWED[key])
    assert key in SHOWN or key in READ, f"result['{key}'] is neither shown in sensor.py nor read by the coordinator"


def test_allowlist_has_no_stale_entries():
    assert set(ALLOWED) <= set(WRITTEN), sorted(set(ALLOWED) - set(WRITTEN))
