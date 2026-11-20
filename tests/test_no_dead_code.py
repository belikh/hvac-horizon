"""Every public module-level function in the package is used by code other than its own module.

A use counts when another module of custom_components/hvac_horizon calls the name, or when a file in
tests/ other than the module's own tests/test_<module>.py calls it. A function that only its own
unit tests call fails this check. Uses are matched in the syntax tree (imports, calls, attribute reads),
so a string or a local variable with the same name does not count.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "hvac_horizon"

# Names Home Assistant calls by name. No Python code in the package calls them.
# Each entry needs a reason.
ALLOWED = {
    "async_setup_entry": "Home Assistant calls it for each config entry.",
    "async_unload_entry": "Home Assistant calls it when an entry unloads.",
    "async_get_config_entry_diagnostics": "Home Assistant calls it for the diagnostics download.",
}


def _public_functions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")
    ]


def _module_files() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def _identifiers(path: Path) -> set[str]:
    """Names a file imports, calls or reads as an attribute. A local variable with the same name does not count."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.alias):
            names.add(node.name)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _external_users(name: str, own: Path) -> list[str]:
    sources = [p for p in _module_files() if p != own]
    sources += [p for p in sorted((ROOT / "tests").rglob("*.py")) if p.name != f"test_{own.stem}.py"]
    return [str(p.relative_to(ROOT)) for p in sources if name in _identifiers(p)]


CASES = [(path, name) for path in _module_files() for name in _public_functions(path)]


def test_public_functions_are_found():
    # Guard against the scan finding nothing and passing trivially.
    assert len(CASES) >= 50


@pytest.mark.parametrize(("path", "name"), CASES, ids=[f"{p.stem}.{n}" for p, n in CASES])
def test_public_function_is_used_outside_its_module(path, name):
    if name in ALLOWED:
        pytest.skip(ALLOWED[name])
    users = _external_users(name, path)
    assert users, f"{path.stem}.{name} is not referenced outside {path.name}. Wire it or delete it."
