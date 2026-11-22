import unittest

from custom_components.hvac_horizon.room_model.pid import Pid


def run(pid, setpoint, start, steps, dt_h=5 / 60, gain=2.0, loss=0.2, ambient=18.0):
    """First-order room: heating raises temperature at `gain` K/h per 100 % output."""
    temp = start
    history = []
    for _ in range(steps):
        out = pid.update(setpoint, temp, dt_h, outdoor=ambient)
        temp += (gain * out / 100.0 - loss * (temp - ambient)) * dt_h
        history.append((out, temp))
    return history


class PidTests(unittest.TestCase):
    def test_reaches_setpoint_without_large_overshoot(self):
        pid = Pid(kp=20.0, ki=60.0, kd=0.5)
        history = run(pid, setpoint=22.0, start=18.0, steps=600)
        self.assertAlmostEqual(history[-1][1], 22.0, delta=0.3)
        self.assertLess(max(t for _, t in history), 23.0)

    def test_output_is_clamped(self):
        pid = Pid(kp=500.0, ki=0.0, kd=0.0)
        self.assertEqual(pid.update(setpoint=30.0, measure=10.0, dt_h=1 / 60), 100.0)
        self.assertEqual(pid.update(setpoint=0.0, measure=10.0, dt_h=1 / 60), 0.0)

    def test_anti_windup_limits_integral_when_saturated(self):
        pid = Pid(kp=1.0, ki=500.0, kd=0.0)
        for _ in range(200):
            pid.update(setpoint=30.0, measure=10.0, dt_h=5 / 60)
        # The integral was stuck at its top. Once the room is above the setpoint, it must unwind.
        for _ in range(3):
            out = pid.update(setpoint=20.0, measure=25.0, dt_h=5 / 60)
        self.assertLessEqual(out, 0.0)

    def test_rejects_non_positive_step(self):
        with self.assertRaises(ValueError):
            Pid(kp=1, ki=0, kd=0).update(20, 19, 0)

    def test_no_derivative_kick_on_setpoint_step(self):
        pid = Pid(kp=0.0, ki=0.0, kd=10.0)
        pid.update(setpoint=20.0, measure=20.0, dt_h=5 / 60)
        self.assertAlmostEqual(pid.update(setpoint=25.0, measure=20.0, dt_h=5 / 60), 0.0, places=6)

    def test_trapezoidal_integral_matches_average_error(self):
        pid = Pid(kp=0.0, ki=10.0, kd=0.0)
        pid.update(setpoint=21.0, measure=20.0, dt_h=0.1)  # error 1, first step uses it alone
        out = pid.update(setpoint=22.0, measure=20.0, dt_h=0.1)  # error 2; average error 1.5
        self.assertAlmostEqual(out, 10.0 * 1.5 * 0.1 + 10.0 * 1.0 * 0.1, places=6)

    def test_outdoor_feedforward_adds_output(self):
        pid = Pid(kp=0.0, ki=0.0, kd=0.0, ke=2.0)
        self.assertAlmostEqual(pid.update(setpoint=22.0, measure=22.0, dt_h=0.1, outdoor=12.0), 20.0)

    def test_reset_keeps_integral_unless_asked(self):
        pid = Pid(kp=0.0, ki=100.0, kd=0.0)
        pid.update(setpoint=21.0, measure=20.0, dt_h=0.1)
        pid.update(setpoint=21.0, measure=20.0, dt_h=0.1)
        before = pid._integral
        pid.reset()
        self.assertEqual(pid._integral, before)
        pid.reset(clear_integral=True)
        self.assertEqual(pid._integral, 0.0)

    def test_frozen_integral_does_not_grow(self):
        frozen = Pid(kp=0.0, ki=60.0, kd=0.0)
        free = Pid(kp=0.0, ki=60.0, kd=0.0)
        for _ in range(100):
            frozen.update(setpoint=24.0, measure=23.7, dt_h=5 / 60, freeze_integral=True)
            free.update(setpoint=24.0, measure=23.7, dt_h=5 / 60)
        self.assertEqual(frozen._integral, 0.0)
        self.assertGreater(free._integral, 0.0)

    def test_set_gains_changes_output(self):
        pid = Pid(kp=1.0, ki=0.0, kd=0.0)
        pid.set_gains(kp=3.0)
        self.assertAlmostEqual(pid.update(setpoint=21.0, measure=20.0, dt_h=0.1), 3.0)


if __name__ == "__main__":
    unittest.main()
