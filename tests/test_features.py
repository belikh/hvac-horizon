import unittest

from custom_components.hvac_horizon.engine.features import (
    apply_polarity,
    boost_output,
    clamp_output,
    coil_dry_due,
    DayType,
    air_quality_ventilation,
    classify_day,
    economizer_phase,
    effective_home,
    forecast_highs,
    boiler_demand,
    heat_source,
    learned_solar_scale,
    humidity_action,
    natural_ventilation_window,
    whole_house_fan_allowed,
    window_actuator_command,
    restart_wait_min,
    ScheduleBlock,
    minutes_until_preset,
    room_devices,
    round_to_step,
    mould_target,
    mpc_heat_plan,
    mpc_idle,
    mpc_plan,
    motion_preset,
    mode_disagrees,
    outdoor_allows,
    pwm_on,
    shed_plan,
    stagger_allowed,
    switch_feedback,
    timed_preset_active,
    timed_preset_name,
    transition_message,
    valve_opening_pct,
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

    def test_restart_delay_waits_only_for_the_rest_of_the_off_time(self):
        self.assertAlmostEqual(restart_wait_min(1.0, 3.0), 2.0)
        self.assertEqual(restart_wait_min(3.0, 3.0), 0.0)
        self.assertEqual(restart_wait_min(10.0, 3.0), 0.0)

    def test_restart_delay_of_zero_is_disabled(self):
        self.assertEqual(restart_wait_min(0.0, 0.0), 0.0)

    def test_timed_preset_window(self):
        self.assertFalse(timed_preset_active(None, 10.0, 30.0))
        self.assertTrue(timed_preset_active(0.0, 10.0, 30.0))
        self.assertFalse(timed_preset_active(0.0, 30.0, 30.0))


if __name__ == "__main__":
    unittest.main()


class PriceShiftTests(unittest.TestCase):
    def test_cheap_power_leaves_the_target_alone(self):
        from custom_components.hvac_horizon.engine.features import price_shifted_target

        self.assertEqual(price_shifted_target(24.0, 0.10, 0.15, 0.40), 24.0)
        self.assertEqual(price_shifted_target(24.0, 0.20, 0.15, 0.40), 24.0)
        self.assertAlmostEqual(price_shifted_target(24.0, 0.3375, 0.15, 0.40), 24.5)

    def test_expensive_power_relaxes(self):
        from custom_components.hvac_horizon.engine.features import price_shifted_target

        self.assertEqual(price_shifted_target(24.0, 0.50, 0.15, 0.40), 25.0)

    def test_linear_between_limits(self):
        from custom_components.hvac_horizon.engine.features import price_shifted_target

        self.assertAlmostEqual(price_shifted_target(24.0, 0.275, 0.15, 0.40), 24.0)

    def test_no_price_keeps_comfort(self):
        from custom_components.hvac_horizon.engine.features import price_shifted_target

        self.assertEqual(price_shifted_target(24.0, None, 0.15, 0.40), 24.0)

    def test_rejects_inverted_limits(self):
        from custom_components.hvac_horizon.engine.features import price_shifted_target

        with self.assertRaises(ValueError):
            price_shifted_target(24.0, 0.2, 0.40, 0.15)


class ScheduleTests(unittest.TestCase):
    def test_block_inside_the_day(self):
        from custom_components.hvac_horizon.engine.features import ScheduleBlock, scheduled_preset

        blocks = [ScheduleBlock(7.0, 9.0, "boost")]
        self.assertEqual(scheduled_preset(8.0, blocks), "boost")
        self.assertEqual(scheduled_preset(9.0, blocks), "comfort")  # end is exclusive

    def test_block_crossing_midnight(self):
        from custom_components.hvac_horizon.engine.features import ScheduleBlock, scheduled_preset

        blocks = [ScheduleBlock(22.0, 6.0, "eco")]
        self.assertEqual(scheduled_preset(23.5, blocks), "eco")
        self.assertEqual(scheduled_preset(5.0, blocks), "eco")
        self.assertEqual(scheduled_preset(12.0, blocks), "comfort")

    def test_first_matching_block_wins(self):
        from custom_components.hvac_horizon.engine.features import ScheduleBlock, scheduled_preset

        blocks = [ScheduleBlock(6.0, 12.0, "boost"), ScheduleBlock(6.0, 8.0, "eco")]
        self.assertEqual(scheduled_preset(7.0, blocks), "boost")

    def test_default_when_nothing_matches(self):
        from custom_components.hvac_horizon.engine.features import scheduled_preset

        self.assertEqual(scheduled_preset(3.0, [], default="away"), "away")

    def test_rejects_out_of_range_hour(self):
        from custom_components.hvac_horizon.engine.features import scheduled_preset

        with self.assertRaises(ValueError):
            scheduled_preset(24.0, [])


class SafetyAndBriefingTests(unittest.TestCase):
    def test_heating_failure_after_window_without_rise(self):
        from custom_components.hvac_horizon.engine.features import heating_failed

        self.assertTrue(heating_failed(60.0, 0.49))
        self.assertTrue(heating_failed(90.0, 0.0))
        self.assertFalse(heating_failed(60.0, 0.5))
        self.assertFalse(heating_failed(59.9, 0.0))

    def test_run_share_weights_each_power_sample_by_the_time_it_held(self):
        from custom_components.hvac_horizon.engine.features import run_share

        # 20 minute window ending at 1200 s. The compressor ran for the first 5 minutes and for 3 minutes near the end.
        samples = [(-60.0, 1050.0), (300.0, 78.0), (900.0, 1040.0), (1080.0, 78.0)]
        self.assertAlmostEqual(run_share(samples, 1200.0, 1200.0, 200.0), (300.0 + 180.0) / 1200.0)
        self.assertEqual(run_share([(0.0, 1050.0)], 1200.0, 1200.0, 200.0), 1.0)
        self.assertEqual(run_share([(0.0, 78.0)], 1200.0, 1200.0, 200.0), 0.0)
        self.assertEqual(run_share([(0.0, 200.0)], 1200.0, 1200.0, 200.0), 1.0)  # at the limit counts as running

    def test_run_share_is_unknown_until_the_samples_cover_the_window(self):
        from custom_components.hvac_horizon.engine.features import run_share

        self.assertIsNone(run_share([], 1200.0, 1200.0, 200.0))
        self.assertIsNone(run_share([(1.0, 78.0)], 1200.0, 1200.0, 200.0))
        self.assertIsNone(run_share([(0.0, 78.0)], 1200.0, 0.0, 200.0))

    def test_ac_failure_needs_the_full_window_and_less_than_half_running(self):
        from custom_components.hvac_horizon.engine.features import ac_failed

        self.assertTrue(ac_failed(20.0, 0.49, False))
        self.assertFalse(ac_failed(20.0, 0.5, False))
        self.assertFalse(ac_failed(19.9, 0.0, False))
        self.assertFalse(ac_failed(60.0, None, False))

    def test_ac_failure_stays_reported_until_the_compressor_runs_three_quarters_of_the_window(self):
        from custom_components.hvac_horizon.engine.features import ac_failed

        self.assertTrue(ac_failed(40.0, 0.6, True))
        self.assertFalse(ac_failed(40.0, 0.75, True))
        self.assertFalse(ac_failed(40.0, None, True))

    def test_heat_on_below_is_target_minus_deadband(self):
        from custom_components.hvac_horizon.engine.features import heat_on_below

        self.assertEqual(heat_on_below(24.0, 0.5), 23.5)
        self.assertEqual(heat_on_below(21.0, 0.0), 21.0)
        with self.assertRaises(ValueError):
            heat_on_below(24.0, -0.1)

    def test_sensor_stall(self):
        from custom_components.hvac_horizon.engine.features import sensor_stalled

        self.assertTrue(sensor_stalled(None, 1000.0))
        self.assertTrue(sensor_stalled(0.0, 1000.0, stall_s=900.0))
        self.assertFalse(sensor_stalled(500.0, 1000.0, stall_s=900.0))

    def test_safety_output_on_stall_and_minimum(self):
        from custom_components.hvac_horizon.engine.features import safety_output

        self.assertEqual(safety_output(True, 80.0, fallback_pct=20.0), 20.0)
        self.assertEqual(safety_output(False, 5.0, minimum_pct=10.0), 10.0)
        self.assertEqual(safety_output(False, 150.0), 100.0)
        with self.assertRaises(ValueError):
            safety_output(False, 50.0, fallback_pct=120.0)

    def test_daily_briefing_text(self):
        from custom_components.hvac_horizon.engine.features import daily_briefing

        text = daily_briefing("hot", 24.0, "pre-cool at 14:00", 17.5)
        self.assertEqual(text, "Day: hot. Comfort 24.0 C. Next: pre-cool at 14:00. Close windows by 17:30.")
        self.assertEqual(daily_briefing("mild", 23.5, None, None), "Day: mild. Comfort 23.5 C.")


class NightPlanTests(unittest.TestCase):
    def test_precool_when_warm_and_cool_outside(self):
        from custom_components.hvac_horizon.engine.features import night_plan

        self.assertEqual(night_plan(outdoor_low_c=18.0, indoor_c=25.0, comfort_c=24.0), "pre-cool")

    def test_hold_when_at_comfort(self):
        from custom_components.hvac_horizon.engine.features import night_plan

        self.assertEqual(night_plan(outdoor_low_c=22.0, indoor_c=23.5, comfort_c=24.0), "hold")

    def test_none_when_warm_and_outside_still_warm(self):
        from custom_components.hvac_horizon.engine.features import night_plan

        self.assertEqual(night_plan(outdoor_low_c=23.0, indoor_c=25.0, comfort_c=24.0), "none")


class LearningSuggestionTests(unittest.TestCase):
    def test_no_suggestion_within_tolerance(self):
        from custom_components.hvac_horizon.engine.features import learning_suggestion

        self.assertIsNone(learning_suggestion(0.3, 0.32))

    def test_suggestion_when_gain_is_higher(self):
        from custom_components.hvac_horizon.engine.features import learning_suggestion

        self.assertIn("50 % more", learning_suggestion(0.3, 0.45))

    def test_suggestion_when_gain_is_lower(self):
        from custom_components.hvac_horizon.engine.features import learning_suggestion

        self.assertIn("50 % less", learning_suggestion(0.3, 0.15))

    def test_no_suggestion_for_zero_current_gain(self):
        from custom_components.hvac_horizon.engine.features import learning_suggestion

        self.assertIsNone(learning_suggestion(0.0, 0.3))


class SuggestionVisibleTests(unittest.TestCase):
    def test_visible_when_not_dismissed(self):
        from custom_components.hvac_horizon.engine.features import suggestion_visible

        self.assertEqual(suggestion_visible("text", None, 0.3), "text")

    def test_hidden_when_dismissed_at_same_gain(self):
        from custom_components.hvac_horizon.engine.features import suggestion_visible

        self.assertIsNone(suggestion_visible("text", 0.3, 0.3))

    def test_visible_again_when_gain_moved_over_one_percent(self):
        from custom_components.hvac_horizon.engine.features import suggestion_visible

        self.assertEqual(suggestion_visible("text", 0.3, 0.31), "text")

    def test_none_in_gives_none_out(self):
        from custom_components.hvac_horizon.engine.features import suggestion_visible

        self.assertIsNone(suggestion_visible(None, None, 0.3))


class VacationTests(unittest.TestCase):
    def test_vacation_holds_vacation_temperature(self):
        from custom_components.hvac_horizon.engine.features import vacation_target

        self.assertEqual(vacation_target(True, 16.0, 24.0), 16.0)

    def test_normal_target_when_vacation_off(self):
        from custom_components.hvac_horizon.engine.features import vacation_target

        self.assertEqual(vacation_target(False, 16.0, 24.0), 24.0)


class PresenceModeTests(unittest.TestCase):
    def test_home_when_any_source_is_home(self):
        from custom_components.hvac_horizon.engine.features import presence_mode

        self.assertEqual(presence_mode([False, True], 30.0, 90.0), "home")

    def test_away_only_after_delay(self):
        from custom_components.hvac_horizon.engine.features import presence_mode

        self.assertEqual(presence_mode([False, False], 30.0, 10.0), "home")
        self.assertEqual(presence_mode([False, False], 30.0, 45.0), "away")

    def test_no_sources_means_home(self):
        from custom_components.hvac_horizon.engine.features import presence_mode

        self.assertEqual(presence_mode([], 30.0, 999.0), "home")


class SolarAndCoverTests(unittest.TestCase):
    def test_sun_heat_gain_in_kw(self):
        from custom_components.hvac_horizon.engine.features import sun_heat_gain

        self.assertAlmostEqual(sun_heat_gain(800.0, 2.0, 0.6), 0.96)

    def test_sun_heat_gain_rejects_bad_inputs(self):
        from custom_components.hvac_horizon.engine.features import sun_heat_gain

        with self.assertRaises(ValueError):
            sun_heat_gain(-1.0, 2.0)
        with self.assertRaises(ValueError):
            sun_heat_gain(100.0, 2.0, shgc=1.5)

    def test_cover_actions(self):
        from custom_components.hvac_horizon.engine.features import cover_action

        self.assertEqual(cover_action(1.5, 1.0, cover_open=True), "close")
        self.assertEqual(cover_action(0.5, 1.0, cover_open=False), "open")
        self.assertEqual(cover_action(1.5, 1.0, cover_open=False), "hold")
        self.assertEqual(cover_action(0.5, 1.0, cover_open=True), "hold")


class ComfortBandTests(unittest.TestCase):
    def test_band_edges(self):
        from custom_components.hvac_horizon.engine.features import comfort_band

        self.assertEqual(comfort_band(24.0, 1.0), (23.0, 25.0))
        with self.assertRaises(ValueError):
            comfort_band(24.0, -1.0)

    def test_band_actions(self):
        from custom_components.hvac_horizon.engine.features import band_action

        self.assertEqual(band_action(26.0, 23.0, 25.0), "cool")
        self.assertEqual(band_action(22.0, 23.0, 25.0), "heat")
        self.assertEqual(band_action(24.0, 23.0, 25.0), "none")
        with self.assertRaises(ValueError):
            band_action(24.0, 25.0, 23.0)


class MouldAndWindowTests(unittest.TestCase):
    def test_window_pause_after_delay(self):
        from custom_components.hvac_horizon.engine.features import window_open_pause

        self.assertFalse(window_open_pause(5.0))
        self.assertTrue(window_open_pause(10.0))

    def test_mould_risk_threshold(self):
        from custom_components.hvac_horizon.engine.features import mould_risk

        self.assertTrue(mould_risk(85.0))
        self.assertFalse(mould_risk(70.0))
        self.assertFalse(mould_risk(None))

    def test_surface_humidity_rises_when_surface_is_colder(self):
        from custom_components.hvac_horizon.engine.features import surface_humidity

        warm = surface_humidity(60.0, 22.0, 21.0)
        cold = surface_humidity(60.0, 22.0, 14.0)
        self.assertGreater(cold, warm)
        self.assertLessEqual(cold, 100.0)

    def test_surface_humidity_rejects_bad_input(self):
        from custom_components.hvac_horizon.engine.features import surface_humidity

        with self.assertRaises(ValueError):
            surface_humidity(120.0, 22.0, 14.0)


class ComplianceTests(unittest.TestCase):
    def test_compliance_percentage(self):
        from custom_components.hvac_horizon.engine.features import compliance_score

        self.assertEqual(compliance_score(90.0, 120.0), 75.0)

    def test_no_time_is_none(self):
        from custom_components.hvac_horizon.engine.features import compliance_score

        self.assertIsNone(compliance_score(0.0, 0.0))

    def test_rejects_impossible_minutes(self):
        from custom_components.hvac_horizon.engine.features import compliance_score

        with self.assertRaises(ValueError):
            compliance_score(130.0, 120.0)


class WeatherSummaryTests(unittest.TestCase):

    def test_hot_streak_needs_consecutive_days(self):
        from custom_components.hvac_horizon.engine.features import hot_streak

        self.assertTrue(hot_streak([31.0, 32.0, 30.5, 20.0]))
        self.assertFalse(hot_streak([31.0, 20.0, 31.0, 32.0]))


class FanSpeedTests(unittest.TestCase):
    def test_no_gap_no_fan(self):
        from custom_components.hvac_horizon.engine.features import fan_speed_from_gap

        self.assertEqual(fan_speed_from_gap(23.0, 24.0), 0)

    def test_speed_rises_with_gap(self):
        from custom_components.hvac_horizon.engine.features import fan_speed_from_gap

        self.assertEqual(fan_speed_from_gap(24.5, 24.0), 1)
        self.assertEqual(fan_speed_from_gap(25.5, 24.0), 2)
        self.assertEqual(fan_speed_from_gap(30.0, 24.0), 3)

    def test_rejects_bad_span(self):
        from custom_components.hvac_horizon.engine.features import fan_speed_from_gap

        with self.assertRaises(ValueError):
            fan_speed_from_gap(25.0, 24.0, max_gap_c=0.0)


class DryingTests(unittest.TestCase):
    def test_drying_when_humid_and_no_cooling_needed(self):
        from custom_components.hvac_horizon.engine.features import drying_needed

        self.assertTrue(drying_needed(70.0, cool_gap_c=0.0))

    def test_no_drying_when_cooling_needed(self):
        from custom_components.hvac_horizon.engine.features import drying_needed

        self.assertFalse(drying_needed(70.0, cool_gap_c=1.0))

    def test_no_drying_without_reading_or_when_dry(self):
        from custom_components.hvac_horizon.engine.features import drying_needed

        self.assertFalse(drying_needed(None))
        self.assertFalse(drying_needed(50.0))


class PriceFallbackTests(unittest.TestCase):
    def test_current_price_is_used(self):
        from custom_components.hvac_horizon.engine.features import stale_price_fallback

        self.assertEqual(stale_price_fallback(0.3, 0.2, 5.0), 0.3)

    def test_last_good_price_within_age(self):
        from custom_components.hvac_horizon.engine.features import stale_price_fallback

        self.assertEqual(stale_price_fallback(None, 0.2, 30.0), 0.2)

    def test_no_price_after_max_age(self):
        from custom_components.hvac_horizon.engine.features import stale_price_fallback

        self.assertIsNone(stale_price_fallback(None, 0.2, 120.0))


class VentilationRateTests(unittest.TestCase):
    def test_rate_bounds_and_linear_middle(self):
        from custom_components.hvac_horizon.engine.features import ventilation_rate_from_co2

        self.assertEqual(ventilation_rate_from_co2(700.0), 0.0)
        self.assertEqual(ventilation_rate_from_co2(1400.0), 1.0)
        self.assertAlmostEqual(ventilation_rate_from_co2(1000.0), 0.5)

    def test_missing_co2_is_zero(self):
        from custom_components.hvac_horizon.engine.features import ventilation_rate_from_co2

        self.assertEqual(ventilation_rate_from_co2(None), 0.0)

    def test_rejects_bad_range(self):
        from custom_components.hvac_horizon.engine.features import ventilation_rate_from_co2

        with self.assertRaises(ValueError):
            ventilation_rate_from_co2(900.0, low_ppm=1200.0, high_ppm=800.0)


class NaturalVentilationTests(unittest.TestCase):
    def test_cold_room_does_not_open_windows(self):
        from custom_components.hvac_horizon.engine.features import natural_ventilation_window

        self.assertEqual(natural_ventilation_window(5.0, 19.0, cool_above_c=24.0), "close")
        self.assertEqual(economizer_phase(5.0, 19.0, 22.0, 0.0, cool_above_c=24.0), "off")
        self.assertEqual(natural_ventilation_window(18.0, 27.0, cool_above_c=24.0), "open")
        self.assertEqual(economizer_phase(18.0, 27.0, 24.0, 0.0, cool_above_c=24.0), "open_windows")

    def test_open_when_outside_is_cooler(self):
        from custom_components.hvac_horizon.engine.features import natural_ventilation_window

        self.assertEqual(natural_ventilation_window(outdoor_c=18.0, indoor_c=25.0), "open")

    def test_close_when_outside_is_warmer_or_too_hot(self):
        from custom_components.hvac_horizon.engine.features import natural_ventilation_window

        self.assertEqual(natural_ventilation_window(outdoor_c=27.0, indoor_c=25.0), "close")
        self.assertEqual(natural_ventilation_window(outdoor_c=25.0, indoor_c=30.0, max_outdoor_c=24.0), "close")

    def test_hold_when_difference_is_small(self):
        from custom_components.hvac_horizon.engine.features import natural_ventilation_window

        self.assertEqual(natural_ventilation_window(outdoor_c=23.5, indoor_c=24.5), "hold")


class InhibitionTests(unittest.TestCase):
    def test_inhibited_until_period_ends(self):
        from custom_components.hvac_horizon.engine.features import inhibited

        self.assertTrue(inhibited(10.0, 30.0))
        self.assertFalse(inhibited(30.0, 30.0))
        self.assertFalse(inhibited(10.0, None))


class LockTests(unittest.TestCase):
    def test_lock_blocks_control(self):
        from custom_components.hvac_horizon.engine.features import lock_allows_control

        self.assertFalse(lock_allows_control(True))
        self.assertTrue(lock_allows_control(False))

    def test_pin_check(self):
        from custom_components.hvac_horizon.engine.features import check_pin

        self.assertTrue(check_pin("1234", "1234"))
        self.assertFalse(check_pin("0000", "1234"))
        self.assertFalse(check_pin(None, "1234"))
        self.assertFalse(check_pin("", None))


class MorePortedFeatureTests(unittest.TestCase):
    def test_keep_alive(self):
        from custom_components.hvac_horizon.engine.features import keep_alive_due

        self.assertTrue(keep_alive_due(None, 10.0))
        self.assertFalse(keep_alive_due(8.0, 10.0))
        self.assertTrue(keep_alive_due(5.0, 10.0))

    def test_whole_house_fan(self):
        from custom_components.hvac_horizon.engine.features import whole_house_fan_allowed

        self.assertTrue(whole_house_fan_allowed(False, False, 18.0, 25.0))
        self.assertFalse(whole_house_fan_allowed(True, False, 18.0, 25.0))
        self.assertFalse(whole_house_fan_allowed(False, True, 18.0, 25.0))
        self.assertFalse(whole_house_fan_allowed(False, False, 24.0, 25.0))
        self.assertFalse(whole_house_fan_allowed(False, False, None, 25.0))

class TransitionMessageTests(unittest.TestCase):
    def test_turns_on(self):
        self.assertEqual(transition_message(False, True, "on", "off"), "on")

    def test_turns_off(self):
        self.assertEqual(transition_message(True, False, "on", "off"), "off")

    def test_stays_on(self):
        self.assertIsNone(transition_message(True, True, "on", "off"))

    def test_stays_off(self):
        self.assertIsNone(transition_message(False, False, "on", "off"))


class ModeDisagreesTests(unittest.TestCase):
    def test_differing_modes_without_setpoint_change(self):
        self.assertTrue(mode_disagrees("cool", "off", False))

    def test_matching_modes(self):
        self.assertFalse(mode_disagrees("cool", "cool", False))

    def test_setpoint_change(self):
        self.assertFalse(mode_disagrees("cool", "off", True))

    def test_written_mode_none(self):
        self.assertFalse(mode_disagrees(None, "off", False))

    def test_actual_mode_none(self):
        self.assertFalse(mode_disagrees("cool", None, False))


class TimedPresetNameTests(unittest.TestCase):
    def test_active_returns_timed_preset(self):
        self.assertEqual(timed_preset_name(True, "eco", "comfort"), "eco")

    def test_inactive_returns_normal_preset(self):
        self.assertEqual(timed_preset_name(False, "eco", "comfort"), "comfort")

    def test_active_without_timed_preset_returns_normal(self):
        self.assertEqual(timed_preset_name(True, None, "comfort"), "comfort")


class SwitchFeedbackTests(unittest.TestCase):
    def test_not_configured(self):
        self.assertEqual(switch_feedback(None, "on"), "not_configured")

    def test_unavailable(self):
        self.assertEqual(switch_feedback(True, "unavailable"), "unavailable")

    def test_match(self):
        self.assertEqual(switch_feedback(False, "off"), "match")

    def test_mismatch(self):
        self.assertEqual(switch_feedback(True, "off"), "mismatch")


class PwmTests(unittest.TestCase):
    def test_zero_is_always_off(self):
        self.assertFalse(any(pwm_on(0, 10, m / 2) for m in range(40)))

    def test_hundred_is_always_on(self):
        self.assertTrue(all(pwm_on(100, 10, m / 2) for m in range(40)))

    def test_forty_on_at_three_minutes(self):
        self.assertTrue(pwm_on(40, 10, 3))

    def test_forty_off_at_five_minutes(self):
        self.assertFalse(pwm_on(40, 10, 5))

    def test_output_above_100_raises(self):
        with self.assertRaises(ValueError):
            pwm_on(101, 10, 1)

    def test_negative_output_raises(self):
        with self.assertRaises(ValueError):
            pwm_on(-1, 10, 1)

    def test_zero_window_raises(self):
        with self.assertRaises(ValueError):
            pwm_on(50, 0, 1)


class ValveOpeningTests(unittest.TestCase):
    def test_two_below_is_fully_open(self):
        self.assertEqual(valve_opening_pct(22.0, 24.0), 100.0)

    def test_at_target_is_closed(self):
        self.assertEqual(valve_opening_pct(24.0, 24.0), 0.0)

    def test_above_target_is_closed(self):
        self.assertEqual(valve_opening_pct(25.0, 24.0), 0.0)

    def test_half_band_below_is_half_open(self):
        self.assertEqual(valve_opening_pct(23.5, 24.0), 50.0)

    def test_zero_band_raises(self):
        with self.assertRaises(ValueError):
            valve_opening_pct(23.0, 24.0, 0)


class ForecastHighsTests(unittest.TestCase):
    def test_normal_items(self):
        self.assertEqual(forecast_highs([{"temperature": 31}, {"temperature": 28.5}]), [31.0, 28.5])

    def test_missing_temperature_is_skipped(self):
        self.assertEqual(forecast_highs([{"temperature": 31.0}, {"condition": "sunny"}]), [31.0])

    def test_non_numeric_temperature_is_skipped(self):
        self.assertEqual(forecast_highs([{"temperature": "hot"}, {"temperature": None}, {"temperature": 20.0}]), [20.0])

    def test_empty_list(self):
        self.assertEqual(forecast_highs([]), [])


class ShedPlanTests(unittest.TestCase):
    LOADS = [("switch.a", 1000.0, True), ("switch.b", 500.0, True), ("switch.c", 800.0, True)]

    def test_under_limit_returns_empty(self):
        self.assertEqual(shed_plan(3000.0, 4000.0, self.LOADS), [])

    def test_over_limit_sheds_lowest_priority_first(self):
        self.assertEqual(shed_plan(4500.0, 4000.0, self.LOADS), ["switch.a"])

    def test_off_loads_are_skipped(self):
        loads = [("switch.a", 1000.0, False), ("switch.b", 500.0, True)]
        self.assertEqual(shed_plan(4400.0, 4000.0, loads), ["switch.b"])

    def test_sheds_several_until_cut_is_met(self):
        self.assertEqual(shed_plan(5200.0, 4000.0, self.LOADS), ["switch.a", "switch.b"])

    def test_stops_early_once_cut_is_met(self):
        self.assertNotIn("switch.c", shed_plan(5200.0, 4000.0, self.LOADS))

    def test_zero_watt_loads_are_skipped(self):
        loads = [("switch.a", 0.0, True), ("switch.b", 500.0, True)]
        self.assertEqual(shed_plan(4400.0, 4000.0, loads), ["switch.b"])

    def test_zero_max_raises(self):
        with self.assertRaises(ValueError):
            shed_plan(100.0, 0.0, self.LOADS)

    def test_negative_margin_raises(self):
        with self.assertRaises(ValueError):
            shed_plan(100.0, 4000.0, self.LOADS, margin_w=-1.0)


class TestEconomizerPhase(unittest.TestCase):
    def test_cool_outdoor_opens_windows(self):
        self.assertEqual(economizer_phase(18.0, 26.0, 24.0, 0.0), "open_windows")

    def test_too_hot_outdoors_is_off(self):
        self.assertEqual(economizer_phase(25.0, 30.0, 24.0, 0.0), "off")

    def test_warm_room_after_30_minutes_gives_fan_assist(self):
        self.assertEqual(economizer_phase(18.0, 26.0, 24.0, 30.0), "fan_assist")

    def test_warm_room_before_30_minutes_stays_open_windows(self):
        self.assertEqual(economizer_phase(18.0, 26.0, 24.0, 29.0), "open_windows")

    def test_room_at_comfort_stays_open_windows(self):
        self.assertEqual(economizer_phase(18.0, 24.0, 24.0, 45.0), "open_windows")

    def test_negative_delta_raises(self):
        with self.assertRaises(ValueError):
            economizer_phase(18.0, 26.0, 24.0, 0.0, min_delta_c=-1.0)

    def test_zero_fan_after_raises(self):
        with self.assertRaises(ValueError):
            economizer_phase(18.0, 26.0, 24.0, 0.0, fan_after_min=0.0)


class HeatSourceTests(unittest.TestCase):
    def test_cold_outdoors_picks_boiler(self):
        self.assertEqual(heat_source(0.0), "boiler")

    def test_mild_outdoors_picks_ac(self):
        self.assertEqual(heat_source(12.0), "ac")

    def test_boundary_picks_boiler(self):
        self.assertEqual(heat_source(5.0), "boiler")

    def test_demand_true_when_cold_and_below_low_edge(self):
        self.assertTrue(boiler_demand(20.0, 23.0, "boiler"))

    def test_demand_false_at_low_edge(self):
        self.assertFalse(boiler_demand(23.0, 23.0, "boiler"))

    def test_demand_false_when_source_is_ac(self):
        self.assertFalse(boiler_demand(20.0, 23.0, "ac"))


class StaggerAllowedTests(unittest.TestCase):
    def test_first_wanting_device_allowed_with_no_recent_start(self):
        self.assertTrue(stagger_allowed("a", ["a", "b"], {}, 100.0))

    def test_second_wanting_device_blocked_after_first_started_2_min_ago(self):
        self.assertFalse(stagger_allowed("b", ["a", "b"], {"a": 98.0}, 100.0))

    def test_second_device_allowed_6_min_after_first_start(self):
        self.assertTrue(stagger_allowed("b", ["b"], {"a": 94.0}, 100.0))

    def test_device_not_wanting_is_false(self):
        self.assertFalse(stagger_allowed("c", ["a", "b"], {}, 100.0))

    def test_negative_gap_raises(self):
        with self.assertRaises(ValueError):
            stagger_allowed("a", ["a"], {}, 100.0, -1.0)


class MpcPlanTests(unittest.TestCase):
    ARGS = dict(fit_k=0.1, fit_c=5.0, fit_g=0.0, target_c=24.0, outdoor_c=30.0, running_kw=1.0)

    def test_empty_prices_gives_empty_plan(self):
        self.assertEqual(mpc_plan(room_c=26.0, prices=[], **self.ARGS), [])

    def test_cool_room_with_cheap_prices_stays_off(self):
        args = dict(self.ARGS, outdoor_c=20.0)
        self.assertEqual(mpc_plan(room_c=22.0, prices=[0.1] * 16, **args), [False] * 16)

    def test_hot_room_with_cheap_prices_turns_on_first(self):
        self.assertTrue(mpc_plan(room_c=28.0, prices=[0.01] * 16, **self.ARGS)[0])

    def test_expensive_prices_with_slightly_warm_room_stay_off(self):
        plan = mpc_plan(room_c=24.2, prices=[50.0] * 16, **self.ARGS)
        self.assertFalse(any(plan))

    def test_nonpositive_step_raises(self):
        with self.assertRaises(ValueError):
            mpc_plan(room_c=26.0, prices=[0.1], step_min=0.0, **self.ARGS)


class MpcIdleTests(unittest.TestCase):
    def test_room_2k_above_target_is_never_idled(self):
        self.assertFalse(mpc_idle([False] * 16, 26.0, 24.0, 0.5))

    def test_room_just_above_target_with_idle_plan_is_idled(self):
        self.assertTrue(mpc_idle([False] * 16, 24.2, 24.0, 0.5))

    def test_room_at_the_deadband_edge_is_idled(self):
        self.assertTrue(mpc_idle([False] * 16, 24.5, 24.0, 0.5))
        self.assertFalse(mpc_idle([False] * 16, 24.51, 24.0, 0.5))

    def test_run_step_within_the_lookahead_blocks_idle(self):
        self.assertFalse(mpc_idle([False, False, True] + [False] * 13, 24.2, 24.0, 0.5))

    def test_run_step_after_the_lookahead_does_not_block_idle(self):
        self.assertTrue(mpc_idle([False] * 4 + [True] * 12, 24.2, 24.0, 0.5))

    def test_no_plan_never_idles(self):
        self.assertFalse(mpc_idle(None, 22.0, 24.0, 0.5))
        self.assertFalse(mpc_idle([], 22.0, 24.0, 0.5))


class MpcHeatPlanTests(unittest.TestCase):
    ARGS = dict(fit_k=0.05, fit_c=0.5, fit_g=0.0, target_c=24.0, outdoor_c=10.0, heat_kw=1.1)

    def test_empty_prices_gives_empty_plan(self):
        self.assertEqual(mpc_heat_plan(room_c=19.0, prices=[], **self.ARGS), [])

    def test_cold_room_heats_first(self):
        self.assertTrue(mpc_heat_plan(room_c=19.0, prices=[0.1, 0.3] * 8, **self.ARGS)[0])

    def test_room_above_target_stays_idle_for_the_first_hour(self):
        # Outdoors at 10 C cools the room at about 0.8 K/h, so it reaches the target only after the horizon.
        self.assertFalse(any(mpc_heat_plan(room_c=26.0, prices=[0.1] * 4, **self.ARGS)))

    def test_no_heater_power_gives_no_heat(self):
        args = dict(self.ARGS, heat_kw=0.0)
        self.assertFalse(any(mpc_heat_plan(room_c=19.0, prices=[0.1] * 16, **args)))

    def test_negative_power_raises(self):
        with self.assertRaises(ValueError):
            mpc_heat_plan(room_c=19.0, prices=[0.1], **dict(self.ARGS, heat_kw=-1.0))


class MotionPresetTests(unittest.TestCase):
    def test_motion_on_gives_home(self):
        self.assertEqual(motion_preset(True, 999.0), "home")

    def test_no_sensor_gives_none(self):
        self.assertIsNone(motion_preset(None, 999.0))

    def test_motion_off_long_enough_gives_away(self):
        self.assertEqual(motion_preset(False, 30.0), "away")

    def test_motion_off_recent_gives_home(self):
        self.assertEqual(motion_preset(False, 5.0), "home")

    def test_zero_delay_raises(self):
        with self.assertRaises(ValueError):
            motion_preset(True, 1.0, away_delay_min=0.0)


class MouldTargetTests(unittest.TestCase):
    def test_mould_raises_by_bump(self):
        self.assertEqual(mould_target(21.0, True), 22.0)

    def test_no_mould_unchanged(self):
        self.assertEqual(mould_target(21.0, False), 21.0)

    def test_custom_bump(self):
        self.assertEqual(mould_target(21.0, True, bump_c=0.5), 21.5)

    def test_negative_bump_raises(self):
        with self.assertRaises(ValueError):
            mould_target(21.0, True, bump_c=-1.0)


class OutputPolarityAndClampTests(unittest.TestCase):
    def test_polarity_inverts_when_inverted(self):
        self.assertFalse(apply_polarity(True, True))

    def test_polarity_unchanged_when_not_inverted(self):
        self.assertTrue(apply_polarity(True, False))

    def test_clamp_below_lo_returns_lo(self):
        self.assertEqual(clamp_output(5.0, 10.0, 90.0), 10.0)

    def test_clamp_above_hi_returns_hi(self):
        self.assertEqual(clamp_output(95.0, 10.0, 90.0), 90.0)

    def test_clamp_inside_returns_value(self):
        self.assertEqual(clamp_output(50.0, 10.0, 90.0), 50.0)

    def test_clamp_lo_above_hi_raises(self):
        with self.assertRaises(ValueError):
            clamp_output(50.0, 90.0, 10.0)

    def test_solar_scale_equal_values_is_one(self):
        self.assertEqual(learned_solar_scale([0.2, 0.4], [0.2, 0.4]), 1.0)

    def test_solar_scale_steady_factor(self):
        self.assertEqual(learned_solar_scale([0.2, 0.4, 0.8], [0.1, 0.2, 0.4]), 2.0)

    def test_solar_scale_skips_near_zero_measured(self):
        self.assertEqual(learned_solar_scale([1.0, 0.2], [0.01, 0.1]), 2.0)

    def test_solar_scale_no_pairs_is_none(self):
        self.assertIsNone(learned_solar_scale([0.1], [0.0]))

    def test_solar_scale_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            learned_solar_scale([0.1, 0.2], [0.1])

    def test_solar_scale_clamped_high(self):
        self.assertEqual(learned_solar_scale([50.0], [0.1]), 10.0)


class EffectiveHomeTests(unittest.TestCase):
    def test_house_away_overrides_room_home(self):
        self.assertFalse(effective_home(True, "away"))

    def test_house_home_returns_room_home(self):
        self.assertTrue(effective_home(True, "home"))
        self.assertFalse(effective_home(False, "home"))

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            effective_home(True, "vacation")


class CoilDryDueTest(unittest.TestCase):
    def test_stopped_cooling_starts_drying(self):
        self.assertTrue(coil_dry_due(True, False, None, 100.0))

    def test_still_drying_inside_window(self):
        self.assertTrue(coil_dry_due(False, False, 100.0, 110.0))

    def test_past_window_is_false(self):
        self.assertFalse(coil_dry_due(False, False, 100.0, 120.0))

    def test_still_cooling_is_false(self):
        self.assertFalse(coil_dry_due(True, True, None, 100.0))

    def test_bad_dry_minutes_raises(self):
        with self.assertRaises(ValueError):
            coil_dry_due(True, False, None, 100.0, 0.0)


class BoostOutputTest(unittest.TestCase):
    def test_boost_gives_full_output(self):
        self.assertEqual(boost_output("boost", 20.0), 100.0)

    def test_comfort_passes_value_through(self):
        self.assertEqual(boost_output("comfort", 42.5), 42.5)

    def test_value_above_100_is_clamped(self):
        self.assertEqual(boost_output("comfort", 150.0), 100.0)

    def test_nan_raises_value_error(self):
        with self.assertRaises(ValueError):
            boost_output("comfort", float("nan"))


class RoundToStepTests(unittest.TestCase):
    def test_rounds_to_whole_step(self):
        self.assertEqual(round_to_step(24.04, 1.0), 24.0)

    def test_rounds_to_half_step(self):
        self.assertEqual(round_to_step(24.26, 0.5), 24.5)

    def test_zero_step_raises(self):
        with self.assertRaises(ValueError):
            round_to_step(24.0, 0)


class RoomDevicesTests(unittest.TestCase):
    def test_one_ac(self):
        self.assertEqual(room_devices({"climate": "climate.a"}), {"cooling": ["climate.a"], "heating": [], "valves": []})

    def test_second_ac_follows_first(self):
        data = {"climate": "climate.a", "climate_2": "climate.b"}
        self.assertEqual(room_devices(data)["cooling"], ["climate.a", "climate.b"])

    def test_heater_switch_is_heating(self):
        data = {"climate": "climate.a", "heater_switch": "switch.h"}
        self.assertEqual(room_devices(data)["heating"], ["switch.h"])

    def test_valve_is_valves(self):
        data = {"climate": "climate.a", "valve": "number.v"}
        self.assertEqual(room_devices(data)["valves"], ["number.v"])

    def test_empty_dict_gives_empty_lists(self):
        self.assertEqual(room_devices({}), {"cooling": [], "heating": [], "valves": []})


class ComfortEnergyBlendTests(unittest.TestCase):
    def test_blend_endpoints_and_midpoint(self):
        from custom_components.hvac_horizon.engine.features import comfort_energy_setpoint

        self.assertEqual(comfort_energy_setpoint(24.0, 26.0, 0.0), 24.0)
        self.assertEqual(comfort_energy_setpoint(24.0, 26.0, 1.0), 26.0)
        self.assertEqual(comfort_energy_setpoint(24.0, 26.0, 0.5), 25.0)

    def test_weight_outside_zero_to_one_raises(self):
        from custom_components.hvac_horizon.engine.features import comfort_energy_setpoint

        with self.assertRaises(ValueError):
            comfort_energy_setpoint(24.0, 26.0, 1.5)


class ValveExerciseTests(unittest.TestCase):
    def test_due_after_seven_days_still(self):
        from custom_components.hvac_horizon.engine.features import valve_exercise_due

        self.assertFalse(valve_exercise_due(3.0))
        self.assertTrue(valve_exercise_due(7.0))


class TrvOffsetTests(unittest.TestCase):
    def test_median_of_paired_differences(self):
        from custom_components.hvac_horizon.engine.features import trv_offset

        self.assertEqual(trv_offset([21.0, 22.0, 23.0], [20.0, 21.0, 22.0]), 1.0)

    def test_outlier_difference_is_dropped(self):
        from custom_components.hvac_horizon.engine.features import trv_offset

        self.assertEqual(trv_offset([21.0, 21.0, 40.0], [20.0, 20.0, 20.0]), 1.0)

    def test_no_pairs_gives_none(self):
        from custom_components.hvac_horizon.engine.features import trv_offset

        self.assertIsNone(trv_offset([], []))

    def test_unequal_lengths_raise(self):
        from custom_components.hvac_horizon.engine.features import trv_offset

        with self.assertRaises(ValueError):
            trv_offset([21.0], [20.0, 20.0])

    def test_corrected_setpoint_adds_offset_or_keeps_setpoint(self):
        from custom_components.hvac_horizon.engine.features import corrected_setpoint

        # A TRV reading 1.5 K warmer raises the valve target, so the valve opens more.
        self.assertEqual(corrected_setpoint(21.0, 1.5), 22.5)
        self.assertEqual(corrected_setpoint(21.0, None), 21.0)


class ResidualHeatTests(unittest.TestCase):
    def test_rise_at_zero_minutes_equals_peak(self):
        from custom_components.hvac_horizon.engine.features import _residual_rise

        self.assertEqual(_residual_rise(3.0, 0.0), 3.0)

    def test_rise_after_one_tau_is_peak_times_exp_minus_one(self):
        import math

        from custom_components.hvac_horizon.engine.features import _residual_rise

        self.assertAlmostEqual(_residual_rise(3.0, 20.0, 20.0), 3.0 * math.exp(-1))

    def test_correction_adds_rise(self):
        # The gate sees the room warmer by the rise still to come, so the heater cuts off earlier.
        from custom_components.hvac_horizon.engine.features import residual_correction

        self.assertAlmostEqual(residual_correction(21.0, 2.0, 0.0), 23.0)

    def test_bad_inputs_raise(self):
        from custom_components.hvac_horizon.engine.features import _residual_rise

        with self.assertRaises(ValueError):
            _residual_rise(2.0, -1.0)
        with self.assertRaises(ValueError):
            _residual_rise(2.0, 5.0, 0.0)
        with self.assertRaises(ValueError):
            _residual_rise(-1.0, 5.0)


class ForecastDayCountTests(unittest.TestCase):
    def test_day_counts(self):
        from custom_components.hvac_horizon.engine.features import weather_day_summary

        counts = weather_day_summary([31.0, 26.0, 20.0, 14.0, 8.0, 31.5])
        self.assertEqual(counts, {"hot": 2, "warm": 1, "mild": 1, "cool": 1, "cold": 1})


class PresenceSourcesTests(unittest.TestCase):
    def test_list_string_and_empty_values(self):
        from custom_components.hvac_horizon.engine.features import presence_sources

        self.assertEqual(presence_sources(["binary_sensor.a", "person.b"]), ["binary_sensor.a", "person.b"])
        self.assertEqual(presence_sources("binary_sensor.a"), ["binary_sensor.a"])
        self.assertEqual(presence_sources(None), [])
        self.assertEqual(presence_sources([]), [])


class MinutesUntilPresetTests(unittest.TestCase):
    def test_counts_minutes_to_the_next_block_that_selects_the_preset(self):
        blocks = [ScheduleBlock(0.0, 7.0, "eco")]
        self.assertEqual(minutes_until_preset(6.0, blocks, "comfort"), 60.0)
        self.assertEqual(minutes_until_preset(5.0, blocks, "comfort"), 120.0)

    def test_wraps_past_midnight(self):
        blocks = [ScheduleBlock(0.0, 7.0, "eco")]
        self.assertEqual(minutes_until_preset(23.0, blocks, "eco"), 60.0)

    def test_is_none_when_the_preset_never_comes(self):
        self.assertIsNone(minutes_until_preset(6.0, [ScheduleBlock(0.0, 24.0, "eco")], "comfort"))



class RaiseCoolThresholdsTests(unittest.TestCase):
    def setUp(self):
        from custom_components.hvac_horizon.engine.decide import Preset, Settings

        self.Preset = Preset
        self.settings = Settings(comfort_c=24.0, preset=Preset.COMFORT, away_c=27.0, window_delay_min=5.0, eco_c=26.0, boost_c=22.0)

    def test_every_preset_cool_threshold_rises_by_one(self):
        from dataclasses import replace

        from custom_components.hvac_horizon.engine.decide import cool_above_c, raise_cool_thresholds

        for preset in self.Preset:
            for home in (True, False):
                s = replace(self.settings, preset=preset)
                raised = raise_cool_thresholds(s, True)
                self.assertAlmostEqual(cool_above_c(raised, home), cool_above_c(s, home) + 1.0, msg=f"{preset} home={home}")

    def test_no_mould_returns_settings_unchanged(self):
        from custom_components.hvac_horizon.engine.decide import raise_cool_thresholds

        self.assertEqual(raise_cool_thresholds(self.settings, False), self.settings)

    def test_unset_eco_and_boost_stay_unset(self):
        from dataclasses import replace

        from custom_components.hvac_horizon.engine.decide import raise_cool_thresholds

        raised = raise_cool_thresholds(replace(self.settings, eco_c=None, boost_c=None), True)
        self.assertIsNone(raised.eco_c)
        self.assertIsNone(raised.boost_c)


class DeadbandTests(unittest.TestCase):
    """Each on-off decision takes its current on-state. Six readings that alternate across the threshold give one change."""

    @staticmethod
    def _changes(decide, readings):
        on = False
        changes = 0
        for reading in readings:
            new = decide(reading, on)
            changes += new != on
            on = new
        return changes

    def test_fan_co2_holds_until_100_ppm_below(self):
        decide = lambda r, on: air_quality_ventilation(r, None, running=on).run
        self.assertEqual(self._changes(decide, [1001, 999] * 3), 1)
        self.assertTrue(decide(901, True))
        self.assertFalse(decide(899, True))

    def test_fan_pm25_holds_until_3_below(self):
        decide = lambda r, on: air_quality_ventilation(None, r, running=on).run
        self.assertEqual(self._changes(decide, [25.0, 24.0] * 3), 1)
        self.assertTrue(decide(22.0, True))
        self.assertFalse(decide(21.9, True))

    def test_humidity_outputs_hold_for_3_points(self):
        dehumidify = lambda r, on: humidity_action(r, dehumidifying=on).action == "dehumidify"
        humidify = lambda r, on: humidity_action(r, humidifying=on).action == "humidify"
        self.assertEqual(self._changes(dehumidify, [60.5, 59.5] * 3), 1)
        self.assertEqual(self._changes(humidify, [39.5, 40.5] * 3), 1)
        self.assertTrue(dehumidify(57.5, True))
        self.assertFalse(dehumidify(56.9, True))
        self.assertTrue(humidify(42.5, True))
        self.assertFalse(humidify(43.1, True))

    def test_window_advice_holds_for_half_a_kelvin(self):
        decide = lambda gap, on: natural_ventilation_window(20.0, 20.0 + gap, is_open=on) == "open"
        self.assertEqual(self._changes(decide, [2.1, 1.9] * 3), 1)
        self.assertFalse(decide(1.4, True))
        # The cooling threshold and the outdoor cap move the same way.
        self.assertEqual(natural_ventilation_window(18.0, 24.0, cool_above_c=24.2, is_open=True), "open")
        self.assertEqual(natural_ventilation_window(18.0, 24.0, cool_above_c=24.2), "close")
        self.assertEqual(natural_ventilation_window(24.4, 30.0, is_open=True), "open")
        self.assertEqual(natural_ventilation_window(24.4, 30.0), "close")

    def test_economizer_holds_for_half_a_kelvin(self):
        decide = lambda gap, on: economizer_phase(20.0, 20.0 + gap, 18.0, 0.0, is_open=on) != "off"
        self.assertEqual(self._changes(decide, [2.1, 1.9] * 3), 1)
        self.assertEqual(economizer_phase(20.0, 21.4, 18.0, 0.0, is_open=True), "off")
        self.assertEqual(economizer_phase(18.0, 24.0, 18.0, 0.0, cool_above_c=24.2, is_open=True), "open_windows")
        self.assertEqual(economizer_phase(18.0, 24.0, 18.0, 0.0, cool_above_c=24.2), "off")

    def test_whole_house_fan_holds_for_half_a_kelvin(self):
        decide = lambda gap, on: whole_house_fan_allowed(False, False, 20.0, 20.0 + gap, fan_on=on)
        self.assertEqual(self._changes(decide, [2.1, 1.9] * 3), 1)
        self.assertFalse(decide(1.4, True))
        self.assertFalse(whole_house_fan_allowed(True, False, 20.0, 23.0, fan_on=True))
        self.assertFalse(whole_house_fan_allowed(False, True, 20.0, 23.0, fan_on=True))

    def test_window_actuator_holds_for_half_a_kelvin(self):
        self.assertIsNone(window_actuator_command(True, 21.9, 20.0, True, True))
        self.assertEqual(window_actuator_command(True, 21.9, 20.0, False, False), None)
        self.assertEqual(window_actuator_command(True, 21.4, 20.0, True, True), "close")


def test_dew_point_and_humid_air_rule():
    from custom_components.hvac_horizon.engine.features import dew_point_c, humid_air_blocks_opening

    assert abs(dew_point_c(25.0, 50.0) - 13.9) < 0.2
    assert abs(dew_point_c(21.0, 98.0) - 20.7) < 0.2
    assert dew_point_c(20.0, 0.0) is None
    assert humid_air_blocks_opening(None, 12.0) is False  # no outdoor humidity: nothing is blocked
    assert humid_air_blocks_opening(20.7, 13.2) is True  # muggy outdoors, dry room
    assert humid_air_blocks_opening(13.5, 13.0) is False  # within the 1 K margin
    assert humid_air_blocks_opening(21.0, None) is True  # no room reading: the 20 C dew point cap applies
    assert humid_air_blocks_opening(15.0, None) is False


def test_whole_house_fan_needs_a_room_above_the_cool_threshold():
    from custom_components.hvac_horizon.engine.features import whole_house_fan_allowed

    assert whole_house_fan_allowed(False, False, 6.0, 19.0, cool_above_c=24.0) is False
    assert whole_house_fan_allowed(False, False, 20.0, 25.0, cool_above_c=24.0) is True
    assert whole_house_fan_allowed(False, False, 20.0, 23.8, fan_on=True, cool_above_c=24.0) is True  # holds for the deadband
    assert whole_house_fan_allowed(False, False, 20.0, 23.4, fan_on=True, cool_above_c=24.0) is False


class FreeCoolingStateTests(unittest.TestCase):
    """Every legal change of phase, and a random walk that finds no other."""

    def step(self, state, minute, room_c, air_moving, cool_above_c=24.0):
        from custom_components.hvac_horizon.engine.features import free_cooling_step

        return free_cooling_step(state, now_s=minute * 60.0, room_c=room_c, cool_above_c=cool_above_c, air_moving=air_moving)

    def test_idle_runs_when_air_moves_and_returns_to_idle_when_it_ends(self):
        from custom_components.hvac_horizon.engine.features import FreeCooling

        running = self.step(FreeCooling(), 0, 25.0, True)
        self.assertEqual(running.phase, "running")
        self.assertEqual(self.step(running, 5, 25.0, False).phase, "idle")

    def test_running_gives_up_when_the_room_is_2k_above_after_30_minutes(self):
        from custom_components.hvac_horizon.engine.features import FreeCooling

        state = self.step(FreeCooling(), 0, 27.5, True)
        state = self.step(state, 25, 26.9, True)
        self.assertEqual(state.phase, "running")
        self.assertEqual(self.step(state, 30, 26.5, True).phase, "gave_up")

    def test_running_gives_up_when_the_room_falls_too_little(self):
        from custom_components.hvac_horizon.engine.features import FreeCooling

        state = self.step(FreeCooling(), 0, 25.0, True)
        self.assertEqual(self.step(state, 30, 24.8, True).phase, "gave_up")

    def test_running_continues_while_the_room_falls(self):
        from custom_components.hvac_horizon.engine.features import FreeCooling

        state = self.step(FreeCooling(), 0, 25.5, True)
        for minute, room in ((30, 25.1), (60, 24.7), (90, 24.3)):
            state = self.step(state, minute, room, True)
            self.assertEqual(state.phase, "running")

    def test_gave_up_holds_until_the_room_is_at_the_threshold(self):
        from custom_components.hvac_horizon.engine.features import FreeCooling

        gave_up = FreeCooling("gave_up")
        self.assertEqual(self.step(gave_up, 0, 24.1, True).phase, "gave_up")
        self.assertEqual(self.step(gave_up, 0, 24.1, False).phase, "gave_up")
        self.assertEqual(self.step(gave_up, 0, 24.0, False).phase, "idle")
        self.assertEqual(self.step(gave_up, 0, 24.0, True).phase, "running")

    def test_a_random_walk_makes_only_the_listed_changes(self):
        import random

        from custom_components.hvac_horizon.engine.features import FreeCooling

        legal = {("idle", "running"), ("running", "idle"), ("running", "gave_up"), ("gave_up", "idle"), ("gave_up", "running")}
        rng = random.Random(3)
        state, room = FreeCooling(), 25.0
        for minute in range(0, 50000, 5):
            room = min(29.0, max(22.0, room + rng.uniform(-0.3, 0.3)))
            after = self.step(state, minute, room, rng.random() < 0.7)
            if after.phase != state.phase:
                self.assertIn((state.phase, after.phase), legal)
                if after.phase == "gave_up":
                    self.assertGreater(room, 24.0)
            if room <= 24.0:
                self.assertNotEqual(after.phase, "gave_up")
            state = after
