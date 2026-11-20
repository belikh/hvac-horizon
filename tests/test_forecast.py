import math
import unittest

from custom_components.hvac_horizon import Fit, PriceSlot, forecast
from custom_components.hvac_horizon.forecast import _equilibrium_c

FIT = Fit(k=0.4, c=1.5, samples=200)


class ForecastTests(unittest.TestCase):
    def test_already_at_setpoint_takes_no_time(self):
        f = forecast(FIT, 24.0, 24.5, 30.0, 1.0, [])
        self.assertTrue(f.reachable)
        self.assertEqual(f.minutes, 0.0)
        self.assertEqual(f.energy_kwh, 0.0)

    def test_unreachable_when_equilibrium_above_target(self):
        # Equilibrium = 30 - 1.5 * 0.5 / 0.4 = 28.125 C, above a 26 C target.
        f = forecast(FIT, 30.0, 26.0, 30.0, 0.5, [])
        self.assertFalse(f.reachable)
        self.assertIsNone(f.minutes)
        self.assertAlmostEqual(f.equilibrium_c, 28.125, places=3)
        self.assertIn("unreachable", f.reason)

    def test_time_matches_closed_form(self):
        # Exact solution: T(t) = T_eq + (T0 - T_eq) * exp(-k t).
        t0, target, out, p = 28.0, 26.5, 30.0, 1.1  # equilibrium 25.9 C, so 26.5 is reachable
        t_eq = _equilibrium_c(FIT, out, p)
        t_closed_h = math.log((t0 - t_eq) / (target - t_eq)) / FIT.k
        f = forecast(FIT, t0, target, out, p, [])
        self.assertTrue(f.reachable)
        self.assertLess(abs(f.minutes - t_closed_h * 60), 1.5)  # within one 1-minute step

    def test_energy_is_power_times_time(self):
        p = 1.1
        f = forecast(FIT, 28.0, 26.5, 30.0, p, [])
        self.assertAlmostEqual(f.energy_kwh, p * f.minutes / 60, places=6)

    def test_cost_follows_price_slots(self):
        # Two 30-min slots: 0.30 then 0.10 $/kWh. Cost must reflect which slot each minute falls in.
        p = 1.1
        slots = [PriceSlot(0, 30, 0.30), PriceSlot(30, 30, 0.10), PriceSlot(60, 600, 0.10)]  # the run takes about 184 min
        f = forecast(FIT, 28.0, 26.5, 30.0, p, slots)
        self.assertTrue(f.reachable)
        cheap_minutes = max(0.0, f.minutes - 30)
        expected = p * (min(f.minutes, 30) / 60 * 0.30 + cheap_minutes / 60 * 0.10)
        self.assertAlmostEqual(f.cost, expected, places=3)

    def test_cost_is_none_when_price_missing(self):
        slots = [PriceSlot(0, 10, 0.30)]  # covers only the first 10 minutes
        f = forecast(FIT, 28.0, 26.5, 30.0, 1.1, slots)
        self.assertTrue(f.reachable)
        self.assertIsNone(f.cost)

    def test_no_cooling_effect_is_not_reachable(self):
        f = forecast(Fit(k=0.4, c=0.0, samples=200), 28.0, 26.5, 30.0, 1.1, [])
        self.assertFalse(f.reachable)
        self.assertIn("no cooling effect", f.reason)

    def test_horizon_is_enforced(self):
        # Very slow cooling: the room reaches the target only after the 12-hour horizon.
        # Heat gain is negligible, so the room falls about 0.04 K/h: 1 K takes about 25 h.
        slow = Fit(k=0.0001, c=0.05, samples=200)
        f = forecast(slow, 28.0, 27.0, 30.0, 0.8, [])
        self.assertFalse(f.reachable)
        self.assertIn("12 hours", f.reason)


if __name__ == "__main__":
    unittest.main()
