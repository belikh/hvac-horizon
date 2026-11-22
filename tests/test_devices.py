import math
import random
import unittest

from custom_components.hvac_horizon.room_model.devices import COOL, HEAT, RoomSample, fit_room
from custom_components.hvac_horizon.room_model.model import NotEnoughData

K, G = 0.3, 0.2
C_AC, H_HEAT = 1.4, 0.9


def simulate(n, seed=3, ac_cycle=6, heat_cycle=None):
    rng = random.Random(seed)
    temp = 24.0
    out = []
    for i in range(n):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0) + rng.gauss(0, 0.05)
        ac = 1.0 if (i // ac_cycle) % 2 == 0 else 0.0
        heat = 0.0
        if heat_cycle is not None:
            heat = 0.8 if (i // heat_cycle) % 2 == 1 else 0.0
        dt_h = 5 / 60.0
        nxt = temp + (K * (outdoor - temp) + G - C_AC * ac + H_HEAT * heat) * dt_h
        out.append(RoomSample(5.0, temp, nxt, outdoor, {"ac": ac, "heater": heat}))
        temp = nxt
    return out


class RoomFitTests(unittest.TestCase):
    def test_recovers_cooler_and_heater_coefficients(self):
        samples = simulate(800, heat_cycle=17)
        result = fit_room(samples, {"ac": COOL, "heater": HEAT})
        self.assertAlmostEqual(result.coef["ac"], C_AC, delta=0.05)
        self.assertAlmostEqual(result.coef["heater"], H_HEAT, delta=0.05)
        self.assertAlmostEqual(result.k, K, delta=0.03)

    def test_device_that_never_ran_does_not_block_the_fit(self):
        samples = simulate(800)  # heater always off
        result = fit_room(samples, {"ac": COOL, "heater": HEAT})
        self.assertAlmostEqual(result.coef["ac"], C_AC, delta=0.05)
        self.assertEqual(result.coef["heater"], 0.0)

    def test_heater_coefficient_never_goes_negative(self):
        # Data where the "heater" column anti-correlates with temperature change, which is unphysical.
        samples = []
        temp = 24.0
        for i in range(200):
            outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
            heat = 0.8 if (i // 17) % 2 else 0.0
            nxt = temp + (K * (outdoor - temp) + G - C_AC * (1.0 if i % 6 < 3 else 0.0) - 0.5 * heat) * (5 / 60)
            samples.append(RoomSample(5.0, temp, nxt, outdoor, {"ac": 1.0 if i % 6 < 3 else 0.0, "heater": heat}))
            temp = nxt
        result = fit_room(samples, {"ac": COOL, "heater": HEAT})
        self.assertGreaterEqual(result.coef["heater"], 0.0)
        self.assertGreaterEqual(result.coef["ac"], 0.0)

    def test_too_few_samples_raises(self):
        with self.assertRaises(NotEnoughData):
            fit_room(simulate(5), {"ac": COOL})


if __name__ == "__main__":
    unittest.main()
