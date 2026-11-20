import unittest

from custom_components.hvac_horizon.pid import Pid


def run(pid, setpoint, start, steps, dt_h=5 / 60, gain=2.0, loss=0.2, ambient=18.0):
    """First-order room: heating raises temperature at `gain` K/h per 100 % output."""
    temp = start
    history = []
    for _ in range(steps):
        out = pid.update(setpoint, temp, dt_h)
        temp += (gain * out / 100.0 - loss * (temp - ambient)) * dt_h
        history.append((out, temp))
    return history


class PidTests(unittest.TestCase):
    def test_reaches_setpoint_without_large_overshoot(self):
        pid = Pid(kp=20.0, ki=60.0, kd=0.5)
        history = run(pid, setpoint=22.0, start=18.0, steps=600)
        final = history[-1][1]
        self.assertAlmostEqual(final, 22.0, delta=0.3)
        self.assertLess(max(t for _, t in history), 22.0 + 1.0)

    def test_output_is_clamped_to_range(self):
        pid = Pid(kp=500.0, ki=0.0, kd=0.0)
        out = pid.update(setpoint=30.0, measure=10.0, dt_h=1 / 60)
        self.assertEqual(out, 100.0)
        out = pid.update(setpoint=0.0, measure=10.0, dt_h=1 / 60)
        self.assertEqual(out, 0.0)

    def test_anti_windup_limits_integral_when_saturated(self):
        pid = Pid(kp=1.0, ki=500.0, kd=0.0)
        for _ in range(200):
            pid.update(setpoint=30.0, measure=10.0, dt_h=5 / 60)  # stuck at 100 % for a long time
        # Once the room is above the setpoint the output must fall quickly, not stay high.
        out = pid.update(setpoint=20.0, measure=25.0, dt_h=5 / 60)
        self.assertLess(out, 50.0)

    def test_rejects_non_positive_step(self):
        with self.assertRaises(ValueError):
            Pid(kp=1, ki=0, kd=0).update(20, 19, 0)

    def test_no_derivative_kick_on_setpoint_step(self):
        pid = Pid(kp=0.0, ki=0.0, kd=10.0)
        pid.update(setpoint=20.0, measure=20.0, dt_h=5 / 60)
        out = pid.update(setpoint=25.0, measure=20.0, dt_h=5 / 60)  # setpoint moved, measurement did not
        self.assertAlmostEqual(out, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
