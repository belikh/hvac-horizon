import unittest

from custom_components.hvac_horizon.room_model.collector import (
    Entry,
    mode_interval_counts,
    price_slots,
    running_power_kw,
    samples_from_entries,
)


class HistoryEntriesTests(unittest.TestCase):
    def test_entries_built_from_histories(self):
        from custom_components.hvac_horizon.room_model.collector import entries_from_history

        rooms = [[(0.0, 26.0)], [(0.0, 25.0)]]
        outdoor = [(0.0, 30.0)]
        power = [(0.0, 1000.0), (300.0, 0.0)]
        entries = entries_from_history(rooms, outdoor, power, start=0.0, end=300.0, step=300.0)
        self.assertEqual(len(entries), 2)
        self.assertAlmostEqual(entries[0].room_c, 25.5)  # median of 26 and 25
        self.assertEqual(entries[1].power_w, 0.0)

    def test_grid_time_skipped_when_an_input_is_missing(self):
        from custom_components.hvac_horizon.room_model.collector import entries_from_history

        entries = entries_from_history([[(400.0, 26.0)]], [(0.0, 30.0)], [(0.0, 900.0)], start=0.0, end=600.0, step=300.0)
        self.assertEqual([e.t for e in entries], [600.0])

    def test_no_rooms_gives_nothing(self):
        from custom_components.hvac_horizon.room_model.collector import entries_from_history

        self.assertEqual(entries_from_history([], [(0.0, 30.0)], [(0.0, 900.0)], 0.0, 300.0), [])


class ResampleTests(unittest.TestCase):
    def test_last_known_value_on_grid(self):
        from custom_components.hvac_horizon.room_model.collector import _resample_states

        points = [(0.0, 1000.0), (420.0, 0.0)]
        grid = _resample_states(points, start=0.0, end=600.0, step=300.0)
        self.assertEqual(grid, [(0.0, 1000.0), (300.0, 1000.0), (600.0, 0.0)])

    def test_none_before_first_state(self):
        from custom_components.hvac_horizon.room_model.collector import _resample_states

        grid = _resample_states([(400.0, 5.0)], start=0.0, end=600.0, step=300.0)
        self.assertEqual(grid[0], (0.0, None))
        self.assertEqual(grid[2], (600.0, 5.0))

    def test_unordered_points_are_sorted(self):
        from custom_components.hvac_horizon.room_model.collector import _resample_states

        grid = _resample_states([(300.0, 2.0), (0.0, 1.0)], start=0.0, end=300.0, step=300.0)
        self.assertEqual(grid, [(0.0, 1.0), (300.0, 2.0)])

    def test_rejects_bad_grid(self):
        from custom_components.hvac_horizon.room_model.collector import _resample_states

        with self.assertRaises(ValueError):
            _resample_states([], start=10.0, end=0.0, step=300.0)


class CsvExportTests(unittest.TestCase):
    def test_csv_has_header_and_sorted_rows(self):
        from custom_components.hvac_horizon.room_model.collector import entries_to_csv

        entries = [Entry(600, 25.8, 30.0, 0), Entry(0, 26.0, 30.0, 1000)]
        text = entries_to_csv(entries)
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "time,room_c,outdoor_c,power_w")
        self.assertEqual(lines[1], "0,26.00,30.00,1000")
        self.assertEqual(lines[2], "600,25.80,30.00,0")


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
        from custom_components.hvac_horizon.room_model.collector import _interval_power_kw

        # 0.1 kWh over 5 minutes is 1.2 kW, even though the instantaneous reading was 0.
        a = Entry(0, 26, 30, 0, energy_kwh=10.0)
        b = Entry(300, 26, 30, 0, energy_kwh=10.1)
        self.assertAlmostEqual(_interval_power_kw(a, b), 1.2)

    def test_energy_counter_reset_falls_back_to_power(self):
        from custom_components.hvac_horizon.room_model.collector import _interval_power_kw

        a = Entry(0, 26, 30, 1100, energy_kwh=50.0)
        b = Entry(300, 26, 30, 1100, energy_kwh=0.2)  # counter reset
        self.assertAlmostEqual(_interval_power_kw(a, b), 1.1)

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
        from custom_components.hvac_horizon.room_model.model import Sample

        out = []
        for _ in range(runs):
            out += [Sample(5.0, 26.0, 25.9, 30.0, 1.0)] * on_steps
            out += [Sample(5.0, 25.9, 25.9, 30.0, 0.0)] * off_steps
        return out

    def test_guard_rejects_too_little_off_time(self):
        from custom_components.hvac_horizon.room_model.collector import has_enough_cycles

        self.assertFalse(has_enough_cycles(self._samples(40, 5, 5)))  # 25 off-samples, under 36

    def test_guard_rejects_too_few_runs(self):
        from custom_components.hvac_horizon.room_model.collector import has_enough_cycles

        self.assertFalse(has_enough_cycles(self._samples(20, 20, 2)))

    def test_guard_accepts_enough_on_off_and_runs(self):
        from custom_components.hvac_horizon.room_model.collector import has_enough_cycles

        self.assertTrue(has_enough_cycles(self._samples(20, 20, 4)))


class PowerMeanTests(unittest.TestCase):
    def test_interval_mean_is_preferred_over_counter_and_instant(self):
        from custom_components.hvac_horizon.room_model.collector import _interval_power_kw

        a = Entry(0, 26, 30, 0, energy_kwh=10.0)
        b = Entry(300, 26, 30, 0, energy_kwh=10.1, power_mean_w=850.0)
        self.assertAlmostEqual(_interval_power_kw(a, b), 0.85)

    def test_interval_mean_below_threshold_counts_as_off(self):
        from custom_components.hvac_horizon.room_model.collector import _interval_power_kw

        a = Entry(0, 26, 30, 1100)
        b = Entry(300, 26, 30, 1100, power_mean_w=50.0)
        self.assertEqual(_interval_power_kw(a, b), 0.0)


class RoomSamplesTests(unittest.TestCase):
    def test_second_power_becomes_the_ac_2_device(self):
        from custom_components.hvac_horizon.room_model.collector import Entry, room_samples_from_entries

        a = Entry(t=0.0, room_c=24.0, outdoor_c=30.0, power_w=1200.0, power_2_w=800.0)
        b = Entry(t=300.0, room_c=23.8, outdoor_c=30.0, power_w=1200.0, power_2_w=800.0)
        (sample,) = room_samples_from_entries([a, b])
        self.assertEqual(sample.minutes, 5.0)
        self.assertAlmostEqual(sample.device_kw["ac"], 1.2)
        self.assertAlmostEqual(sample.device_kw["ac_2"], 0.8)

    def test_gap_breaks_the_pair(self):
        from custom_components.hvac_horizon.room_model.collector import Entry, room_samples_from_entries

        a = Entry(t=0.0, room_c=24.0, outdoor_c=30.0, power_w=1200.0)
        b = Entry(t=1800.0, room_c=23.8, outdoor_c=30.0, power_w=1200.0)
        self.assertEqual(room_samples_from_entries([a, b]), [])


import math

from custom_components.hvac_horizon.room_model.tpi import learn_room_rates


def _series(t0, n, ac_mode, k=0.3, g=0.2, c=1.4, warm_kw=None):
    """Readings 5 minutes apart. The AC runs 6 steps on and 6 off. While on it is in `ac_mode`.

    With `warm_kw` set, running intervals warm the room at warm_kw * 2 K/h instead of cooling it (a heating AC).
    """
    temp, out = 24.0, []
    for i in range(n):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        on = (i // 6) % 2 == 0
        out.append(Entry(t=t0 + i * 300, room_c=temp, outdoor_c=outdoor, power_w=1000.0 if on else 0.0, mode=ac_mode if on else "off"))
        effect = (2.0 * warm_kw if warm_kw else -c) if on else 0.0
        temp += (k * (outdoor - temp) + g + effect) * (5 / 60)
    return out


class ModeLabelTests(unittest.TestCase):
    def test_heat_mode_power_does_not_change_the_cooling_coefficient(self):
        cool = _series(0, 300, "cool")
        heat = _series(10**6, 300, "heat", warm_kw=1.0)
        base = learn_room_rates(samples_from_entries(cool))
        mixed = learn_room_rates(samples_from_entries(cool + heat))
        self.assertAlmostEqual(mixed.c, base.c, places=6)
        self.assertEqual(mixed.samples_on, base.samples_on)
        self.assertAlmostEqual(base.c, 1.4, delta=0.05)

    def test_dry_mode_power_is_excluded_from_the_cooling_fit(self):
        cool = _series(0, 300, "cool")
        dry = _series(10**6, 300, "dry", warm_kw=0.2)
        base = learn_room_rates(samples_from_entries(cool))
        mixed = learn_room_rates(samples_from_entries(cool + dry))
        self.assertAlmostEqual(mixed.c, base.c, places=6)
        self.assertEqual(mixed.samples_on, base.samples_on)

    def test_pure_cooling_series_fits_as_before(self):
        fit = learn_room_rates(samples_from_entries(_series(0, 400, "cool")))
        self.assertAlmostEqual(fit.k, 0.3, delta=0.02)
        self.assertAlmostEqual(fit.c, 1.4, delta=0.05)

    def test_entries_without_a_mode_count_as_cooling(self):
        legacy = [Entry(t=e.t, room_c=e.room_c, outdoor_c=e.outdoor_c, power_w=e.power_w) for e in _series(0, 100, "cool")]
        self.assertEqual(len(samples_from_entries(legacy)), 99)

    def test_interval_counts_per_mode(self):
        entries = _series(0, 24, "cool") + _series(10**6, 24, "heat", warm_kw=1.0) + _series(2 * 10**6, 24, "dry", warm_kw=0.2)
        counts = mode_interval_counts(entries)
        self.assertGreater(counts["cool"], 0)
        self.assertGreater(counts["heat"], 0)
        self.assertGreater(counts["dry"], 0)
        self.assertEqual(len(samples_from_entries(entries)), counts["off"] + counts["cool"])
