"""The kept models survive the store and a damaged item is left out."""

import pytest

from custom_components.hvac_horizon.room_model import kept
from custom_components.hvac_horizon.room_model.corrections import Corrections
from custom_components.hvac_horizon.room_model.mode_models import RateFit
from custom_components.hvac_horizon.room_model.model import Fit
from custom_components.hvac_horizon.room_model.tpi import TpiFit

WHOLE = {
    "fit": (Fit(k=0.3, c=1.4, g=0.2, samples=400, residual_k_per_h=0.1, k_stderr=0.01, c_stderr=0.02, g_stderr=0.03, condition=8.0, outliers_dropped=2), 1.8e9),
    "tpi": (TpiFit(k=0.3, g=0.2, c=1.4, samples_off=200, samples_on=200), 1000.0),
    "heat_rate": (2.0, 2000.0),
    "ac_heat": (RateFit(rate=1.8, stderr=None, samples=24), 3000.0),
    "ac_heat_kw": (0.9, 3000.0),
    "dry": (RateFit(rate=4.0, stderr=0.2, samples=30), 4000.0),
    "dry_kw": (0.5, 4000.0),
    "corrections": (Corrections(s=0.4, c_out=-0.02, c_rh=0.0, improvement_pct=5.0), 1000.0),
}


def test_every_kept_model_survives_the_store():
    assert kept.from_store(kept.to_store(WHOLE)) == WHOLE
    assert isinstance(kept.from_store(kept.to_store(WHOLE))["fit"][0].samples, int)


@pytest.mark.parametrize("garbage", ["text", -1, 1e308, float("nan"), [], {}, None, True])
def test_a_damaged_item_is_left_out_and_the_others_load(garbage):
    stored = kept.to_store(WHOLE)
    assert kept.from_store(garbage) == {}
    for name in WHOLE:
        assert name not in kept.from_store({**stored, name: garbage})
        if garbage != 1e308:  # a time has no upper limit
            assert name not in kept.from_store({**stored, name: {"t": garbage, "value": stored[name]["value"]}})
        assert name not in kept.from_store({**stored, name: {"t": 1.0, "value": garbage}}), name
        assert len(kept.from_store({**stored, name: garbage})) == len(WHOLE) - 1
    for name, field in (("fit", "k"), ("fit", "c"), ("tpi", "k"), ("tpi", "samples_on"), ("ac_heat", "rate"), ("dry", "samples")):
        value = dict(stored[name]["value"], **{field: garbage})
        assert name not in kept.from_store({**stored, name: {"t": 1.0, "value": value}}), (name, field)


def test_a_missing_optional_field_takes_its_default():
    loaded = kept.from_store({"fit": {"t": 5.0, "value": {"k": 0.3, "c": 1.4}}})
    assert loaded == {"fit": (Fit(k=0.3, c=1.4), 5.0)}
