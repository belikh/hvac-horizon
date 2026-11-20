import math
import unittest

from custom_components.hvac_horizon.forecast import forecast
from custom_components.hvac_horizon.model import NotEnoughData, Sample
from custom_components.hvac_horizon.tpi import learn_tpi

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


class LearnTpiTests(unittest.TestCase):
    def test_recovers_off_and_on_coefficients(self):
        result = learn_tpi(simulate(800))
        self.assertAlmostEqual(result.k, K, delta=0.02)
        self.assertAlmostEqual(result.g, G, delta=0.05)
        self.assertAlmostEqual(result.c, C, delta=0.05)
        self.assertGreater(result.samples_off, 0)
        self.assertGreater(result.samples_on, 0)

    def test_needs_both_states(self):
        always_on = [Sample(5.0, s.temp_c, s.temp_next_c, s.outdoor_c, 1.0) for s in simulate(200)]
        with self.assertRaises(NotEnoughData):
            learn_tpi(always_on)

    def test_too_few_samples_raise(self):
        with self.assertRaises(NotEnoughData):
            learn_tpi(simulate(10))

    def test_to_fit_feeds_the_forecast(self):
        tpi = learn_tpi(simulate(800))
        fitted = tpi.to_fit()
        self.assertEqual((fitted.k, fitted.c, fitted.g), (tpi.k, tpi.c, tpi.g))
        result = forecast(fitted, temp_c=26.0, target_c=24.0, outdoor_c=30.0, running_power_kw=3.0, prices=[])  # equilibrium 16.7 C, so 24 C is reachable
        self.assertTrue(result.reachable)
        self.assertIsNotNone(result.minutes)


if __name__ == "__main__":
    unittest.main()
