---
title: Test and release
summary: How to set up the test environment, what each test file checks, how the feature matrix is kept, and how to release a version.
section: develop
order: 20
audience: developers
status: draft
---

## Set up the test environment

The package imports Home Assistant. Run the tests in a virtual environment.

1. Create the environment in the repository root.

   ```sh title="Create the environment"
   python3 -m venv .venv
   ```

2. Install the pinned test requirements.

   ```sh title="Install the requirements"
   .venv/bin/pip install -r requirements-test.txt
   ```

   The pins are `pytest-homeassistant-custom-component` 0.13.375, `pytest` 9.0.3 and `pytest-asyncio` 1.4.0.

## Run the tests

Run the full suite with `scripts/check.sh`. It uses the interpreter in `PYTHON`, and `python3` when `PYTHON` is unset.

```sh title="Run the suite"
PYTHON=.venv/bin/python scripts/check.sh
```

The script runs `python -m pytest -q`. It exits non-zero on any failure. It then removes `__pycache__` and `.pytest_cache`.

To run one file, call pytest directly.

```sh title="Run one file"
.venv/bin/python -m pytest tests/test_model.py -q
```

`pytest.ini` sets `asyncio_mode = auto`, `pythonpath = .` and `testpaths = tests`.

## Test layout

| Path | Runs | Checks |
| :-- | :-- | :-- |
| `tests/test_*.py` | Unit tests, with no Home Assistant import | The pure modules, such as `decide.py`, `features.py`, `model.py` and `tpi.py`. |
| `tests/ha/test_config_flow.py` | Home Assistant | The setup and reconfigure forms. |
| `tests/ha/test_setup.py` | Home Assistant | Setup, one refresh, control, services and unload. |
| `tests/test_no_dead_code.py` | Unit | Every public module-level function is used. |
| `tests/test_published_keys.py` | Unit | Every key the coordinator publishes is shown or read back. |

`tests/ha/` has no `__init__.py`. The standard `unittest` run does not collect it. Use pytest for those tests.

### What the guard tests enforce

`test_no_dead_code.py` fails when a public function in the package has no caller. A caller is another module of the package, or a test file other than the module's own `tests/test_<module>.py`. Private functions and the Home Assistant hooks are exempt.

`test_published_keys.py` fails when a key the coordinator writes to its result is neither on a sensor nor read by the coordinator. A sensor shows a key through `STATUS_KEYS` or `extra_state_attributes`. An internal key goes in `ALLOWED` in that file, with a reason.

## Feature matrix

`docs/feature-matrix.md` compares this integration with four others. It is hand-written. It is not generated.

Each row has a status. The five values are:

| Status | Meaning |
| :-- | :-- |
| `drives` | A call writes a device or setpoint, or a value that the control path reads changes that write. |
| `published` | A sensor or attribute shows the value. No control path reads it. |
| `control` | A gate, service, repair issue, notification, event, view or user action. It writes no setpoint or device. |
| `pure` | Must be zero. |
| `missing` | Not built. The cell gives the reason in one line. |

Each cell cites the file and line numbers of the code. It names the test that shows the behaviour. A `drives` cell with no test that asserts the write says so.

Review the matrix one row at a time. Check each cited line and test against the code before you change the row. Update the row when its code changes. The header of the file says which commit it was checked against.

## Release

The repository has no release workflow. Release by hand with these steps.

1. Set `version` in `custom_components/hvac_horizon/manifest.json`.
2. Set `version` in `docs.config.json` to the same value. No test checks that the two match.
3. Check `homeassistant` in `hacs.json`. It holds the minimum Home Assistant version the integration supports.
4. Run the suite with `scripts/check.sh`.
5. Commit the version change, then push it.

:::see-also
- [[develop/architecture]]: the modules and the tick that the tests cover.
:::
