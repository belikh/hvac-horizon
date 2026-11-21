import math
import unittest

import pytest

from custom_components.hvac_horizon.forecast import forecast
from custom_components.hvac_horizon.model import NotEnoughData, Sample, fit_confidence
from custom_components.hvac_horizon.tpi import (
    GAIN_MAX,
    GAIN_MIN,
    GAIN_STEP_MAX,
    HEAT_RATE_MAX,
    TpiFit,
    adjust_cool_gain,
    adjust_heat_gain,
    heat_up_minutes,
    learn_heat_rate,
    learn_room_rates,
    tpi_on_percent,
    tpi_threshold_forced_off,
)

K, G, C = 0.3, 0.2, 1.4


def simulate(n, ac_cycle=6, power_kw=1.0):
    temp = 24.0
    out = []
    for i in range(n):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        ac = 1.0 if (i // ac_cycle) % 2 == 0 else 0.0
        power = power_kw * ac
        nxt = temp + (K * (outdoor - temp) + G - C * power) * (5 / 60)
        out.append(Sample(minutes=5.0, temp_c=temp, temp_next_c=nxt, outdoor_c=outdoor, power_kw=power))
        temp = nxt
    return out


class LearnRoomRatesTests(unittest.TestCase):
    def test_recovers_off_and_on_coefficients(self):
        result = learn_room_rates(simulate(800))
        self.assertAlmostEqual(result.k, K, delta=0.02)
        self.assertAlmostEqual(result.g, G, delta=0.05)
        self.assertAlmostEqual(result.c, C, delta=0.05)
        self.assertGreater(result.samples_off, 0)
        self.assertGreater(result.samples_on, 0)

    def test_standard_errors_are_computed_and_scale_with_the_gain(self):
        import random

        rng = random.Random(3)
        noisy = [Sample(s.minutes, s.temp_c, s.temp_next_c + rng.gauss(0, 0.01), s.outdoor_c, s.power_kw) for s in simulate(800)]
        result = learn_room_rates(noisy)
        for value in (result.k_stderr, result.g_stderr, result.c_stderr, result.residual_k_per_h):
            self.assertIsNotNone(value)
            self.assertGreater(value, 0)
        fitted = result.to_fit(1.5)
        self.assertAlmostEqual(fitted.c_stderr, result.c_stderr * 1.5)
        self.assertEqual(fitted.k_stderr, result.k_stderr)
        self.assertIsNotNone(fit_confidence(fitted))

    def test_needs_both_states(self):
        always_on = [Sample(5.0, s.temp_c, s.temp_next_c, s.outdoor_c, 1.0) for s in simulate(200)]
        with self.assertRaises(NotEnoughData):
            learn_room_rates(always_on)

    def test_too_few_samples_raise(self):
        with self.assertRaises(NotEnoughData):
            learn_room_rates(simulate(10))

    def test_to_fit_feeds_the_forecast(self):
        tpi = learn_room_rates(simulate(800))
        fitted = tpi.to_fit()
        self.assertEqual((fitted.k, fitted.c, fitted.g), (tpi.k, tpi.c, tpi.g))
        result = forecast(fitted, temp_c=26.0, target_c=24.0, outdoor_c=30.0, running_power_kw=3.0, prices=[])  # equilibrium 16.7 C, so 24 C is reachable
        self.assertTrue(result.reachable)
        self.assertIsNotNone(result.minutes)


class AdjustCoolGainTests(unittest.TestCase):
    TPI = TpiFit(k=K, g=G, c=C, samples_off=100, samples_on=100)

    def interval(self, temp_next, power_kw=1.0):
        # 5 minutes at 26 C, outdoor 30 C. With the AC off the room rises by (K * 4 + G) * 5/60, about 0.117 K.
        return Sample(minutes=5.0, temp_c=26.0, temp_next_c=temp_next, outdoor_c=30.0, power_kw=power_kw)

    def test_room_warmer_than_predicted_lowers_the_gain(self):
        predicted_with_gain_one = 26.0 + (K * 4 + G) * 5 / 60 - C * 1.0 * 5 / 60  # gain 1.0, AC at 1 kW
        self.assertLess(adjust_cool_gain(1.0, self.interval(predicted_with_gain_one + 0.05), self.TPI), 1.0)

    def test_room_cooler_than_predicted_raises_the_gain(self):
        predicted_with_gain_one = 26.0 + (K * 4 + G) * 5 / 60 - C * 1.0 * 5 / 60  # gain 1.0, AC at 1 kW
        self.assertGreater(adjust_cool_gain(1.0, self.interval(predicted_with_gain_one - 0.05), self.TPI), 1.0)

    def test_one_interval_moves_the_gain_at_most_the_step(self):
        self.assertAlmostEqual(adjust_cool_gain(1.0, self.interval(30.0), self.TPI), 1.0 - GAIN_STEP_MAX)

    def test_gain_stays_at_the_clamp(self):
        self.assertEqual(adjust_cool_gain(GAIN_MIN, self.interval(30.0), self.TPI), GAIN_MIN)
        self.assertEqual(adjust_cool_gain(GAIN_MAX, self.interval(20.0), self.TPI), GAIN_MAX)

    def test_ac_off_interval_leaves_the_gain(self):
        self.assertEqual(adjust_cool_gain(0.8, self.interval(30.0, power_kw=0.0), self.TPI), 0.8)

    def test_to_fit_scales_only_the_cooling(self):
        fitted = self.TPI.to_fit(0.5)
        self.assertEqual((fitted.k, fitted.g), (K, G))
        self.assertAlmostEqual(fitted.c, C * 0.5)


if __name__ == "__main__":
    unittest.main()


def heating_pairs(n, k=0.1, g=0.0, h=4.0):
    """Heater-on intervals at alternating 50 and 100 percent duty, with the AC off. The true heat rate is h."""
    pairs = []
    temp = 20.0
    for i in range(n):
        duty = 50.0 if i % 2 == 0 else 100.0
        outdoor = 10.0
        rate = k * (outdoor - temp) + g + h * duty / 100.0
        nxt = temp + rate * (5 / 60)
        pairs.append((Sample(minutes=5.0, temp_c=temp, temp_next_c=nxt, outdoor_c=outdoor, power_kw=0.0), duty))
        temp = nxt
    return pairs


class HeatRateTests(unittest.TestCase):
    def test_learns_the_heat_rate_from_heating_intervals(self):
        self.assertAlmostEqual(learn_heat_rate(heating_pairs(12), k=0.1, g=0.0), 4.0, places=6)

    def test_clamps_the_heat_rate_to_its_bound(self):
        self.assertEqual(learn_heat_rate(heating_pairs(12, h=50.0), k=0.1, g=0.0), HEAT_RATE_MAX)

    def test_needs_twelve_heating_intervals(self):
        with self.assertRaises(NotEnoughData):
            learn_heat_rate(heating_pairs(11), k=0.1, g=0.0)

    def test_ignores_intervals_with_the_ac_running(self):
        pairs = [(Sample(minutes=s.minutes, temp_c=s.temp_c, temp_next_c=s.temp_next_c, outdoor_c=s.outdoor_c, power_kw=1.0), d) for s, d in heating_pairs(12)]
        with self.assertRaises(NotEnoughData):
            learn_heat_rate(pairs, k=0.1, g=0.0)

    def test_heat_up_minutes_steps_the_room_model(self):
        # A 4 K rise at 4 K/h takes 60 minutes. Stepping by minute can land one minute either side.
        minutes = heat_up_minutes(20.0, 24.0, outdoor_c=10.0, k=0.0, g=0.0, heat_rate=4.0)
        self.assertIsNotNone(minutes)
        self.assertAlmostEqual(minutes, 60.0, delta=1.0)

    def test_heat_up_minutes_is_zero_at_the_target(self):
        self.assertEqual(heat_up_minutes(24.0, 24.0, outdoor_c=10.0, k=0.1, g=0.0, heat_rate=4.0), 0.0)

    def test_heat_up_minutes_is_none_when_the_room_cannot_warm(self):
        self.assertIsNone(heat_up_minutes(19.0, 24.0, outdoor_c=5.0, k=0.5, g=0.0, heat_rate=0.0))


class HeatGainTests(unittest.TestCase):
    def heating_sample(self, rise_k, duty_pct=50.0, power_kw=0.0):
        return Sample(minutes=5.0, temp_c=20.0, temp_next_c=20.0 + rise_k, outdoor_c=10.0, power_kw=power_kw)

    def test_a_faster_rise_than_predicted_raises_the_heat_gain(self):
        # Heat rate 4 K/h at 50 percent for 5 minutes predicts 0.17 K. The room rose 0.25 K.
        self.assertGreater(adjust_heat_gain(1.0, self.heating_sample(0.25), 50.0, heat_rate=4.0, k=0.0, g=0.0), 1.0)

    def test_a_slower_rise_than_predicted_lowers_the_heat_gain(self):
        self.assertLess(adjust_heat_gain(1.0, self.heating_sample(0.05), 50.0, heat_rate=4.0, k=0.0, g=0.0), 1.0)

    def test_the_heat_gain_stays_within_its_bounds_and_its_step(self):
        gain = 1.0
        for _ in range(500):
            new = adjust_heat_gain(gain, self.heating_sample(1.0), 100.0, heat_rate=4.0, k=0.0, g=0.0)
            self.assertLessEqual(new - gain, GAIN_STEP_MAX + 1e-12)
            gain = new
        self.assertEqual(gain, GAIN_MAX)
        for _ in range(500):
            gain = adjust_heat_gain(gain, self.heating_sample(-1.0), 100.0, heat_rate=4.0, k=0.0, g=0.0)
        self.assertEqual(gain, GAIN_MIN)

    def test_the_heat_gain_does_not_move_with_the_ac_running_or_the_heater_off(self):
        self.assertEqual(adjust_heat_gain(1.0, self.heating_sample(0.25, power_kw=1.0), 50.0, heat_rate=4.0, k=0.0, g=0.0), 1.0)
        self.assertEqual(adjust_heat_gain(1.0, self.heating_sample(0.25), 0.0, heat_rate=4.0, k=0.0, g=0.0), 1.0)


def test_tpi_on_percent_follows_the_vt_heat_rule():
    # coef_int * (target - room) + coef_ext * (target - outdoor), in percent.
    assert tpi_on_percent(24.0, 20.0, 5.0, 0.1, 0.01) == pytest.approx(59.0)
    assert tpi_on_percent(22.0, 21.0, 10.0, 0.5, 0.02) == pytest.approx(74.0)  # 0.5 * 1 + 0.02 * 12


def test_tpi_on_percent_clamps_to_0_and_100():
    # Documented defaults (0.6, 0.01) saturate at 100 % for a 4 K gap.
    assert tpi_on_percent(24.0, 20.0, 5.0, 0.6, 0.01) == 100.0
    assert tpi_on_percent(24.0, 25.0, 5.0, 0.6, 0.01) == 0.0


def test_max_on_percent_caps_the_tpi_output():
    # Raw rule gives 59 % (0.1 * 4 + 0.01 * 19). A cap of 30 gives 30. No cap leaves the raw value.
    assert tpi_on_percent(24.0, 20.0, 5.0, 0.1, 0.01) == pytest.approx(59.0)
    assert tpi_on_percent(24.0, 20.0, 5.0, 0.1, 0.01, max_pct=30.0) == pytest.approx(30.0)
    assert tpi_on_percent(24.0, 20.0, 5.0, 0.1, 0.01, max_pct=90.0) == pytest.approx(59.0)


@pytest.mark.parametrize(
    ("slope", "room", "high", "low", "forced"),
    [
        (0.2, 24.6, 0.5, 0.3, True),  # rising, 0.6 above target, high 0.5: forced off
        (0.2, 24.4, 0.5, 0.3, False),  # rising but inside the high threshold
        (-0.2, 24.4, 0.5, 0.3, True),  # falling, 0.4 above target, low 0.3: forced off
        (-0.2, 24.2, 0.5, 0.3, False),  # falling, inside the low threshold
        (None, 24.6, 0.5, 0.3, False),  # no previous tick: no trend, no threshold
        (0.2, 24.6, 0.0, 0.3, False),  # high at 0 disables the rule, as in VT
        (0.2, 24.6, 0.5, 0.0, False),  # low at 0 disables the rule too
    ],
)
def test_threshold_forces_tpi_off_by_trend(slope, room, high, low, forced):
    assert tpi_threshold_forced_off(room, 24.0, slope, high, low) is forced
