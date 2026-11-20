import math
import unittest

from custom_components.hvac_horizon.forecast import forecast
from custom_components.hvac_horizon.model import NotEnoughData, Sample
from custom_components.hvac_horizon.tpi import (
    GAIN_MAX,
    GAIN_MIN,
    GAIN_STEP_MAX,
    HEAT_RATE_MAX,
    TpiFit,
    adjust_cool_gain,
    heat_up_minutes,
    learn_heat_rate,
    learn_room_rates,
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

