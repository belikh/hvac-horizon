import unittest

from custom_components.hvac_horizon.collector import (
    Entry,
    price_slots,
    running_power_kw,
    samples_from_entries,
)


class ResampleTests(unittest.TestCase):
    def test_last_known_value_on_grid(self):
        from custom_components.hvac_horizon.collector import resample_states

        points = [(0.0, 1000.0), (420.0, 0.0)]
        grid = resample_states(points, start=0.0, end=600.0, step=300.0)
        self.assertEqual(grid, [(0.0, 1000.0), (300.0, 1000.0), (600.0, 0.0)])

    def test_none_before_first_state(self):
        from custom_components.hvac_horizon.collector import resample_states

        grid = resample_states([(400.0, 5.0)], start=0.0, end=600.0, step=300.0)
        self.assertEqual(grid[0], (0.0, None))
        self.assertEqual(grid[2], (600.0, 5.0))

    def test_unordered_points_are_sorted(self):
        from custom_components.hvac_horizon.collector import resample_states

        grid = resample_states([(300.0, 2.0), (0.0, 1.0)], start=0.0, end=300.0, step=300.0)
        self.assertEqual(grid, [(0.0, 1.0), (300.0, 2.0)])

    def test_rejects_bad_grid(self):
        from custom_components.hvac_horizon.collector import resample_states

        with self.assertRaises(ValueError):
            resample_states([], start=10.0, end=0.0, step=300.0)


class CsvExportTests(unittest.TestCase):
    def test_csv_has_header_and_sorted_rows(self):
        from custom_components.hvac_horizon.collector import entries_to_csv

        entries = [Entry(600, 25.8, 30.0, 0), Entry(0, 26.0, 30.0, 1000)]
        text = entries_to_csv(entries)
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "time,room_c,outdoor_c,power_w")
        self.assertEqual(lines[1], "0,26.00,30.00,1000")
        self.assertEqual(lines[2], "600,25.80,30.00,0")

    def test_csv_round_trips_through_backfill_reader(self):
        import tempfile
        from pathlib import Path

        from custom_components.hvac_horizon.backfill import read_rows
        from custom_components.hvac_horizon.collector import entries_to_csv

        entries = [Entry(0, 26.0, 30.0, 1000), Entry(300, 25.9, 30.0, 0)]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h.csv"
            path.write_text(entries_to_csv(entries))
            rows = read_rows(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].power_w, 1000.0)


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

    def test_energy_counter_gives_interval_mean(self):
        from custom_components.hvac_horizon.collector import interval_power_kw

        # 0.1 kWh over 5 minutes is 1.2 kW, even though the instantaneous reading was 0.
        a = Entry(0, 26, 30, 0, energy_kwh=10.0)
        b = Entry(300, 26, 30, 0, energy_kwh=10.1)
        self.assertAlmostEqual(interval_power_kw(a, b), 1.2)

    def test_energy_counter_reset_falls_back_to_power(self):
        from custom_components.hvac_horizon.collector import interval_power_kw

        a = Entry(0, 26, 30, 1100, energy_kwh=50.0)
        b = Entry(300, 26, 30, 1100, energy_kwh=0.2)  # counter reset
        self.assertAlmostEqual(interval_power_kw(a, b), 1.1)

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


class CycleGuardTests(unittest.TestCase):
    def _samples(self, on_steps, off_steps, runs):
        from custom_components.hvac_horizon.model import Sample

        out = []
        for _ in range(runs):
            out += [Sample(5.0, 26.0, 25.9, 30.0, 1.0)] * on_steps
            out += [Sample(5.0, 25.9, 25.9, 30.0, 0.0)] * off_steps
        return out

    def test_guard_rejects_too_little_off_time(self):
        from custom_components.hvac_horizon.collector import has_enough_cycles

        self.assertFalse(has_enough_cycles(self._samples(40, 5, 5)))  # 25 off-samples, under 36

    def test_guard_rejects_too_few_runs(self):
        from custom_components.hvac_horizon.collector import has_enough_cycles

        self.assertFalse(has_enough_cycles(self._samples(20, 20, 2)))

    def test_guard_accepts_enough_on_off_and_runs(self):
        from custom_components.hvac_horizon.collector import has_enough_cycles

        self.assertTrue(has_enough_cycles(self._samples(20, 20, 4)))


class PowerMeanTests(unittest.TestCase):
    def test_interval_mean_is_preferred_over_counter_and_instant(self):
        from custom_components.hvac_horizon.collector import interval_power_kw

        a = Entry(0, 26, 30, 0, energy_kwh=10.0)
        b = Entry(300, 26, 30, 0, energy_kwh=10.1, power_mean_w=850.0)
        self.assertAlmostEqual(interval_power_kw(a, b), 0.85)

    def test_interval_mean_below_threshold_counts_as_off(self):
        from custom_components.hvac_horizon.collector import interval_power_kw

        a = Entry(0, 26, 30, 1100)
        b = Entry(300, 26, 30, 1100, power_mean_w=50.0)
        self.assertEqual(interval_power_kw(a, b), 0.0)
