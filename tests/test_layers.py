"""The three layers of the package, checked on the syntax tree of every module.

room_model  the library: imports the standard library and its own modules
engine      the control decisions: imports room_model and const.py as well
the rest    the Home Assistant adapter: imports anything

A room model or an engine module that imports Home Assistant, or a room model module that imports the engine or
the adapter, fails here.
"""

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "hvac_horizon"
NAME = "custom_components.hvac_horizon"
ALLOWED = {
    "room_model": (f"{NAME}.room_model",),
    "engine": (f"{NAME}.room_model", f"{NAME}.engine", f"{NAME}.const"),
}


def _imports(path: Path) -> list[str]:
    """Every module a file imports, as an absolute name."""
    package = [*NAME.split("."), *path.relative_to(PACKAGE).parts[:-1]]
    found = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            found.append(".".join([*base, *( [node.module] if node.module else [])]))
    return found


def _files(layer: str) -> list[Path]:
    return sorted((PACKAGE / layer).glob("*.py"))


@pytest.mark.parametrize("path", _files("room_model") + _files("engine"), ids=lambda p: f"{p.parent.name}.{p.stem}")
def test_a_library_or_engine_module_imports_only_its_layer(path):
    allowed = ALLOWED[path.parent.name]
    for module in _imports(path):
        top = module.split(".")[0]
        if top in sys.stdlib_module_names:
            continue
        assert module.startswith(allowed), f"{path.name} imports {module}"


def test_both_layers_hold_modules():
    assert len(_files("room_model")) >= 12 and len(_files("engine")) >= 5
