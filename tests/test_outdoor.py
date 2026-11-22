import unittest
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from custom_components.hvac_horizon.engine.control import precool_start_in_min
from custom_components.hvac_horizon.engine.features import mpc_heat_plan, mpc_plan
from custom_components.hvac_horizon.room_model.forecast import forecast, forecast_range
from custom_components.hvac_horizon.room_model.model import Fit
from custom_components.hvac_horizon.room_model.outdoor import (
    ANCHOR_DECAY_H,
    BIAS_LIMIT_K,
    BIAS_STEP_MAX_K,
    OutdoorBias,
    HourlyPoint,
    build_series,
    condition_now,
    night_low,
    _parse_time,
    parse_hourly,
    temperature_now,
)
from custom_components.hvac_horizon.room_model.solar_model import CLOUD_FACTOR_CLOUDY, condition_factor
from custom_components.hvac_horizon.room_model.tpi import heat_up_minutes

BRISBANE = ZoneInfo("Australia/Brisbane")  # UTC+10, no daylight saving
START = datetime(2026, 11, 21, 18, 0, tzinfo=BRISBANE).timestamp()
FIT = Fit(k=0.4, c=1.5, samples=200)


def hourly(temps, first=START, condition=None):
    return [HourlyPoint(first + 3600.0 * i, t, condition) for i, t in enumerate(temps)]


class ParseTests(unittest.TestCase):
    def test_datetime_without_offset_is_local_time(self):
        parsed = _parse_time("2026-11-21T18:00:00", BRISBANE)
        self.assertEqual(parsed.timestamp(), START)

    def test_datetime_with_offset_keeps_its_offset(self):
        parsed = _parse_time("2026-11-21T08:00:00+00:00", BRISBANE)
        self.assertEqual(parsed.timestamp(), START)

    def test_datetime_object_without_tzinfo_is_local_time(self):
        self.assertEqual(_parse_time(datetime(2026, 11, 21, 18, 0), BRISBANE).timestamp(), START)

    def test_unparseable_values_give_none(self):
        self.assertIsNone(_parse_time("tomorrow", BRISBANE))
        self.assertIsNone(_parse_time(None, BRISBANE))

    def test_hourly_skips_items_without_time_or_temperature_and_sorts(self):
        items = [
            {"datetime": "2026-11-21T19:00:00", "temperature": 26.0, "condition": "cloudy"},
            {"datetime": "2026-11-21T18:00:00", "temperature": 28.0},
            {"temperature": 31.0},
            {"datetime": "2026-11-21T20:00:00", "temperature": None},
            {"datetime": "2026-11-21T21:00:00", "temperature": True},
        ]
        points = parse_hourly(items, BRISBANE)
        self.assertEqual([p.temp_c for p in points], [28.0, 26.0])
        self.assertEqual([p.condition for p in points], [None, "cloudy"])


class SeriesTests(unittest.TestCase):
    def test_linear_between_hours(self):
        series = build_series(hourly([28.0, 24.0, 24.0]), START, None)
        self.assertAlmostEqual(series(0), 28.0)
        self.assertAlmostEqual(series(30), 26.0)
        self.assertAlmostEqual(series(60), 24.0)

    def test_value_is_held_past_the_last_hour(self):
        series = build_series(hourly([28.0, 24.0]), START, None)
        self.assertAlmostEqual(series(5000), 24.0)

    def test_anchor_starts_at_the_sensor_and_decays_to_the_forecast(self):
        series = build_series(hourly([28.0] * 8), START, 30.0)
        self.assertAlmostEqual(series(0), 30.0)
        self.assertAlmostEqual(series(ANCHOR_DECAY_H * 30), 29.0)
        self.assertAlmostEqual(series(ANCHOR_DECAY_H * 60), 28.0)
        self.assertAlmostEqual(series(ANCHOR_DECAY_H * 60 + 120), 28.0)

    def test_forecast_that_starts_in_the_past_is_read_at_now(self):
        # First hour was 90 minutes ago at 20 C, next hours 22 and 26. At now the forecast is 24 C.
        points = hourly([20.0, 22.0, 26.0], first=START - 5400.0)
        series = build_series(points, START, 25.0)
        self.assertAlmostEqual(series(0), 25.0)
        self.assertAlmostEqual(series(60), 26.0 + 1.0 * (1 - 60 / 180), places=6)  # forecast end value plus the decaying offset

    def test_forecast_entirely_in_the_past_gives_none(self):
        self.assertIsNone(build_series(hourly([20.0, 21.0], first=START - 36000.0), START, 25.0))

    def test_no_points_gives_none(self):
        self.assertIsNone(build_series([], START, 25.0))

    def test_single_point_is_constant(self):
        series = build_series(hourly([21.0], first=START + 60.0), START, None)
        self.assertAlmostEqual(series(0), 21.0)
        self.assertAlmostEqual(series(600), 21.0)

    def test_negative_minutes_read_the_start(self):
        series = build_series(hourly([28.0, 24.0]), START, None)
        self.assertAlmostEqual(series(-5), 28.0)

    def test_temperature_now_interpolates_and_rejects_times_outside_the_span(self):
        points = hourly([28.0, 24.0])
        self.assertAlmostEqual(temperature_now(points, START + 1800), 26.0)
        self.assertIsNone(temperature_now(points, START - 1))
        self.assertIsNone(temperature_now(points, START + 7200))

    def test_condition_now_takes_the_entry_at_or_before_now(self):
        points = [HourlyPoint(START + 3600.0 * i, 20.0, c) for i, c in enumerate(["sunny", "cloudy", "rainy"])]
        self.assertEqual(condition_now(points, START + 5000), "cloudy")
        self.assertEqual(condition_now(points, START - 100), "sunny")
        self.assertIsNone(condition_now([], START))

    def test_condition_factor(self):
        self.assertEqual(condition_factor("cloudy"), CLOUD_FACTOR_CLOUDY)
        self.assertIsNone(condition_factor("tornado"))
        self.assertIsNone(condition_factor(None))


class NightLowTests(unittest.TestCase):
    ITEMS = [
        {"datetime": "2026-11-20T00:00:00", "templow": 5.0},
        {"datetime": "2026-11-21T00:00:00", "templow": None},
        {"datetime": "2026-11-22T00:00:00", "templow": 17.0},
        {"datetime": "2026-11-23T00:00:00", "templow": 15.0},
    ]

    def test_first_item_from_today_with_a_low(self):
        self.assertEqual(night_low(self.ITEMS, date(2026, 11, 21), BRISBANE), 17.0)

    def test_todays_low_is_used_when_present(self):
        items = [dict(self.ITEMS[1], templow=19.0)] + self.ITEMS[2:]
        self.assertEqual(night_low(items, date(2026, 11, 21), BRISBANE), 19.0)

    def test_none_without_a_low(self):
        self.assertIsNone(night_low([{"datetime": "2026-11-21T00:00:00", "temperature": 30.0}], date(2026, 11, 21), BRISBANE))
        self.assertIsNone(night_low([], date(2026, 11, 21), BRISBANE))


class FallingOutdoorTests(unittest.TestCase):
    def test_falling_series_reaches_the_setpoint_sooner_than_a_constant_one(self):
        constant = forecast(FIT, 28.0, 26.5, 30.0, 1.1, [])
        falling = forecast(FIT, 28.0, 26.5, 30.0, 1.1, [], outdoor_series=lambda m: 30.0 - m / 6.0 if m < 60 else 20.0)
        self.assertTrue(constant.reachable and falling.reachable)
        self.assertLess(falling.minutes, constant.minutes)

    def test_series_equal_to_the_constant_gives_the_same_result(self):
        self.assertEqual(
            forecast(FIT, 28.0, 26.5, 30.0, 1.1, []).minutes,
            forecast(FIT, 28.0, 26.5, 30.0, 1.1, [], outdoor_series=lambda m: 30.0).minutes,
        )

    def test_unreachable_now_but_reachable_when_the_outdoor_falls(self):
        # Equilibrium at 30 C is 28.125 C, above the 26 C target. At 20 C it is 18.1 C.
        self.assertFalse(forecast(FIT, 30.0, 26.0, 30.0, 0.5, []).reachable)
        falling = forecast(FIT, 30.0, 26.0, 30.0, 0.5, [], outdoor_series=lambda m: 30.0 if m < 120 else 20.0)
        self.assertTrue(falling.reachable)

    def test_unreachable_when_the_series_never_gets_cool_enough(self):
        self.assertFalse(forecast(FIT, 30.0, 26.0, 30.0, 0.5, [], outdoor_series=lambda m: 30.0).reachable)

    def test_range_uses_the_series(self):
        fit = Fit(k=0.4, c=1.5, samples=200, c_stderr=0.2)
        series = lambda m: 24.0
        constant = forecast_range(fit, 28.0, 26.5, 30.0, 1.1, [])
        falling = forecast_range(fit, 28.0, 26.5, 30.0, 1.1, [], outdoor_series=series)
        self.assertLess(falling[0], constant[0])
        self.assertLess(falling[1], constant[1])

    def test_precool_starts_later_when_the_outdoor_will_fall(self):
        # The room needs less time to cool when the outdoor falls, so the latest start moves later.
        constant = precool_start_in_min(FIT, 28.0, 26.5, 30.0, 1.1, 300.0, [])
        falling = precool_start_in_min(FIT, 28.0, 26.5, 30.0, 1.1, 300.0, [], outdoor_series=lambda m: 24.0)
        self.assertLess(constant, falling)

    def test_preheat_time_uses_the_series(self):
        warm = heat_up_minutes(15.0, 21.0, 10.0, 0.1, 0.0, 3.0)
        warming = heat_up_minutes(15.0, 21.0, 10.0, 0.1, 0.0, 3.0, outdoor_series=lambda m: 18.0)
        self.assertLess(warming, warm)


class PlanTests(unittest.TestCase):
    ARGS = dict(fit_k=0.1, fit_c=5.0, fit_g=0.0, room_c=25.0, target_c=24.0, running_kw=1.0, prices=[0.3] * 16)

    def test_cooling_plan_runs_less_when_the_outdoor_falls(self):
        constant = mpc_plan(outdoor_c=34.0, **self.ARGS)
        falling = mpc_plan(outdoor_c=34.0, outdoor_series=lambda m: 14.0, **self.ARGS)
        self.assertGreater(sum(constant), sum(falling))

    def test_cooling_plan_without_series_is_unchanged_by_a_flat_series(self):
        self.assertEqual(mpc_plan(outdoor_c=30.0, **self.ARGS), mpc_plan(outdoor_c=30.0, outdoor_series=lambda m: 30.0, **self.ARGS))

    def test_heat_plan_runs_more_when_the_outdoor_falls(self):
        args = dict(fit_k=0.1, fit_c=5.0, fit_g=0.0, room_c=20.0, target_c=21.0, heat_kw=1.0, prices=[0.3] * 16)
        constant = mpc_heat_plan(outdoor_c=20.0, **args)
        falling = mpc_heat_plan(outdoor_c=20.0, outdoor_series=lambda m: 0.0, **args)
        self.assertGreater(sum(falling), sum(constant))


class BiasTests(unittest.TestCase):
    def learn(self, bias, pairs):
        for sensor, forecast_c, hour in pairs:
            bias = bias.record(sensor, forecast_c, hour)
        return bias

    def test_steady_offset_converges_at_every_hour(self):
        bias = self.learn(OutdoorBias(), [(22.0, 20.0, day * 24 + h) for day in range(20) for h in range(24)])
        for hour in range(24):
            self.assertAlmostEqual(bias.for_hour(hour), 2.0, delta=0.05)
        self.assertAlmostEqual(bias.overall, 2.0, delta=0.05)

    def test_bias_is_learned_per_hour_of_day(self):
        pairs = [(20.0 + (3.0 if 12 <= h <= 17 else 0.0), 20.0, h) for day in range(12) for h in range(24)]
        bias = self.learn(OutdoorBias(), pairs)
        self.assertAlmostEqual(bias.for_hour(15), 3.0, delta=0.1)
        self.assertAlmostEqual(bias.for_hour(2), 0.0, delta=0.05)

    def test_fewer_than_six_pairs_give_no_bias(self):
        bias = self.learn(OutdoorBias(), [(25.0, 20.0, h) for h in range(5)])
        self.assertEqual(bias.for_hour(2), 0.0)
        self.assertNotEqual(bias.record(25.0, 20.0, 5).for_hour(2), 0.0)

    def test_bin_with_fewer_than_three_pairs_uses_the_overall_bias(self):
        bias = self.learn(OutdoorBias(), [(22.0, 20.0, h % 12) for h in range(30)])
        self.assertEqual(bias.counts[20], 0)
        self.assertEqual(bias.for_hour(20), bias.overall)
        self.assertGreater(bias.overall, 1.5)

    def test_one_outlier_moves_a_bin_by_at_most_half_a_kelvin(self):
        bias = self.learn(OutdoorBias(), [(22.0, 20.0, 9)] * 20)
        before = bias.for_hour(9)
        after = bias.record(40.0, 20.0, 9).for_hour(9)
        self.assertLessEqual(abs(after - before), BIAS_STEP_MAX_K + 1e-9)
        self.assertAlmostEqual(abs(after - before), BIAS_STEP_MAX_K)

    def test_first_pair_moves_by_at_most_half_a_kelvin(self):
        self.assertAlmostEqual(OutdoorBias().record(40.0, 20.0, 3).overall, BIAS_STEP_MAX_K)

    def test_bias_is_clamped(self):
        bias = self.learn(OutdoorBias(), [(50.0, 20.0, 5)] * 200)
        self.assertEqual(bias.for_hour(5), BIAS_LIMIT_K)
        self.assertEqual(bias.overall, BIAS_LIMIT_K)

    def test_round_trip_and_bad_data(self):
        bias = self.learn(OutdoorBias(), [(22.0, 20.0, h) for h in range(24) for _ in range(4)])
        self.assertEqual(OutdoorBias.from_dict(bias.to_dict()), bias)
        self.assertEqual(OutdoorBias.from_dict(None), OutdoorBias())
        self.assertEqual(OutdoorBias.from_dict({"means": [1.0], "counts": [1], "overall": 0, "total": 1}), OutdoorBias())
        self.assertEqual(OutdoorBias.from_dict({"means": "x"}), OutdoorBias())

    def test_series_adds_the_bias_of_each_hour_and_anchors_to_the_sensor(self):
        # Forecast 20 C flat. Bias +2 K before the second hour, +4 K after it. The sensor reads 22 C.
        bias_at = lambda t: 2.0 if t < START + 3600 else 4.0
        series = build_series(hourly([20.0] * 12), START, 22.0, bias_at)
        self.assertAlmostEqual(series(0), 22.0)
        self.assertAlmostEqual(series(30), 22.0)
        self.assertAlmostEqual(series(60 * 5), 24.0)
