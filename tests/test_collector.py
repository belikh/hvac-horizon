import unittest

from custom_components.hvac_horizon.collector import (
    Entry,
    price_slots,
    running_power_kw,
    samples_from_entries,
)


class CollectorTests(unittest.TestCase):
    def test_samples_pair_readings_one_step_apart(self):
        entries = [Entry(0, 26.0, 30.0, 1000), Entry(300, 25.9, 30.0, 1000), Entry(600, 25.8, 30.0, 0)]
        s = samples_from_entries(entries)
        self.assertEqual(len(s), 2)
        self.assertAlmostEqual(s[0].minutes, 5.0)
        self.assertAlmostEqual(s[0].power_kw, 1.0)

    def test_gap_breaks_the_chain(self):
        entries = [Entry(0, 26.0, 30.0, 1000), Entry(300, 25.9, 30.0, 1000), Entry(3000, 25.0, 30.0, 0)]
        s = samples_from_entries(entries)
        self.assertEqual(len(s), 1)  # the 45-minute gap yields no sample

    def test_out_of_order_entries_are_sorted(self):
        entries = [Entry(600, 25.8, 30.0, 0), Entry(0, 26.0, 30.0, 1000), Entry(300, 25.9, 30.0, 1000)]
        self.assertEqual(len(samples_from_entries(entries)), 2)

    def test_fan_level_power_is_treated_as_off(self):
        entries = [Entry(0, 26.0, 30.0, 100), Entry(300, 25.9, 30.0, 1100), Entry(600, 25.8, 30.0, 1100)]
        s = samples_from_entries(entries)
        self.assertEqual(s[0].power_kw, 0.0)  # 100 W fan draw
        self.assertEqual(s[1].power_kw, 1.1)

    def test_running_power_ignores_off_readings(self):
        entries = [Entry(0, 26, 30, 0), Entry(300, 26, 30, 1100), Entry(600, 26, 30, 900)]
        self.assertAlmostEqual(running_power_kw(entries, now=600), 1.0)

    def test_running_power_none_when_ac_never_ran(self):
        entries = [Entry(0, 26, 30, 0), Entry(300, 26, 30, 5)]
        self.assertIsNone(running_power_kw(entries, now=300))

    def test_running_power_respects_window(self):
        entries = [Entry(0, 26, 30, 1500), Entry(10000, 26, 30, 800)]
        self.assertAlmostEqual(running_power_kw(entries, now=10000, window_s=3600), 0.8)

    def test_price_slots_lengths_and_offsets(self):
        # Forecast starts at 0 s, 300 s and 1800 s, with a 300 s and a 1500 s gap.
        forecast = [(0.0, 0.30), (300.0, 0.28), (1800.0, 0.20)]
        slots = price_slots(forecast, now=150.0)
        # The first slot ends at 300 s, after now, so it is kept with a start 2.5 minutes before now.
        self.assertAlmostEqual(slots[0].start_min, -2.5)
        self.assertAlmostEqual(slots[0].duration_min, 5.0)  # full 300 s slot
        self.assertAlmostEqual(slots[-1].duration_min, 30.0)  # default last slot length

    def test_price_slots_drop_past_slots(self):
        forecast = [(0.0, 0.30), (300.0, 0.28)]
        slots = price_slots(forecast, now=400.0)
        self.assertEqual([s.price_per_kwh for s in slots], [0.28])


if __name__ == "__main__":
    unittest.main()
