import math
import random
import unittest

from custom_components.hvac_horizon.room_model.corrections import Corrections, apply, learn_corrections
from custom_components.hvac_horizon.room_model.forecast import PriceSlot, forecast
from custom_components.hvac_horizon.room_model.mode_models import dry_forecast, _fit_through_zero, heat_forecast, learn_ac_heat, learn_dry
from custom_components.hvac_horizon.room_model.model import Fit, NotEnoughData, Sample

K, G = 0.12, 0.3


def heat_samples(c_heat=2.5, n=60, noise=0.0, seed=1):
    rng = random.Random(seed)
    out, room = [], 17.0
    for i in range(n):
        outdoor, power = 5.0 + (i % 7), 0.8 + 0.4 * (i % 3)
        rate = K * (outdoor - room) + G + c_heat * power + rng.gauss(0.0, noise)
        nxt = room + rate * 5.0 / 60.0
        out.append(Sample(5.0, room, nxt, outdoor, power))
        room = nxt if nxt < 23.0 else 17.0
    return out


class HeatModelTests(unittest.TestCase):
    def test_ac_heat_rate_is_recovered(self):
        found = learn_ac_heat(heat_samples(noise=0.2), K, G)
        self.assertAlmostEqual(found.rate, 2.5, delta=0.1)
        self.assertEqual(found.samples, 60)
        self.assertGreater(found.stderr, 0.0)

    def test_too_few_heat_intervals_raise(self):
        with self.assertRaises(NotEnoughData):
            learn_ac_heat(heat_samples(n=5), K, G)

    def test_heat_forecast_matches_a_minute_by_minute_room(self):
        rate, room, target, outdoor = 3.0, 17.0, 21.0, 6.0
        found = heat_forecast(K, G, rate, 1.5, room, target, outdoor, [PriceSlot(0.0, 1440.0, 0.3)])
        minutes, temp = 0, room
        while temp < target:
            temp += (K * (outdoor - temp) + G + rate) / 60.0
            minutes += 1
        self.assertTrue(found.reachable)
        self.assertAlmostEqual(found.minutes, minutes, delta=2.0)
        self.assertAlmostEqual(found.energy_kwh, 1.5 * found.minutes / 60.0, places=6)
        self.assertAlmostEqual(found.cost, found.energy_kwh * 0.3, places=6)

    def test_heat_forecast_without_power_has_minutes_and_no_energy(self):
        found = heat_forecast(K, G, 3.0, None, 17.0, 21.0, 6.0, [])
        self.assertTrue(found.reachable)
        self.assertGreater(found.minutes, 0.0)
        self.assertIsNone(found.energy_kwh)
        self.assertIsNone(found.cost)

    def test_heat_forecast_at_the_target_is_zero(self):
        self.assertEqual(heat_forecast(K, G, 3.0, 1.0, 21.0, 21.0, 6.0, []).minutes, 0.0)

    def test_a_weak_heater_on_a_cold_day_is_unreachable_and_names_where_the_room_settles(self):
        found = heat_forecast(K, G, 1.0, 1.0, 15.0, 21.0, 0.0, [])
        self.assertFalse(found.reachable)
        self.assertAlmostEqual(found.equilibrium_c, (G + 1.0) / K, places=3)
        self.assertIn("settles at 10.8 C", found.reason)

    def test_a_falling_outdoor_series_lengthens_the_heat_up(self):
        steady = heat_forecast(K, G, 4.0, 1.0, 17.0, 21.0, 8.0, [])
        falling = heat_forecast(K, G, 4.0, 1.0, 17.0, 21.0, 8.0, [], outdoor_series=lambda m: 8.0 - m / 30.0)
        self.assertGreater(falling.minutes, steady.minutes)


class DryModelTests(unittest.TestCase):
    def test_dry_rate_and_forecast(self):
        rng = random.Random(2)
        found = learn_dry([(0.6, -6.0 + rng.gauss(0.0, 0.5)) for _ in range(40)])
        self.assertAlmostEqual(found.rate, 6.0, delta=0.3)
        result = dry_forecast(found.rate, 70.0, 58.0, 0.6, [PriceSlot(0.0, 1440.0, 0.25)])
        self.assertAlmostEqual(result.minutes, 120.0, delta=8.0)
        self.assertAlmostEqual(result.energy_kwh, 0.6 * result.minutes / 60.0, places=6)
        self.assertAlmostEqual(result.cost, result.energy_kwh * 0.25, places=6)

    def test_humidity_that_rises_in_dry_mode_gives_no_model(self):
        with self.assertRaises(NotEnoughData):
            learn_dry([(0.6, 1.0)] * 20)

    def test_dry_forecast_edges(self):
        self.assertEqual(dry_forecast(5.0, 55.0, 58.0, 0.6, []).minutes, 0.0)
        self.assertFalse(dry_forecast(0.5, 90.0, 58.0, 0.6, []).reachable)
        self.assertIsNone(dry_forecast(5.0, 70.0, 58.0, None, []).energy_kwh)
        self.assertIsNone(dry_forecast(5.0, 70.0, 58.0, 0.6, []).cost)

    def test_fit_through_zero_needs_variation(self):
        with self.assertRaises(NotEnoughData):
            _fit_through_zero([(0.0, 1.0)] * 20)


def room_samples(n, s=0.0, c_out=0.0, c_rh=0.0, noise=0.05, seed=4, with_solar=True, with_rh=True):
    """Readings of a room with base fit k=0.1, c=4, g=0.2 and the given true corrections."""
    rng = random.Random(seed)
    out, room = [], 26.0
    for i in range(n):
        hour = (i / 12.0) % 24.0
        outdoor = 28.0 + 7.0 * math.sin((hour - 9.0) / 24.0 * 2 * math.pi) + rng.uniform(-1.0, 1.0)
        solar = max(0.0, math.sin((hour - 6.0) / 12.0 * math.pi)) * rng.choice([0.3, 1.0])
        rh = 50.0 + 20.0 * math.sin(i / 97.0)
        power = 1.0 if (i // 9) % 2 else 0.0
        c = 4.0 + c_out * (outdoor - 30.0) + c_rh * (rh - 50.0)
        rate = 0.1 * (outdoor - room) - c * power + 0.2 + s * solar + rng.gauss(0.0, noise)
        nxt = room + rate * 5.0 / 60.0
        out.append(Sample(5.0, room, nxt, outdoor, power, solar if with_solar else None, rh if with_rh else None))
        room = min(30.0, max(21.0, nxt))
    return out


BASE = Fit(k=0.1, c=4.0, g=0.2)


class CorrectionsTests(unittest.TestCase):
    def test_a_room_that_fits_the_base_model_gets_no_correction(self):
        self.assertEqual(learn_corrections(room_samples(1200), BASE).terms, ())

    def test_solar_gain_is_found(self):
        found = learn_corrections(room_samples(1200, s=1.5), BASE)
        self.assertEqual(found.terms, ("solar",))
        self.assertAlmostEqual(found.s, 1.5, delta=0.1)
        self.assertGreater(found.improvement_pct, 3.0)

    def test_weaker_cooling_on_hot_days_is_found(self):
        found = learn_corrections(room_samples(1200, c_out=-0.15), BASE)
        self.assertIn("outdoor", found.terms)
        self.assertAlmostEqual(found.c_out, -0.15, delta=0.03)

    def test_weaker_cooling_in_humid_air_is_found(self):
        found = learn_corrections(room_samples(1200, c_rh=-0.05), BASE)
        self.assertIn("humidity", found.terms)
        self.assertAlmostEqual(found.c_rh, -0.05, delta=0.01)

    def test_all_three_are_found_together(self):
        found = learn_corrections(room_samples(3000, s=1.2, c_out=-0.12, c_rh=-0.04), BASE)
        self.assertEqual(set(found.terms), {"solar", "outdoor", "humidity"})

    def test_a_term_without_readings_is_left_out(self):
        found = learn_corrections(room_samples(1200, s=1.5, c_rh=-0.05, with_solar=False, with_rh=False), BASE)
        self.assertNotIn("solar", found.terms)
        self.assertNotIn("humidity", found.terms)

    def test_too_few_readings_give_no_correction(self):
        self.assertEqual(learn_corrections(room_samples(100, s=1.5), BASE).terms, ())

    def test_a_correction_with_the_wrong_sign_is_left_out(self):
        self.assertNotIn("solar", learn_corrections(room_samples(1200, s=-1.5), BASE).terms)
        self.assertNotIn("outdoor", learn_corrections(room_samples(1200, c_out=0.15), BASE).terms)

    def test_apply_moves_g_and_c_and_keeps_c_positive(self):
        corrections = Corrections(s=1.0, c_out=-0.2, c_rh=-0.05)
        at = apply(BASE, corrections, 35.0, 0.5, 70.0)
        self.assertAlmostEqual(at.g, 0.7)
        self.assertAlmostEqual(at.c, 4.0 - 1.0 - 1.0)
        self.assertAlmostEqual(apply(BASE, corrections, 60.0, 0.0, None).c, 0.8)
        self.assertIs(apply(BASE, None, 35.0, 0.5, 70.0), BASE)
        self.assertIs(apply(BASE, Corrections(), 35.0, 0.5, 70.0), BASE)

    def test_the_forecast_is_longer_in_the_sun(self):
        corrections = Corrections(s=2.0)
        shade = forecast(BASE, 27.0, 24.0, 32.0, 1.0, [])
        sun = forecast(BASE, 27.0, 24.0, 32.0, 1.0, [], fit_at=lambda minute, outdoor: apply(BASE, corrections, outdoor, 1.0, None))
        self.assertGreater(sun.minutes, shade.minutes)
