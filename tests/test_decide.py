import unittest

from custom_components.hvac_horizon.decide import Mode, RoomState, Settings, decide

S = Settings(comfort_c=24.0, away_c=27.0, window_delay_min=10.0)


def state(**kw):
    base = dict(room_c=26.0, window_open_min=0.0, someone_home=True, precool_start_in_min=None)
    base.update(kw)
    return RoomState(**base)


class DecideTests(unittest.TestCase):
    def test_window_open_past_delay_pauses(self):
        a = decide(state(window_open_min=12.0), S)
        self.assertEqual(a.mode, Mode.OFF)

    def test_window_open_under_delay_does_not_pause(self):
        a = decide(state(window_open_min=5.0), S)
        self.assertEqual(a.mode, Mode.COOL)

    def test_window_beats_precool(self):
        a = decide(state(window_open_min=12.0, precool_start_in_min=0.0), S)
        self.assertEqual(a.mode, Mode.OFF)

    def test_away_precool_cools_to_comfort_when_room_is_above_it(self):
        a = decide(state(someone_home=False, room_c=26.0, precool_start_in_min=0.0), S)
        self.assertEqual((a.mode, a.setpoint_c, a.reason), (Mode.COOL, 24.0, "pre-cool starts now"))

    def test_away_precool_waits_when_planner_says_later(self):
        a = decide(state(someone_home=False, room_c=26.0, precool_start_in_min=30.0), S)
        self.assertEqual(a.mode, Mode.OFF)

    def test_away_precool_idle_when_room_is_at_target(self):
        a = decide(state(someone_home=False, room_c=23.8, precool_start_in_min=0.0), S)
        self.assertEqual(a.mode, Mode.OFF)

    def test_away_uses_away_setpoint(self):
        a = decide(state(someone_home=False, room_c=28.0), S)
        self.assertEqual((a.mode, a.setpoint_c), (Mode.COOL, 27.0))

    def test_precool_starts_when_planner_says(self):
        a = decide(state(room_c=25.0, precool_start_in_min=0.0), S)
        self.assertEqual((a.mode, a.setpoint_c), (Mode.COOL, 24.0))

    def test_precool_waits_when_planner_says_later(self):
        # Below comfort, so the only reason to cool is the planner, and it says wait.
        a = decide(state(room_c=23.5, precool_start_in_min=30.0), S)
        self.assertEqual(a.mode, Mode.OFF)

    def test_above_comfort_cools_even_before_planned_start(self):
        a = decide(state(room_c=25.0, precool_start_in_min=30.0), S)
        self.assertEqual((a.mode, a.setpoint_c), (Mode.COOL, 24.0))

    def test_hot_room_cools_to_comfort(self):
        a = decide(state(room_c=26.0), S)
        self.assertEqual((a.mode, a.setpoint_c), (Mode.COOL, 24.0))

    def test_cool_room_stays_off(self):
        a = decide(state(room_c=23.5), S)
        self.assertEqual(a.mode, Mode.OFF)


if __name__ == "__main__":
    unittest.main()


class SafetyFixTests(unittest.TestCase):
    def test_away_does_not_switch_on_for_a_cool_room(self):
        a = decide(state(someone_home=False, room_c=25.0), S)  # away_c is 27
        self.assertEqual(a.mode, Mode.OFF)

    def test_away_holds_setback_for_a_hot_room(self):
        a = decide(state(someone_home=False, room_c=28.0), S)
        self.assertEqual((a.mode, a.setpoint_c), (Mode.COOL, 27.0))


class OccupancyEcoTests(unittest.TestCase):
    def test_unoccupied_with_eco_uses_eco_setpoint(self):
        s = Settings(comfort_c=24.0, away_c=27.0, eco_c=25.5)
        a = decide(state(someone_home=False, room_c=26.5), s)
        self.assertEqual((a.mode, a.setpoint_c, a.reason), (Mode.COOL, 25.5, "eco when unoccupied"))

    def test_unoccupied_without_eco_uses_away_setpoint(self):
        a = decide(state(someone_home=False, room_c=28.0), S)
        self.assertEqual((a.mode, a.setpoint_c, a.reason), (Mode.COOL, 27.0, "away setback"))

    def test_occupied_ignores_eco(self):
        s = Settings(comfort_c=24.0, away_c=27.0, eco_c=25.5)
        a = decide(state(someone_home=True, room_c=26.0), s)
        self.assertEqual((a.mode, a.setpoint_c), (Mode.COOL, 24.0))


class PresetTests(unittest.TestCase):
    def test_eco_targets_eco_setpoint(self):
        from custom_components.hvac_horizon.decide import Preset, Settings, _preset_setpoint

        s = Settings(comfort_c=24.0, away_c=27.0, eco_c=25.5, preset=Preset.ECO)
        self.assertEqual(_preset_setpoint(s), 25.5)
        self.assertEqual(decide(state(room_c=26.2), s).setpoint_c, 25.5)

    def test_boost_targets_boost_setpoint(self):
        from custom_components.hvac_horizon.decide import Preset, Settings, _preset_setpoint

        s = Settings(comfort_c=24.0, away_c=27.0, boost_c=22.0, preset=Preset.BOOST)
        self.assertEqual(_preset_setpoint(s), 22.0)

    def test_eco_falls_back_to_comfort_when_unset(self):
        from custom_components.hvac_horizon.decide import Preset, Settings, _preset_setpoint

        s = Settings(comfort_c=24.0, away_c=27.0, preset=Preset.ECO)
        self.assertEqual(_preset_setpoint(s), 24.0)


class OutdoorLimitTests(unittest.TestCase):
    def test_cooling_blocked_below_outdoor_limit(self):
        a = decide(state(room_c=26.0, outdoor_c=12.0), S)
        self.assertEqual(a.mode, Mode.OFF)
        self.assertIn("cooling limit", a.reason)

    def test_cooling_allowed_at_or_above_outdoor_limit(self):
        a = decide(state(room_c=26.0, outdoor_c=20.0), S)
        self.assertEqual(a.mode, Mode.COOL)

    def test_unknown_outdoor_does_not_block(self):
        a = decide(state(room_c=26.0, outdoor_c=None), S)
        self.assertEqual(a.mode, Mode.COOL)


class DeadbandTests(unittest.TestCase):
    def test_off_ac_starts_only_above_target_plus_band(self):
        self.assertEqual(decide(state(room_c=24.5), S, ac_cooling=False).mode, Mode.OFF)
        self.assertEqual(decide(state(room_c=24.6), S, ac_cooling=False).mode, Mode.COOL)

    def test_cooling_ac_stops_only_below_target_minus_band(self):
        self.assertEqual(decide(state(room_c=23.5), S, ac_cooling=True).mode, Mode.COOL)
        self.assertEqual(decide(state(room_c=23.4), S, ac_cooling=True).mode, Mode.OFF)

    def test_state_inside_the_band_is_kept(self):
        for room in (23.6, 24.0, 24.5):
            self.assertEqual(decide(state(room_c=room), S, ac_cooling=True).mode, Mode.COOL)
            self.assertEqual(decide(state(room_c=room), S, ac_cooling=False).mode, Mode.OFF)

    def test_away_setback_uses_the_same_band(self):
        away = state(someone_home=False)
        self.assertEqual(decide(RoomState(**{**away.__dict__, "room_c": 27.4}), S).mode, Mode.OFF)
        self.assertEqual(decide(RoomState(**{**away.__dict__, "room_c": 27.6}), S).mode, Mode.COOL)
        self.assertEqual(decide(RoomState(**{**away.__dict__, "room_c": 26.6}), S, ac_cooling=True).mode, Mode.COOL)
