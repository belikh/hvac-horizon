import unittest
from datetime import date

from custom_components.hvac_horizon.investigator import ActionRecord, CallBudget, DayReading, day_context, day_summary, investigate


class InvestigateTests(unittest.TestCase):
    def test_running_ac_names_the_reason_and_the_numbers(self):
        out = investigate("cool: above comfort target", 25.1, 24.0, True, 0.0, [])
        self.assertEqual(out["answer"], "The AC is running: above comfort target. Room 25.1 C, cool above 24.0 C.")

    def test_idle_ac_says_why_it_is_not_running(self):
        out = investigate("off: at or below comfort target", 23.5, 24.0, True, 0.0, [])
        self.assertTrue(out["answer"].startswith("The AC is not running: at or below comfort target."))

    def test_away_and_open_window_are_named(self):
        out = investigate("off: away, room within setback", 26.0, 27.0, False, 12.0, [])
        self.assertIn("Nobody is home, so the away setback applies.", out["answer"])
        self.assertIn("A window has been open for 12 min.", out["answer"])

    def test_no_action_yet(self):
        self.assertEqual(investigate(None, None, None, True, 0.0, [])["answer"], "No decision yet. The control path has not run.")

    def test_recent_actions_keep_the_last_five_in_order(self):
        history = [ActionRecord(float(i), f"cool: step {i}") for i in range(8)]
        out = investigate("cool: now", 25.0, 24.0, True, 0.0, history)
        self.assertEqual(out["recent"], [f"cool: step {i}" for i in range(3, 8)])


class CallBudgetTests(unittest.TestCase):
    def test_cap_stops_calls_for_the_day(self):
        budget = CallBudget()
        day = date(2026, 11, 20)
        self.assertEqual([budget.try_take(day, 2) for _ in range(4)], [True, True, False, False])
        self.assertEqual(budget.used(day), 2)

    def test_budget_resets_on_a_new_day(self):
        budget = CallBudget()
        self.assertTrue(budget.try_take(date(2026, 11, 20), 1))
        self.assertFalse(budget.try_take(date(2026, 11, 20), 1))
        self.assertTrue(budget.try_take(date(2026, 11, 21), 1))
        self.assertEqual(budget.used(date(2026, 11, 20)), 0)

    def test_zero_limit_allows_no_call(self):
        self.assertFalse(CallBudget().try_take(date(2026, 11, 20), 0))


class DaySummaryTests(unittest.TestCase):
    def readings(self):
        # Minute 0 to 20, one reading every 5 minutes. The AC is off, then cool, then off again.
        modes = ["off", "off", "cool", "cool", "off"]
        return [DayReading(minutes=float(5 * i), room_c=25.0 + i, outdoor_c=30.0, mode=m) for i, m in enumerate(modes)]

    def test_summary_gives_room_range_share_and_last_change(self):
        text = day_summary(self.readings(), now_min=20.0)
        self.assertEqual(
            text.splitlines(),
            [
                "Last 24 hours: 5 readings, 2 mode changes.",
                "Room 25.0 to 29.0 C, last 29.0 C.",
                "Outdoor 30.0 to 30.0 C.",
                "AC in cool or heat mode for 40 % of readings.",
                "Last mode change: off, 0 min ago.",
            ],
        )

    def test_no_readings_says_so(self):
        self.assertEqual(day_summary([], now_min=0.0), "No readings in the last 24 hours.")

    def test_context_lists_each_mode_change_oldest_first(self):
        text = day_context(self.readings(), now_min=20.0)
        self.assertIn("Mode changes, oldest first:\n10 min ago: cool\n0 min ago: off", text)


if __name__ == "__main__":
    unittest.main()
