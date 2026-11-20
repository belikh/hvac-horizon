import unittest

from custom_components.hvac_horizon.solar_model import clear_sky_irradiance, solar_elevation_azimuth, window_irradiance


class SolarModelTest(unittest.TestCase):
    def test_sun_below_horizon_gives_zero_irradiance(self):
        elevation, _ = solar_elevation_azimuth(-27.5, 153.0, 14.0, 355)  # midnight local
        self.assertEqual(clear_sky_irradiance(elevation), 0.0)

    def test_brisbane_summer_noon_elevation_above_70(self):
        elevation, _ = solar_elevation_azimuth(-27.5, 153.0, 2.0, 355)  # about 12:00 solar time
        self.assertGreater(elevation, 70.0)

    def test_invalid_latitude_raises(self):
        with self.assertRaises(ValueError):
            solar_elevation_azimuth(91.0, 0.0, 12.0, 100)

    def test_clear_sky_zero_at_elevation_zero(self):
        self.assertEqual(clear_sky_irradiance(0.0), 0.0)

    def test_irradiance_at_most_1353(self):
        self.assertLessEqual(clear_sky_irradiance(90.0), 1353.0)

    def test_window_facing_sun_beats_window_facing_away(self):
        direct = clear_sky_irradiance(30.0)
        self.assertGreater(window_irradiance(direct, 30.0, 90.0, 90.0), window_irradiance(direct, 30.0, 90.0, 270.0))

    def test_window_facing_away_at_low_elevation_gives_zero(self):
        self.assertEqual(window_irradiance(clear_sky_irradiance(10.0), 10.0, 90.0, 270.0), 0.0)


if __name__ == "__main__":
    unittest.main()
