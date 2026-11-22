"""Config flow schema: suggested values, schedule block validation and form labels.

These tests call the schema directly, so they need no running Home Assistant instance.
"""

import json
from pathlib import Path

import pytest
import voluptuous as vol
from homeassistant.helpers import selector

from custom_components.hvac_horizon import config_flow
from custom_components.hvac_horizon.const import (
    CONF_CONTROL,
    CONF_COMFORT_C,
    CONF_HEATER_INVERTED,
    CONF_LOCK_PIN,
    CONF_SCHEDULE_BLOCKS,
    DEFAULT_COMFORT_C,
)

VALID_BLOCKS = [
    {"start_h": 6, "end_h": 9, "preset": "eco"},
    {"start_h": 22, "end_h": 6, "preset": "comfort"},  # crosses midnight
]


@pytest.mark.parametrize("value", [VALID_BLOCKS, [], [{"start_h": 0, "end_h": 24, "preset": "boost"}]])
def test_schedule_blocks_accept_a_valid_list(value):
    assert config_flow._schedule_block_problem(value) is None


@pytest.mark.parametrize(
    "value",
    [
        {"start_h": 6, "end_h": 9, "preset": "eco"},  # not a list
        [{"start_h": 6, "end_h": 9}],  # no preset
        [{"start_h": 6, "end_h": 9, "preset": "eco", "days": "mon"}],  # extra key
        [{"start_h": 25, "end_h": 9, "preset": "eco"}],  # hour above 24
        [{"start_h": -1, "end_h": 9, "preset": "eco"}],  # hour below 0
        [{"start_h": "6", "end_h": 9, "preset": "eco"}],  # hour as text
        [{"start_h": True, "end_h": 9, "preset": "eco"}],  # bool is not an hour
        [{"start_h": 6, "end_h": 6, "preset": "eco"}],  # empty block
        [{"start_h": 6, "end_h": 9, "preset": "cooling"}],  # unknown preset
        ["06:00-09:00 eco"],  # text block
    ],
)
def test_schedule_blocks_reject_a_malformed_list(value):
    assert config_flow._schedule_block_problem(value) is not None


def _sample(validator):
    v = validator.validators[0] if isinstance(validator, vol.All) else validator
    if isinstance(v, selector.BooleanSelector):
        return True
    if isinstance(v, selector.NumberSelector):
        return 2.5
    if isinstance(v, selector.ObjectSelector):
        return VALID_BLOCKS
    if isinstance(v, selector.SelectSelector):
        return v.config["options"][-1]
    if isinstance(v, (selector.EntitySelector, selector.TextSelector)):
        return ["sensor.a"] if v.config.get("multiple") else "sensor.a"
    raise AssertionError(f"no sample for {v!r}")


def test_suggested_value_shows_for_every_field_with_a_stored_value():
    stored = {str(k): _sample(v) for k, v in config_flow._schema({}).schema.items()}
    schema = config_flow._schema(stored)
    checked = 0
    for marker in schema.schema:
        name = str(marker)
        if name == CONF_LOCK_PIN:
            # The PIN is a secret, so the form never shows it.
            assert not (marker.description or {}).get("suggested_value")
            continue
        assert marker.description == {"suggested_value": stored[name]}, name
        checked += 1
    assert checked == len(stored) - 1


def test_fields_with_a_default_keep_it_when_the_entry_lacks_them():
    schema = config_flow._schema({})
    defaults = {str(k): k.default() for k in schema.schema if k.default is not vol.UNDEFINED}
    assert defaults[CONF_COMFORT_C] == DEFAULT_COMFORT_C
    assert defaults[CONF_HEATER_INVERTED] is False
    assert defaults[CONF_CONTROL] is False


def test_heater_inverted_stored_value_is_suggested_and_missing_means_false():
    for stored, expected in ((True, True), (False, False)):
        marker = next(k for k in config_flow._schema({CONF_HEATER_INVERTED: stored}).schema if str(k) == CONF_HEATER_INVERTED)
        assert marker.description == {"suggested_value": expected}
        assert marker.default() is expected
    marker = next(k for k in config_flow._schema({}).schema if str(k) == CONF_HEATER_INVERTED)
    assert marker.description is None
    assert marker.default() is False


def _translations():
    return json.loads((Path(config_flow.__file__).parent / "translations" / "en.json").read_text(encoding="utf-8"))


def test_sections_split_the_full_schema_with_no_overlap():
    fields = [str(k) for k in config_flow._schema({}).schema]
    in_sections = [name for names in config_flow.SECTIONS.values() for name in names]
    assert sorted(in_sections) == sorted(fields)  # every field is in a section, none twice


def test_only_the_room_section_has_required_fields():
    for name in config_flow.SECTIONS:
        required = [str(k) for k in config_flow._section_schema(name, {}).schema if isinstance(k, vol.Required)]
        assert (required != []) == (name == "room"), name


def test_every_section_field_has_a_label_in_its_step():
    steps = _translations()["config"]["step"]
    for name, names in config_flow.SECTIONS.items():
        labels = steps[name]["data"]
        assert set(labels) == set(names), name


def test_every_step_has_a_title_and_description_and_each_menu_option_a_label():
    steps = _translations()["config"]["step"]
    for name in [*config_flow.SECTIONS, *config_flow.LIST_STEPS, "menu", "reconfigure"]:
        assert steps[name]["title"] and steps[name]["description"], name
    assert set(config_flow.MENU_ORDER) == {*config_flow.OPTIONAL_SECTIONS, *config_flow.LIST_STEPS}
    assert set(steps["menu"]["menu_options"]) == {"finish", *config_flow.MENU_ORDER}
    assert set(steps["reconfigure"]["menu_options"]) == {"finish", "room", *config_flow.MENU_ORDER}
    for name, schema in (("schedule", config_flow._schedule_schema([{"start_h": 1, "end_h": 2, "preset": "eco"}])), ("fleet", config_flow._fleet_schema([{"device_id": "a"}]))):
        assert {str(k) for k in schema.schema} == set(steps[name]["data"]), name


def _serialise_field(validator):
    """The form field for one schema value. Only selectors have a serialised form; any other validator raises."""
    from homeassistant.helpers import config_validation as cv

    if not isinstance(validator, selector.Selector):
        raise AssertionError(f"{type(validator).__name__} is not a selector, so the form cannot render it")
    out = cv.custom_serializer(validator)
    assert isinstance(out, dict) and "selector" in out, f"{validator!r} serialised to {out!r}"
    return out


# voluptuous_serialize.convert returns UNSUPPORTED for every schema under the probatio shim that
# Home Assistant installs in this venv, even for a plain str field. So this test serialises each
# selector with cv.custom_serializer instead of converting the whole schema.
@pytest.mark.parametrize(
    "stored",
    [{}, {"comfort_c": 22.0, "control": True, "heater_inverted": True, CONF_SCHEDULE_BLOCKS: VALID_BLOCKS}],
    ids=["user", "reconfigure"],
)
def test_every_form_field_serialises_for_the_form(stored):
    schema = config_flow._schema(stored)
    for marker, validator in schema.schema.items():
        try:
            _serialise_field(validator)
        except AssertionError as error:
            raise AssertionError(f"field {marker} cannot be serialised: {error}") from None


def test_serialiser_check_rejects_a_plain_validator():
    # Negative control: the check above must fail on a field the form cannot render.
    with pytest.raises(AssertionError, match="not a selector"):
        _serialise_field(str)
