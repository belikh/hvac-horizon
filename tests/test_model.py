import math
import random
import unittest

from custom_components.hvac_horizon import Sample, fit
from custom_components.hvac_horizon.model import MAX_CONDITION, NotEnoughData, fit_confidence

K_TRUE = 0.4  # 1/h
C_TRUE = 1.5  # K/h per kW


def simulate(n: int, minutes: float = 5.0, seed: int = 1, noise: float = 0.0) -> list[Sample]:
    """Generate samples from the true model with AC power switching on and off."""
    rng = random.Random(seed)
    samples = []
    temp = 28.0
    for i in range(n):
        outdoor = 30.0 + 2.0 * math.sin(i / 20.0)
        power = 1.1 if (i // 6) % 2 == 0 else 0.0  # 30 min on, 30 min off
        dt_h = minutes / 60.0
        nxt = temp + (K_TRUE * (outdoor - temp) - C_TRUE * power) * dt_h
        nxt += rng.gauss(0, noise)
        samples.append(Sample(minutes, temp, nxt, outdoor, power))
        temp = nxt
    return samples


class FitTests(unittest.TestCase):
    def test_recovers_k_and_c_from_clean_data(self):
        result = fit(simulate(200))
        self.assertAlmostEqual(result.k, K_TRUE, places=6)
        self.assertAlmostEqual(result.c, C_TRUE, places=6)
        self.assertEqual(result.samples, 200)

    def test_recovers_k_and_c_within_tolerance_with_noise(self):
        result = fit(simulate(2000, noise=0.02))
        self.assertAbs(result.k, K_TRUE, 0.05)
        self.assertAbs(result.c, C_TRUE, 0.1)

    def test_rejects_too_few_samples(self):
        with self.assertRaises(NotEnoughData):
            fit(simulate(5))

    def test_rejects_constant_power(self):
        samples = [Sample(5, 26.0 - i * 0.05, 26.0 - (i + 1) * 0.05, 30.0, 1.0) for i in range(30)]
        with self.assertRaises(NotEnoughData):
            fit(samples)

    def test_clamps_negative_coefficients_to_zero(self):
        # Room warms while AC runs: the fit must not return a negative cooling effect.
        samples = [
            Sample(5, 24.0, 24.2, 30.0 + 2.0 * math.sin(i), 1.0 if i % 2 else 0.0) for i in range(30)
        ]  # outdoor varies, so k is identified
        result = fit(samples)
        self.assertGreaterEqual(result.c, 0.0)
        self.assertGreaterEqual(result.k, 0.0)

    def test_recovers_constant_gain(self):
        samples = []
        temp = 26.0
        for i in range(600):
            outdoor = 28.0 + 4.0 * math.sin(i / 7.0)
            power = 1.0 if (i // 6) % 2 == 0 else 0.0
            nxt = temp + (0.3 * (outdoor - temp) - 1.2 * power + 0.5) * (5 / 60)
            samples.append(Sample(5.0, temp, nxt, outdoor, power))
            temp = nxt
        result = fit(samples)
        self.assertAlmostEqual(result.k, 0.3, delta=0.02)
        self.assertAlmostEqual(result.c, 1.2, delta=0.05)
        self.assertAlmostEqual(result.g, 0.5, delta=0.05)

    def test_rejects_flat_outdoor(self):
        samples = [Sample(5.0, 26.0, 25.9, 30.0, 1.0 if i % 2 else 0.0) for i in range(60)]
        with self.assertRaises(NotEnoughData):
            fit(samples)

    def test_gross_outliers_are_dropped_and_counted(self):
        clean = fit(simulate(400, noise=0.02))
        samples = simulate(400, noise=0.02)
        for i in range(10, 400, 20):  # 20 samples, 5 percent, each with a 4 K reading error
            s = samples[i]
            samples[i] = Sample(s.minutes, s.temp_c, s.temp_next_c + 4.0, s.outdoor_c, s.power_kw)
        result = fit(samples)
        self.assertEqual(result.outliers_dropped, 20)
        self.assertEqual(result.samples, 400)
        self.assertLessEqual(abs(result.k - clean.k), 0.05 * clean.k)
        self.assertLessEqual(abs(result.c - clean.c), 0.05 * clean.c)
        self.assertLessEqual(abs(result.g - clean.g), 0.05)  # K/h; the true g is 0
        self.assertEqual(clean.outliers_dropped, 0)

    def test_outlier_drop_is_capped_at_ten_percent(self):
        samples = simulate(200, noise=0.02)
        for i in range(0, 200, 4):  # 25 percent bad
            s = samples[i]
            samples[i] = Sample(s.minutes, s.temp_c, s.temp_next_c + 4.0, s.outdoor_c, s.power_kw)
        self.assertLessEqual(fit(samples).outliers_dropped, 20)

    def test_clean_data_drops_nothing(self):
        self.assertEqual(fit(simulate(200)).outliers_dropped, 0)

    def test_near_collinear_series_is_rejected_with_the_reason(self):
        rng = random.Random(2)
        samples = []
        for i in range(300):  # outdoor tracks AC power, so k, c and g cannot be separated
            power = 1.0 if (i // 6) % 2 == 0 else 0.0
            outdoor = 28.0 + 4.0 * power + 0.01 * rng.gauss(0, 1)
            nxt = 26.0 + (0.4 * (outdoor - 26.0) - 1.5 * power + 0.01 * rng.gauss(0, 1)) * 5 / 60
            samples.append(Sample(5.0, 26.0, nxt, outdoor, power))
        with self.assertRaises(NotEnoughData) as ctx:
            fit(samples)
        self.assertIn("nearly collinear", str(ctx.exception))

    def test_synthetic_series_pass_the_condition_limit(self):
        for n in (30, 60, 200, 2000):
            self.assertLess(fit(simulate(n, noise=0.02)).condition, MAX_CONDITION)

    def test_unidentified_c_is_rejected(self):
        rng = random.Random(5)
        samples = []
        temp = 26.0
        for i in range(200):  # the AC flips on and off but the room ignores it
            outdoor = 30.0 + 3.0 * math.sin(i / 11.0)
            power = 1.0 if (i // 6) % 2 == 0 else 0.0
            nxt = temp + (0.4 * (outdoor - temp) - 0.002 * power) * 5 / 60 + rng.gauss(0, 0.05)
            samples.append(Sample(5.0, temp, nxt, outdoor, power))
            temp = nxt
        with self.assertRaises(NotEnoughData) as ctx:
            fit(samples)
        self.assertIn("not identified", str(ctx.exception))

    def test_standard_errors_shrink_with_more_samples(self):
        small = fit(simulate(100, noise=0.02))
        large = fit(simulate(1600, noise=0.02))
        for name in ("k_stderr", "c_stderr", "g_stderr"):
            self.assertLess(getattr(large, name), getattr(small, name), name)
        self.assertAlmostEqual(large.residual_k_per_h, 0.02 * 12, delta=0.03)  # noise 0.02 K per 5 minutes is 0.24 K/h

    def test_confidence_label_follows_relative_error_of_c(self):
        from custom_components.hvac_horizon.model import Fit

        self.assertEqual(fit_confidence(Fit(k=0.4, c=1.0, c_stderr=0.05)), "high")
        self.assertEqual(fit_confidence(Fit(k=0.4, c=1.0, c_stderr=0.2)), "medium")
        self.assertEqual(fit_confidence(Fit(k=0.4, c=1.0, c_stderr=0.5)), "low")
        self.assertIsNone(fit_confidence(Fit(k=0.4, c=1.0)))

    def assertAbs(self, value, target, tol):
        self.assertLessEqual(abs(value - target), tol, f"{value} not within {tol} of {target}")


if __name__ == "__main__":
    unittest.main()


class SolverTests(unittest.TestCase):
    def test_solve_satisfies_random_systems(self):
        from custom_components.hvac_horizon.model import _solve

        rng = random.Random(7)
        for _ in range(500):
            n = rng.choice([1, 2, 3])
            a = [[rng.uniform(-50.0, 50.0) for _ in range(n)] for _ in range(n)]
            for i in range(n):
                a[i][i] += 200.0  # diagonally dominant, so the system has one solution
            b = [rng.uniform(-100.0, 100.0) for _ in range(n)]
            x = _solve(a, b)
            for row, target in zip(a, b):
                self.assertAlmostEqual(sum(v * xi for v, xi in zip(row, x)), target, places=6)

    def test_clamped_fit_is_the_best_fit_with_k_and_c_at_or_above_zero(self):
        """The clamped solver matches a search over every set of columns it may drop."""
        from custom_components.hvac_horizon.model import _least_squares, _residuals, _solve_clamped

        def sse(rows, y, active, coef):
            return sum(r * r for r in _residuals(rows, y, active, coef))

        rng = random.Random(11)
        for _ in range(300):
            k, c, g = rng.uniform(-0.2, 0.4), rng.uniform(-2.0, 4.0), rng.uniform(-1.0, 1.0)
            rows = [[rng.uniform(-5.0, 15.0), -rng.choice([0.0, rng.uniform(0.3, 2.0)]), 1.0] for _ in range(60)]
            y = [k * r[0] + c * r[1] + g + rng.gauss(0.0, 0.3) for r in rows]
            active, coef = _solve_clamped(rows, y)
            best = None
            for subset in ([0, 1, 2], [1, 2], [0, 2], [2]):
                found = _least_squares(rows, y, subset)
                if all(v >= 0 for idx, v in zip(subset, found) if idx in (0, 1)):
                    error = sse(rows, y, subset, found)
                    best = error if best is None else min(best, error)
            self.assertLessEqual(sse(rows, y, active, coef), best * (1 + 1e-9) + 1e-9)
