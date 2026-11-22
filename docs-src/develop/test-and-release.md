---
title: Test and release
summary: Set up the test environment, run the suite, read the guard tests, and cut a release that HACS offers to users.
section: develop
order: 20
audience: developers
status: draft
---

## Set up the test environment

The tests run on Python 3.14 against Home Assistant 2026.11.2.

```sh title="Create the environment"
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
```

`requirements-test.txt` pins `pytest-homeassistant` 2026.11.2, `pytest` 9.0.3 and `pytest-asyncio` 1.4.0. `pytest-homeassistant` is Home Assistant's own test package. Its release number is the Home Assistant version it tests.

## Run the tests

```sh title="Run the full suite"
PYTHON=.venv/bin/python scripts/check.sh
```

The script runs `python -m pytest -q` and exits non-zero on any failure. The full suite takes about 11 minutes: the settings audit takes 6 of them.

```sh title="Run everything except the settings audit"
.venv/bin/python -m pytest -q --ignore=tests/ha/test_settings_effect.py
```

```sh title="Run one file"
.venv/bin/python -m pytest tests/test_model.py -q
```

A passing run ends with a line like this:

```text title="Output"
1549 passed, 2 xfailed
```

The 2 expected failures are simulation profiles. See [[concepts/limits#known-limits-of-control|Known limits of control]].

Read the warnings as well as the failures. Fix a deprecation warning in the same change that shows it.

## Test layout

| Path | Checks |
| :-- | :-- |
| `tests/test_*.py` | The pure modules, such as `decide.py`, `features.py`, `model.py` and `fleet.py`. `tests/test_model.py` checks the solver on 500 random systems and the constrained fit on 300 random data sets against a search of every allowed fit. |
| `tests/ha/test_config_flow.py` | The setup and reconfigure forms. |
| `tests/ha/test_setup.py` | Setup, control, the room thermostat, services, restarts and unload, against fake devices. |
| `tests/ha/test_basics.py` | The basics of a thermostat, from the outside: mode, target, off, restart and dead sensors against a simulated room. |
| `tests/ha/test_settings_effect.py` | Every setting changes a device command or a published value. |
| `tests/fakes.py` | The fake AC and switch, real Home Assistant entities. `tests/ha/test_fakes.py` tests them. See [[develop/test-framework]]. |
| `tests/ha/test_device_faults.py` | A device that raises does not end the tick or the safety stop. |
| `tests/sim/` | The integration in a closed loop with a simulated room: 24 simulated hours for each of 9 cooling profiles, and the device modes and device faults. Marker `sim`. |
| `tests/test_no_dead_code.py` | Every public function has a caller. |
| `tests/test_layers.py` | A module in `room_model/` or `engine/` imports only its layer. |
| `tests/test_published_keys.py` | Every key the coordinator publishes is on a sensor or read back. |

`pytest.ini` sets `asyncio_mode = auto` and `addopts = -p no:respx`. Home Assistant installs its own httpx alias before `respx` may load.

## Guard tests

Four groups of tests exist to stop work that looks done and does nothing.

**The thermostat contract.** `test_room_mode_decides_which_devices_may_run` in `tests/ha/test_setup.py` runs each mode against a hot room and a cold room. It asserts which devices receive a start command. It was added after the AC cooled a room in `heat` mode: the mode was stored and shown, and control never read it.

**The basics.** `tests/ha/test_basics.py` uses the room thermostat as a person does. Each test sets a mode and a target, lets a simulated room run minute by minute and reads the heater switch, the AC and the thermostat state. No test reads the coordinator. The rules it holds:

- `heat` warms a room below the target and `cool` cools a room above it, at outdoor temperatures from 5 to 35 °C.
- The room reaches the target within 4 hours and stays within 1 K of it.
- A new target starts or ends a run. The thermostat shows the target you set.
- `off` and a mode change end a run in the same call.
- The heater and the AC do not run against each other at any minute, and `auto` does not take turns between them over a day.
- With a window actuator, `auto` cools with the window on a cold day and runs the AC when the window does not help.
- The mode and the target survive a restart. Dead room sensors turn the devices off.
- Twelve seeded random days hold the same rules: a random mode, start and outdoor temperature, sensors with 0.15 K of noise, one restart and one target change at random minutes. The heater starts at most 36 times and the AC at most 18 times in 6 hours.

Add a case here for every fault a user reports about basic behaviour, before the fix.

**The settings audit.** `tests/ha/test_settings_effect.py` runs a scripted day twice for each setting: once with a base entry and once with the setting changed. It compares the device commands and the published data of the two runs.

| Level in `SETTINGS` | The test passes when |
| :-- | :-- |
| `control` | The device commands differ. |
| `published` | The published data differs. The row gives the reason in `why`. |
| `exempt` | Not run. The row gives the reason in `why`. |

`test_the_scripted_day_is_repeatable` runs the same entry twice and asserts equal output. Without it a difference would prove nothing. When a new setting does not show its level, extend `House.day()` with a phase that exercises it.

**Dead code and unread keys.** `test_no_dead_code.py` fails when a public function has no caller outside its own test file. `test_published_keys.py` fails when a published key is neither on a sensor nor read by the coordinator.

## Build the documentation

The pages are in `docs-src/`. `docs/*.md` is generated from them.

```sh title="Check and build"
npx jupiter-docs check
npx jupiter-docs build --md
```

Commit `docs-src/` and the regenerated `docs/*.md` together. `docs/feature-matrix.md` is generated by `tests/ha/test_settings_effect.py` from its audit table. Regenerate it with `HVAC_WRITE_MATRIX=1 python -m pytest tests/ha/test_settings_effect.py -k feature_matrix`. The suite fails when the page is stale.

## Continuous integration

`.github/workflows/ci.yml` runs on each push: the test suite on Python 3.14, `hassfest` and the HACS validation.

## Release

1. Set `version` in `custom_components/hvac_horizon/manifest.json` and in `docs.config.json` to the same value.
2. Rename the `Unreleased` heading in `CHANGELOG.md` to the version and the date.
3. Check `homeassistant` in `hacs.json`. It is the minimum Home Assistant version, and it equals the `pytest-homeassistant` pin.
4. Run `scripts/check.sh`, commit and push to `main`.
5. Wait for CI to pass on the commit.
6. Tag the commit `v<version>`, push the tag and publish a GitHub release with the changelog section as its notes.

```sh title="Tag and publish"
git tag v0.6.2
git push origin v0.6.2
gh release create v0.6.2 --title "0.6.2" --notes-file notes.md
```

HACS offers the newest release to users. A user installs it from HACS and restarts Home Assistant.

:::see-also
- [[develop/test-framework]]: the fake devices, the simulated house and the harness.
- [[develop/architecture]]: the modules and the tick that the tests cover.
:::
