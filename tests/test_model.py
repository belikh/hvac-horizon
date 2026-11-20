import math
import random
import unittest

from custom_components.hvac_horizon import Sample, fit
from custom_components.hvac_horizon.model import NotEnoughData

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

    def assertAbs(self, value, target, tol):
        self.assertLessEqual(abs(value - target), tol, f"{value} not within {tol} of {target}")


if __name__ == "__main__":
    unittest.main()
