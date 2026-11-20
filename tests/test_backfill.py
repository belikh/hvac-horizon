import math
import tempfile
import unittest
from pathlib import Path

from custom_components.hvac_horizon import Fit, Sample, fit
from custom_components.hvac_horizon.backfill import (
    Row,
    check_cooldowns,
    find_cooldowns,
    read_rows,
    samples_from_rows,
)

K, C = 0.4, 1.5


def build_rows(n: int, step_min: float = 5.0) -> list[Row]:
    """Synthetic room history: AC on for 40 min, off for 60 min, outdoor 30 C with a daily swing."""
    rows = []
    temp = 27.0
    dt_h = step_min / 60.0
    for i in range(n):
        outdoor = 30.0 + 2.0 * math.sin(2 * math.pi * i * step_min / (24 * 60))
        power_w = 1100.0 if (i % 20) < 8 else 0.0  # 8 steps on (40 min), 12 steps off
        rows.append(Row(f"t{i}", temp, outdoor, power_w))
        temp = temp + (K * (outdoor - temp) - C * power_w / 1000.0) * dt_h
    return rows


class BackfillTests(unittest.TestCase):
    def test_read_rows_skips_empty_cells(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h.csv"
            path.write_text(
                "time,room_c,outdoor_c,power_w\n"
                "t0,26.0,30.0,1000\n"
                "t1,,30.0,1000\n"
                "t2,25.5,30.5,0\n"
            )
            rows = read_rows(path)
        self.assertEqual([r.time for r in rows], ["t0", "t2"])

    def test_samples_pair_consecutive_rows(self):
        rows = [Row("t0", 26.0, 30.0, 1000.0), Row("t1", 25.8, 30.1, 0.0)]
        s = samples_from_rows(rows, 5.0)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0].temp_c, 26.0)
        self.assertEqual(s[0].temp_next_c, 25.8)
        self.assertAlmostEqual(s[0].power_kw, 1.0)

    def test_fit_on_history_recovers_coefficients(self):
        samples = samples_from_rows(build_rows(400), 5.0)
        result = fit(samples)
        self.assertAlmostEqual(result.k, K, delta=0.05)
        self.assertAlmostEqual(result.c, C, delta=0.1)

    def test_cooldowns_found_and_predicted(self):
        rows = build_rows(400)
        samples = samples_from_rows(rows, 5.0)
        cooldowns = find_cooldowns(samples, target_c=26.8, margin_c=0.2)  # duty cycle keeps the room near 26-30 C
        self.assertGreater(len(cooldowns), 0)
        checks = check_cooldowns(fit(samples), cooldowns)
        reachable = [c for c in checks if c.reachable]
        self.assertGreater(len(reachable), 0)
        for c in reachable:
            # The forecast uses the same constant-power model as the data, so errors should stay small.
            self.assertLess(abs(c.error_min), 15.0, c)

    def test_clamped_k_refits_c_alone(self):
        # Heat gain is negative in this data (unphysical), so k clamps to zero and c is refit alone.
        samples = []
        temp = 27.0
        for i in range(60):
            power = 1.0 if (i // 6) % 2 == 0 else 0.0
            nxt = temp + (-0.2 * (30.0 - temp) - 1.5 * power) * (5 / 60)
            samples.append(Sample(5.0, temp, nxt, 30.0, power))
            temp = nxt
        result = fit(samples)
        self.assertEqual(result.k, 0.0)
        self.assertGreater(result.c, 0.0)


if __name__ == "__main__":
    unittest.main()


class BacktestTests(unittest.TestCase):
    def test_backtest_uses_only_earlier_data(self):
        from custom_components.hvac_horizon.backfill import backtest, median_abs_error

        samples = samples_from_rows(build_rows(600), 5.0)
        runs = backtest(samples, target_c=26.8, min_train=60, margin_c=0.2)
        predicted = [r for r in runs if r.predicted_min is not None]
        self.assertGreater(len(predicted), 0)
        # Synthetic data comes from the same model, so the error should stay small.
        self.assertLess(median_abs_error(runs), 10.0)

    def test_backtest_skips_runs_with_too_little_history(self):
        from custom_components.hvac_horizon.backfill import backtest

        samples = samples_from_rows(build_rows(120), 5.0)
        runs = backtest(samples, target_c=26.8, min_train=10_000, margin_c=0.2)
        self.assertTrue(all(r.predicted_min is None for r in runs))

    def test_median_abs_error_handles_even_and_empty(self):
        from custom_components.hvac_horizon.backfill import BacktestRun, median_abs_error

        runs = [BacktestRun(0, 10, 12, 2.0), BacktestRun(1, 10, 15, 5.0), BacktestRun(2, 10, 9, -1.0), BacktestRun(3, 10, 20, 10.0)]
        self.assertAlmostEqual(median_abs_error(runs), 3.5)
        self.assertIsNone(median_abs_error([]))
