import math
import unittest

from custom_components.hvac_horizon import Sample, fit
from custom_components.hvac_horizon.backfill import _find_cooldowns

K, C = 0.4, 1.5


def build_samples(n: int, step_min: float = 5.0) -> list[Sample]:
    """Synthetic room history: AC on for 40 min, off for 60 min, outdoor 30 C with a daily swing."""
    samples = []
    temp = 27.0
    dt_h = step_min / 60.0
    for i in range(n):
        outdoor = 30.0 + 2.0 * math.sin(2 * math.pi * i * step_min / (24 * 60))
        power_kw = 1.1 if (i % 20) < 8 else 0.0  # 8 steps on (40 min), 12 steps off
        nxt = temp + (K * (outdoor - temp) - C * power_kw) * dt_h
        samples.append(Sample(step_min, temp, nxt, outdoor, power_kw))
        temp = nxt
    return samples


class BackfillTests(unittest.TestCase):
    def test_fit_on_history_recovers_coefficients(self):
        samples = build_samples(400)
        result = fit(samples)
        self.assertAlmostEqual(result.k, K, delta=0.05)
        self.assertAlmostEqual(result.c, C, delta=0.1)

    def test_cooldowns_are_found(self):
        samples = build_samples(400)
        cooldowns = _find_cooldowns(samples, target_c=26.8, margin_c=0.2)  # duty cycle keeps the room near 26-30 C
        self.assertGreater(len(cooldowns), 0)
        for cd in cooldowns:
            self.assertGreaterEqual(cd.start_c, cd.target_c + 0.2)

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

        samples = build_samples(600)
        runs = backtest(samples, target_c=26.8, min_train=60, margin_c=0.2)
        predicted = [r for r in runs if r.predicted_min is not None]
        self.assertGreater(len(predicted), 0)
        # Synthetic data comes from the same model, so the error should stay small.
        self.assertLess(median_abs_error(runs), 10.0)

    def test_backtest_skips_runs_with_too_little_history(self):
        from custom_components.hvac_horizon.backfill import backtest

        samples = build_samples(120)
        runs = backtest(samples, target_c=26.8, min_train=10_000, margin_c=0.2)
        self.assertTrue(all(r.predicted_min is None for r in runs))

    def test_median_abs_error_handles_even_and_empty(self):
        from custom_components.hvac_horizon.backfill import BacktestRun, median_abs_error

        runs = [BacktestRun(0, 10, 12, 2.0), BacktestRun(1, 10, 15, 5.0), BacktestRun(2, 10, 9, -1.0), BacktestRun(3, 10, 20, 10.0)]
        self.assertAlmostEqual(median_abs_error(runs), 3.5)
        self.assertIsNone(median_abs_error([]))


class AccuracyPairsTests(unittest.TestCase):
    def test_pairs_hold_the_predicted_runs_with_their_errors(self):
        from custom_components.hvac_horizon.backfill import BacktestRun, accuracy_pairs, median_abs_error

        runs = [
            BacktestRun(0, 40.0, None, None),  # too little history: left out
            BacktestRun(10, 40.0, 50.0, 10.0),
            BacktestRun(20, 30.0, None, None),  # no prediction: left out
            BacktestRun(30, 60.0, 52.0, -8.0),
            BacktestRun(40, 25.0, 31.0, 6.0),
        ]
        self.assertEqual(
            accuracy_pairs(runs),
            [
                {"predicted_min": 50.0, "measured_min": 40.0, "error_min": 10.0},
                {"predicted_min": 52.0, "measured_min": 60.0, "error_min": -8.0},
                {"predicted_min": 31.0, "measured_min": 25.0, "error_min": 6.0},
            ],
        )
        self.assertEqual(median_abs_error(runs), 8.0)

    def test_pairs_keep_the_last_20_predicted_runs(self):
        from custom_components.hvac_horizon.backfill import BacktestRun, accuracy_pairs

        runs = [BacktestRun(i, 30.0, 30.0 + i, float(i)) for i in range(25)]
        pairs = accuracy_pairs(runs)
        self.assertEqual(len(pairs), 20)
        self.assertEqual(pairs[0]["predicted_min"], 35.0)  # run 5
        self.assertEqual(pairs[-1]["error_min"], 24.0)  # run 24

