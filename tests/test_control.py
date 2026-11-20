import unittest

from custom_components.hvac_horizon import Fit, PriceSlot
from custom_components.hvac_horizon.control import plan_precool

FIT = Fit(k=0.4, c=1.5, g=0.0, samples=200)


class PrecoolTests(unittest.TestCase):
    def test_no_plan_when_deadline_too_short(self):
        # 28 -> 26.5 C takes about 184 minutes at 1.1 kW, so a 60-minute deadline cannot be met.
        p = plan_precool(FIT, 28.0, 26.5, 30.0, 1.1, deadline_min=60.0, prices=[])
        self.assertIsNone(p)

    def test_plan_arrives_by_deadline(self):
        p = plan_precool(FIT, 28.0, 26.5, 30.0, 1.1, deadline_min=300.0, prices=[])
        self.assertIsNotNone(p)
        self.assertLessEqual(p.start_in_min + p.forecast.minutes, 300.0)

    def test_prefers_cheap_window(self):
        # Expensive until minute 120, then cheap. A start in the cheap window costs less,
        # so the plan should not start in the expensive window when the deadline allows waiting.
        prices = [PriceSlot(0, 120, 0.50), PriceSlot(120, 600, 0.10)]
        p = plan_precool(FIT, 28.0, 26.5, 30.0, 1.1, deadline_min=360.0, prices=prices)
        self.assertIsNotNone(p)
        self.assertGreaterEqual(p.start_in_min, 120.0 - 5.0)

    def test_latest_start_when_no_prices(self):
        # Without prices every start costs the same, so the plan takes the latest start that still arrives in time.
        from custom_components.hvac_horizon.forecast import forecast

        base = forecast(FIT, 28.0, 26.5, 30.0, 1.1, [])
        expected_start = int((300.0 - base.minutes) // 5) * 5
        p = plan_precool(FIT, 28.0, 26.5, 30.0, 1.1, deadline_min=300.0, prices=[])
        self.assertEqual(p.start_in_min, float(expected_start))


if __name__ == "__main__":
    unittest.main()


class PrecoolStartTests(unittest.TestCase):
    def test_no_deadline_means_no_start(self):
        from custom_components.hvac_horizon.control import precool_start_in_min

        self.assertIsNone(precool_start_in_min(FIT, 28.0, 26.5, 30.0, 1.1, None, []))

    def test_start_is_returned_for_a_reachable_deadline(self):
        from custom_components.hvac_horizon.control import precool_start_in_min

        start = precool_start_in_min(FIT, 28.0, 26.5, 30.0, 1.1, 300.0, [])
        self.assertIsNotNone(start)
        self.assertGreaterEqual(start, 0.0)

    def test_unreachable_deadline_returns_none(self):
        from custom_components.hvac_horizon.control import precool_start_in_min

        self.assertIsNone(precool_start_in_min(FIT, 28.0, 26.5, 30.0, 1.1, 60.0, []))
