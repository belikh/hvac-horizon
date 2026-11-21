import math
import unittest

from custom_components.hvac_horizon import Fit, PriceSlot, forecast
from custom_components.hvac_horizon.forecast import _equilibrium_c, forecast_range, _step_temperature

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

    def test_exact_step_matches_the_analytic_solution_over_12_hours(self):
        t0, out, p = 28.0, 30.0, 1.1
        t_eq = out + (FIT.g - FIT.c * p) / FIT.k
        temp = t0
        for _ in range(720):
            temp = _step_temperature(FIT, temp, out, p, 1 / 60)
        analytic = t_eq + (t0 - t_eq) * math.exp(-FIT.k * 12.0)
        self.assertLess(abs(temp - analytic), 1e-9)

    def test_exact_step_with_gain_matches_the_analytic_solution(self):
        f = Fit(k=0.3, c=1.2, g=0.5)
        t_eq = 28.0 + (0.5 - 1.2 * 1.0) / 0.3
        temp = 26.0
        for _ in range(720):
            temp = _step_temperature(f, temp, 28.0, 1.0, 1 / 60)
        self.assertLess(abs(temp - (t_eq + (26.0 - t_eq) * math.exp(-0.3 * 12.0))), 1e-9)

    def test_exact_step_with_zero_k_is_linear(self):
        f = Fit(k=0.0, c=1.0, g=0.2)
        self.assertAlmostEqual(_step_temperature(f, 28.0, 30.0, 1.5, 0.5), 28.0 + (0.2 - 1.5) * 0.5, places=12)

    def test_exact_step_differs_from_euler_by_a_small_bounded_amount(self):
        # Euler with a 1-minute step has a first-order error of about k*dt/2 relative to the change.
        # For k = 0.4 /h that is 0.33 percent of the change; the bound used is 0.01 K.
        t0, out, p = 28.0, 30.0, 1.1
        euler = exact = t0
        for _ in range(720):
            euler += (FIT.k * (out - euler) - FIT.c * p + FIT.g) / 60
            exact = _step_temperature(FIT, exact, out, p, 1 / 60)
        self.assertGreater(abs(euler - exact), 0.0)
        self.assertLess(abs(euler - exact), 0.01)  # measured 0.00028 K after 12 h

    def test_horizon_is_a_parameter(self):
        f = forecast(FIT, 28.0, 26.5, 30.0, 1.1, [], horizon_min=60.0)
        self.assertFalse(f.reachable)
        self.assertIn("1 hours", f.reason)

    def test_range_brackets_the_forecast_on_a_fitted_room(self):
        import random

        from custom_components.hvac_horizon import Sample, fit
        from tests.test_model import simulate

        fitted = fit(simulate(300, noise=0.03))
        self.assertIsNotNone(fitted.c_stderr)
        f = forecast(fitted, 28.0, 26.5, 30.0, 1.1, [])
        low, high = forecast_range(fitted, 28.0, 26.5, 30.0, 1.1, [])
        self.assertTrue(f.reachable)
        self.assertLessEqual(low, f.minutes)
        self.assertLessEqual(f.minutes, high)
        self.assertLess(low, high)

    def test_range_is_none_without_standard_errors(self):
        self.assertEqual(forecast_range(FIT, 28.0, 26.5, 30.0, 1.1, []), (None, None))

    def test_range_high_is_none_when_smaller_c_cannot_reach(self):
        f = Fit(k=0.4, c=1.5, c_stderr=1.4)  # c - se = 0.1: equilibrium is above the target
        low, high = forecast_range(f, 28.0, 26.5, 30.0, 1.1, [])
        self.assertIsNotNone(low)
        self.assertIsNone(high)


if __name__ == "__main__":
    unittest.main()
