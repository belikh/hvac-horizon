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
