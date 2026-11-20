import unittest

from custom_components.hvac_horizon.features import (
    DayType,
    air_quality_ventilation,
    classify_day,
    humidity_action,
    minimum_time_ok,
    outdoor_allows,
    timed_preset_active,
)


class FeatureTests(unittest.TestCase):
    def test_day_classes(self):
        self.assertEqual(classify_day(31), DayType.HOT)
        self.assertEqual(classify_day(26), DayType.WARM)
        self.assertEqual(classify_day(20), DayType.MILD)
        self.assertEqual(classify_day(14), DayType.COOL)
        self.assertEqual(classify_day(8), DayType.COLD)

    def test_outdoor_limits(self):
        self.assertFalse(outdoor_allows("cool", 15.0))
        self.assertTrue(outdoor_allows("cool", 16.0))
        self.assertFalse(outdoor_allows("heat", 23.0))
        self.assertTrue(outdoor_allows("heat", 22.0))
        with self.assertRaises(ValueError):
            outdoor_allows("dry", 20.0)

    def test_air_quality_ventilation(self):
        self.assertTrue(air_quality_ventilation(1100.0, None).run)
        self.assertTrue(air_quality_ventilation(None, 30.0).run)
        self.assertFalse(air_quality_ventilation(None, None).run)
        self.assertFalse(air_quality_ventilation(700.0, 10.0).run)

    def test_humidity_band(self):
        self.assertEqual(humidity_action(70.0).action, "dehumidify")
        self.assertEqual(humidity_action(30.0).action, "humidify")
        self.assertEqual(humidity_action(50.0).action, "none")
        self.assertEqual(humidity_action(None).action, "none")

    def test_minimum_on_and_off_times(self):
        self.assertFalse(minimum_time_ok(True, 3.0, min_on_min=5.0, min_off_min=3.0))
        self.assertTrue(minimum_time_ok(True, 5.0, min_on_min=5.0, min_off_min=3.0))
        self.assertTrue(minimum_time_ok(False, 3.0, min_on_min=5.0, min_off_min=3.0))

    def test_timed_preset_window(self):
        self.assertFalse(timed_preset_active(None, 10.0, 30.0))
        self.assertTrue(timed_preset_active(0.0, 10.0, 30.0))
        self.assertFalse(timed_preset_active(0.0, 30.0, 30.0))


if __name__ == "__main__":
    unittest.main()


class PriceShiftTests(unittest.TestCase):
    def test_cheap_power_cools_further(self):
        from custom_components.hvac_horizon.features import price_shifted_target

        self.assertEqual(price_shifted_target(24.0, 0.10, 0.15, 0.40), 23.0)

    def test_expensive_power_relaxes(self):
        from custom_components.hvac_horizon.features import price_shifted_target

        self.assertEqual(price_shifted_target(24.0, 0.50, 0.15, 0.40), 25.0)

    def test_linear_between_limits(self):
        from custom_components.hvac_horizon.features import price_shifted_target

        self.assertAlmostEqual(price_shifted_target(24.0, 0.275, 0.15, 0.40), 24.0)

    def test_no_price_keeps_comfort(self):
        from custom_components.hvac_horizon.features import price_shifted_target

        self.assertEqual(price_shifted_target(24.0, None, 0.15, 0.40), 24.0)

    def test_rejects_inverted_limits(self):
        from custom_components.hvac_horizon.features import price_shifted_target

        with self.assertRaises(ValueError):
            price_shifted_target(24.0, 0.2, 0.40, 0.15)


class ScheduleTests(unittest.TestCase):
    def test_block_inside_the_day(self):
        from custom_components.hvac_horizon.features import ScheduleBlock, scheduled_preset

        blocks = [ScheduleBlock(7.0, 9.0, "boost")]
        self.assertEqual(scheduled_preset(8.0, blocks), "boost")
        self.assertEqual(scheduled_preset(9.0, blocks), "comfort")  # end is exclusive

    def test_block_crossing_midnight(self):
        from custom_components.hvac_horizon.features import ScheduleBlock, scheduled_preset

        blocks = [ScheduleBlock(22.0, 6.0, "eco")]
        self.assertEqual(scheduled_preset(23.5, blocks), "eco")
        self.assertEqual(scheduled_preset(5.0, blocks), "eco")
        self.assertEqual(scheduled_preset(12.0, blocks), "comfort")

    def test_first_matching_block_wins(self):
        from custom_components.hvac_horizon.features import ScheduleBlock, scheduled_preset

        blocks = [ScheduleBlock(6.0, 12.0, "boost"), ScheduleBlock(6.0, 8.0, "eco")]
        self.assertEqual(scheduled_preset(7.0, blocks), "boost")

    def test_default_when_nothing_matches(self):
        from custom_components.hvac_horizon.features import scheduled_preset

        self.assertEqual(scheduled_preset(3.0, [], default="away"), "away")

    def test_rejects_out_of_range_hour(self):
        from custom_components.hvac_horizon.features import scheduled_preset

        with self.assertRaises(ValueError):
            scheduled_preset(24.0, [])


class SafetyAndBriefingTests(unittest.TestCase):
    def test_heating_failure_after_window_without_rise(self):
        from custom_components.hvac_horizon.features import heating_failure

        self.assertTrue(heating_failure(35.0, 0.05))
        self.assertFalse(heating_failure(35.0, 0.5))
        self.assertFalse(heating_failure(10.0, 0.0))

    def test_sensor_stall(self):
        from custom_components.hvac_horizon.features import sensor_stalled

        self.assertTrue(sensor_stalled(None, 1000.0))
        self.assertTrue(sensor_stalled(0.0, 1000.0, stall_s=900.0))
        self.assertFalse(sensor_stalled(500.0, 1000.0, stall_s=900.0))

    def test_safety_output_on_stall_and_minimum(self):
        from custom_components.hvac_horizon.features import safety_output

        self.assertEqual(safety_output(True, 80.0, fallback_pct=20.0), 20.0)
        self.assertEqual(safety_output(False, 5.0, minimum_pct=10.0), 10.0)
        self.assertEqual(safety_output(False, 150.0), 100.0)
        with self.assertRaises(ValueError):
            safety_output(False, 50.0, fallback_pct=120.0)

    def test_daily_briefing_text(self):
        from custom_components.hvac_horizon.features import daily_briefing

        text = daily_briefing("hot", 24.0, "pre-cool at 14:00", 17.5)
        self.assertEqual(text, "Day: hot. Comfort 24.0 C. Next: pre-cool at 14:00. Close windows by 17:30.")
        self.assertEqual(daily_briefing("mild", 23.5, None, None), "Day: mild. Comfort 23.5 C.")


class NightPlanTests(unittest.TestCase):
    def test_precool_when_warm_and_cool_outside(self):
        from custom_components.hvac_horizon.features import night_plan

        self.assertEqual(night_plan(outdoor_low_c=18.0, indoor_c=25.0, comfort_c=24.0), "pre-cool")

    def test_hold_when_at_comfort(self):
        from custom_components.hvac_horizon.features import night_plan

        self.assertEqual(night_plan(outdoor_low_c=22.0, indoor_c=23.5, comfort_c=24.0), "hold")

    def test_none_when_warm_and_outside_still_warm(self):
        from custom_components.hvac_horizon.features import night_plan

        self.assertEqual(night_plan(outdoor_low_c=23.0, indoor_c=25.0, comfort_c=24.0), "none")


class LearningSuggestionTests(unittest.TestCase):
    def test_no_suggestion_within_tolerance(self):
        from custom_components.hvac_horizon.features import learning_suggestion

        self.assertIsNone(learning_suggestion(0.3, 0.32))

    def test_suggestion_when_gain_is_higher(self):
        from custom_components.hvac_horizon.features import learning_suggestion

        self.assertIn("50 % more", learning_suggestion(0.3, 0.45))

    def test_suggestion_when_gain_is_lower(self):
        from custom_components.hvac_horizon.features import learning_suggestion

        self.assertIn("50 % less", learning_suggestion(0.3, 0.15))

    def test_no_suggestion_for_zero_current_gain(self):
        from custom_components.hvac_horizon.features import learning_suggestion

        self.assertIsNone(learning_suggestion(0.0, 0.3))


class VacationTests(unittest.TestCase):
    def test_vacation_holds_vacation_temperature(self):
        from custom_components.hvac_horizon.features import vacation_target

        self.assertEqual(vacation_target(True, 16.0, 24.0), 16.0)

    def test_normal_target_when_vacation_off(self):
        from custom_components.hvac_horizon.features import vacation_target

        self.assertEqual(vacation_target(False, 16.0, 24.0), 24.0)


class PresenceModeTests(unittest.TestCase):
    def test_home_when_any_source_is_home(self):
        from custom_components.hvac_horizon.features import presence_mode

        self.assertEqual(presence_mode([False, True], 30.0, 90.0), "home")

    def test_away_only_after_delay(self):
        from custom_components.hvac_horizon.features import presence_mode

        self.assertEqual(presence_mode([False, False], 30.0, 10.0), "home")
        self.assertEqual(presence_mode([False, False], 30.0, 45.0), "away")

    def test_no_sources_means_home(self):
        from custom_components.hvac_horizon.features import presence_mode

        self.assertEqual(presence_mode([], 30.0, 999.0), "home")


class SolarAndCoverTests(unittest.TestCase):
    def test_sun_heat_gain_in_kw(self):
        from custom_components.hvac_horizon.features import sun_heat_gain

        self.assertAlmostEqual(sun_heat_gain(800.0, 2.0, 0.6), 0.96)

    def test_sun_heat_gain_rejects_bad_inputs(self):
        from custom_components.hvac_horizon.features import sun_heat_gain

        with self.assertRaises(ValueError):
            sun_heat_gain(-1.0, 2.0)
        with self.assertRaises(ValueError):
            sun_heat_gain(100.0, 2.0, shgc=1.5)

    def test_cover_actions(self):
        from custom_components.hvac_horizon.features import cover_action

        self.assertEqual(cover_action(1.5, 1.0, cover_open=True), "close")
        self.assertEqual(cover_action(0.5, 1.0, cover_open=False), "open")
        self.assertEqual(cover_action(1.5, 1.0, cover_open=False), "hold")
        self.assertEqual(cover_action(0.5, 1.0, cover_open=True), "hold")


class ComfortBandTests(unittest.TestCase):
    def test_band_edges(self):
        from custom_components.hvac_horizon.features import comfort_band

        self.assertEqual(comfort_band(24.0, 1.0), (23.0, 25.0))
        with self.assertRaises(ValueError):
            comfort_band(24.0, -1.0)

    def test_band_actions(self):
        from custom_components.hvac_horizon.features import band_action

        self.assertEqual(band_action(26.0, 23.0, 25.0), "cool")
        self.assertEqual(band_action(22.0, 23.0, 25.0), "heat")
        self.assertEqual(band_action(24.0, 23.0, 25.0), "none")
        with self.assertRaises(ValueError):
            band_action(24.0, 25.0, 23.0)


class MouldAndWindowTests(unittest.TestCase):
    def test_window_pause_after_delay(self):
        from custom_components.hvac_horizon.features import window_open_pause

        self.assertFalse(window_open_pause(5.0))
        self.assertTrue(window_open_pause(10.0))

    def test_mould_risk_threshold(self):
        from custom_components.hvac_horizon.features import mould_risk

        self.assertTrue(mould_risk(85.0))
        self.assertFalse(mould_risk(70.0))
        self.assertFalse(mould_risk(None))

    def test_surface_humidity_rises_when_surface_is_colder(self):
        from custom_components.hvac_horizon.features import surface_humidity

        warm = surface_humidity(60.0, 22.0, 21.0)
        cold = surface_humidity(60.0, 22.0, 14.0)
        self.assertGreater(cold, warm)
        self.assertLessEqual(cold, 100.0)

    def test_surface_humidity_rejects_bad_input(self):
        from custom_components.hvac_horizon.features import surface_humidity

        with self.assertRaises(ValueError):
            surface_humidity(120.0, 22.0, 14.0)


class ComplianceTests(unittest.TestCase):
    def test_compliance_percentage(self):
        from custom_components.hvac_horizon.features import compliance_score

        self.assertEqual(compliance_score(90.0, 120.0), 75.0)

    def test_no_time_is_none(self):
        from custom_components.hvac_horizon.features import compliance_score

        self.assertIsNone(compliance_score(0.0, 0.0))

    def test_rejects_impossible_minutes(self):
        from custom_components.hvac_horizon.features import compliance_score

        with self.assertRaises(ValueError):
            compliance_score(130.0, 120.0)


class WeatherSummaryTests(unittest.TestCase):
    def test_day_counts(self):
        from custom_components.hvac_horizon.features import weather_day_summary

        counts = weather_day_summary([31.0, 26.0, 20.0, 14.0, 8.0, 31.5])
        self.assertEqual(counts, {"hot": 2, "warm": 1, "mild": 1, "cool": 1, "cold": 1})

    def test_hot_streak_needs_consecutive_days(self):
        from custom_components.hvac_horizon.features import hot_streak

        self.assertTrue(hot_streak([31.0, 32.0, 30.5, 20.0]))
        self.assertFalse(hot_streak([31.0, 20.0, 31.0, 32.0]))


class FanSpeedTests(unittest.TestCase):
    def test_no_gap_no_fan(self):
        from custom_components.hvac_horizon.features import fan_speed_from_gap

        self.assertEqual(fan_speed_from_gap(23.0, 24.0), 0)

    def test_speed_rises_with_gap(self):
        from custom_components.hvac_horizon.features import fan_speed_from_gap

        self.assertEqual(fan_speed_from_gap(24.5, 24.0), 1)
        self.assertEqual(fan_speed_from_gap(25.5, 24.0), 2)
        self.assertEqual(fan_speed_from_gap(30.0, 24.0), 3)

    def test_rejects_bad_span(self):
        from custom_components.hvac_horizon.features import fan_speed_from_gap

        with self.assertRaises(ValueError):
            fan_speed_from_gap(25.0, 24.0, max_gap_c=0.0)


class DryingTests(unittest.TestCase):
    def test_drying_when_humid_and_no_cooling_needed(self):
        from custom_components.hvac_horizon.features import drying_needed

        self.assertTrue(drying_needed(70.0, cool_gap_c=0.0))

    def test_no_drying_when_cooling_needed(self):
        from custom_components.hvac_horizon.features import drying_needed

        self.assertFalse(drying_needed(70.0, cool_gap_c=1.0))

    def test_no_drying_without_reading_or_when_dry(self):
        from custom_components.hvac_horizon.features import drying_needed

        self.assertFalse(drying_needed(None))
        self.assertFalse(drying_needed(50.0))


class PriceFallbackTests(unittest.TestCase):
    def test_current_price_is_used(self):
        from custom_components.hvac_horizon.features import stale_price_fallback

        self.assertEqual(stale_price_fallback(0.3, 0.2, 5.0), 0.3)

    def test_last_good_price_within_age(self):
        from custom_components.hvac_horizon.features import stale_price_fallback

        self.assertEqual(stale_price_fallback(None, 0.2, 30.0), 0.2)

    def test_no_price_after_max_age(self):
        from custom_components.hvac_horizon.features import stale_price_fallback

        self.assertIsNone(stale_price_fallback(None, 0.2, 120.0))


class VentilationRateTests(unittest.TestCase):
    def test_rate_bounds_and_linear_middle(self):
        from custom_components.hvac_horizon.features import ventilation_rate_from_co2

        self.assertEqual(ventilation_rate_from_co2(700.0), 0.0)
        self.assertEqual(ventilation_rate_from_co2(1400.0), 1.0)
        self.assertAlmostEqual(ventilation_rate_from_co2(1000.0), 0.5)

    def test_missing_co2_is_zero(self):
        from custom_components.hvac_horizon.features import ventilation_rate_from_co2

        self.assertEqual(ventilation_rate_from_co2(None), 0.0)

    def test_rejects_bad_range(self):
        from custom_components.hvac_horizon.features import ventilation_rate_from_co2

        with self.assertRaises(ValueError):
            ventilation_rate_from_co2(900.0, low_ppm=1200.0, high_ppm=800.0)


class NaturalVentilationTests(unittest.TestCase):
    def test_open_when_outside_is_cooler(self):
        from custom_components.hvac_horizon.features import natural_ventilation_window

        self.assertEqual(natural_ventilation_window(outdoor_c=18.0, indoor_c=25.0), "open")

    def test_close_when_outside_is_warmer_or_too_hot(self):
        from custom_components.hvac_horizon.features import natural_ventilation_window

        self.assertEqual(natural_ventilation_window(outdoor_c=27.0, indoor_c=25.0), "close")
        self.assertEqual(natural_ventilation_window(outdoor_c=25.0, indoor_c=30.0, max_outdoor_c=24.0), "close")

    def test_hold_when_difference_is_small(self):
        from custom_components.hvac_horizon.features import natural_ventilation_window

        self.assertEqual(natural_ventilation_window(outdoor_c=23.5, indoor_c=24.5), "hold")


class InhibitionTests(unittest.TestCase):
    def test_inhibited_until_period_ends(self):
        from custom_components.hvac_horizon.features import inhibited

        self.assertTrue(inhibited(10.0, 30.0))
        self.assertFalse(inhibited(30.0, 30.0))
        self.assertFalse(inhibited(10.0, None))


class LockTests(unittest.TestCase):
    def test_lock_blocks_control(self):
        from custom_components.hvac_horizon.features import lock_allows_control

        self.assertFalse(lock_allows_control(True))
        self.assertTrue(lock_allows_control(False))

    def test_pin_check(self):
        from custom_components.hvac_horizon.features import check_pin

        self.assertTrue(check_pin("1234", "1234"))
        self.assertFalse(check_pin("0000", "1234"))
        self.assertFalse(check_pin(None, "1234"))
        self.assertFalse(check_pin("", None))


class MorePortedFeatureTests(unittest.TestCase):
    def test_keep_alive(self):
        from custom_components.hvac_horizon.features import keep_alive_due

        self.assertTrue(keep_alive_due(None, 10.0))
        self.assertFalse(keep_alive_due(8.0, 10.0))
        self.assertTrue(keep_alive_due(5.0, 10.0))

    def test_valve_exercise(self):
        from custom_components.hvac_horizon.features import valve_exercise_due

        self.assertFalse(valve_exercise_due(3.0))
        self.assertTrue(valve_exercise_due(7.0))

    def test_whole_house_fan(self):
        from custom_components.hvac_horizon.features import whole_house_fan_allowed

        self.assertTrue(whole_house_fan_allowed(False, False, 18.0, 25.0))
        self.assertFalse(whole_house_fan_allowed(True, False, 18.0, 25.0))
        self.assertFalse(whole_house_fan_allowed(False, True, 18.0, 25.0))
        self.assertFalse(whole_house_fan_allowed(False, False, 24.0, 25.0))
        self.assertFalse(whole_house_fan_allowed(False, False, None, 25.0))

    def test_comfort_energy_blend(self):
        from custom_components.hvac_horizon.features import comfort_energy_setpoint

        self.assertEqual(comfort_energy_setpoint(24.0, 26.0, 0.0), 24.0)
        self.assertEqual(comfort_energy_setpoint(24.0, 26.0, 1.0), 26.0)
        self.assertEqual(comfort_energy_setpoint(24.0, 26.0, 0.5), 25.0)
        with self.assertRaises(ValueError):
            comfort_energy_setpoint(24.0, 26.0, 1.5)
