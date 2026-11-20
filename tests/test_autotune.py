import math
import unittest

from custom_components.hvac_horizon.autotune import period_from_peaks, ultimate_gain, ziegler_nichols


class AutotuneTests(unittest.TestCase):
    def test_ultimate_gain(self):
        self.assertAlmostEqual(ultimate_gain(50.0, 1.0), 4 * 50.0 / math.pi)

    def test_ziegler_nichols_rules(self):
        g = ziegler_nichols(ku=10.0, tu_h=0.4)
        self.assertAlmostEqual(g.kp, 6.0)
        self.assertAlmostEqual(g.ki, 6.0 / 0.2)
        self.assertAlmostEqual(g.kd, 6.0 * 0.4 / 8.0)

    def test_period_from_peaks(self):
        self.assertAlmostEqual(period_from_peaks([0.0, 0.4, 0.8, 1.2]), 0.4)
        self.assertIsNone(period_from_peaks([0.0]))

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            ultimate_gain(0.0, 1.0)
        with self.assertRaises(ValueError):
            ziegler_nichols(0.0, 0.4)
