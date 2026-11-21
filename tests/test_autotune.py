import math
import unittest

from custom_components.hvac_horizon.autotune import _period_from_peaks, _ultimate_gain, _ziegler_nichols


class AutotuneTests(unittest.TestCase):
    def test_ultimate_gain(self):
        self.assertAlmostEqual(_ultimate_gain(50.0, 1.0), 4 * 50.0 / math.pi)

    def test_ziegler_nichols_rules(self):
        g = _ziegler_nichols(ku=10.0, tu_h=0.4)
        self.assertAlmostEqual(g.kp, 6.0)
        self.assertAlmostEqual(g.ki, 6.0 / 0.2)
        self.assertAlmostEqual(g.kd, 6.0 * 0.4 / 8.0)

    def test_period_from_peaks(self):
        self.assertAlmostEqual(_period_from_peaks([0.0, 0.4, 0.8, 1.2]), 0.4)
        self.assertIsNone(_period_from_peaks([0.0]))

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            _ultimate_gain(0.0, 1.0)
        with self.assertRaises(ValueError):
            _ziegler_nichols(0.0, 0.4)


class RelayTunerTests(unittest.TestCase):
    def test_relay_switches_around_setpoint(self):
        from custom_components.hvac_horizon.autotune import RelayTuner

        t = RelayTuner(setpoint_c=22.0, hysteresis_c=0.3)
        self.assertTrue(t.step(21.5, 0.0))
        self.assertFalse(t.step(22.4, 0.1))
        self.assertFalse(t.step(22.0, 0.2))
        self.assertTrue(t.step(21.6, 0.3))

    def test_result_after_enough_oscillation(self):
        import math as m

        from custom_components.hvac_horizon.autotune import RelayTuner

        t = RelayTuner(setpoint_c=22.0, hysteresis_c=0.2, min_peaks=4)
        now = 0.0
        for i in range(400):
            temp = 22.0 + 0.5 * m.sin(i / 10.0)
            t.step(temp, now)
            now += 1.0 / 60.0
        self.assertTrue(t.done)
        g = t.result()
        self.assertIsNotNone(g)
        self.assertGreater(g.kp, 0.0)

    def test_time_limit_and_range_constants(self):
        from custom_components.hvac_horizon import const

        self.assertEqual(const.AUTOTUNE_TIME_LIMIT_MIN, 120.0)
        self.assertLess(const.AUTOTUNE_MIN_SETPOINT_C, const.AUTOTUNE_MAX_SETPOINT_C)
        self.assertEqual(const.AUTOTUNE_MIN_RISE_K, 2.0)

    def test_no_result_before_enough_peaks(self):
        from custom_components.hvac_horizon.autotune import RelayTuner

        t = RelayTuner(setpoint_c=22.0, min_peaks=4)
        t.step(21.0, 0.0)
        self.assertIsNone(t.result())
