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
