import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extra_hazards as x
import build_climatology as c

TH = {"extra_hazards": {"heatwave": {"tmax_anomaly_c": {"minor": 12, "moderate": 14, "significant": 17, "major": 21}}}}


class T(unittest.TestCase):
    def test_day_level_needs_warm_night(self):
        self.assertEqual(x.day_level(13, 2, [12, 14, 17, 21]), 1)
        self.assertEqual(x.day_level(22, 0.5, [12, 14, 17, 21]), 4)
        self.assertEqual(x.day_level(22, 0, [12, 14, 17, 21]), 0)
        self.assertEqual(x.day_level(None, 1, [12, 14, 17, 21]), 0)

    def test_two_consecutive_days_needed(self):
        self.assertEqual(x.heat_tiers([0, 3, 0, 3, 0]), [0, 0, 0, 0, 0])
        self.assertEqual(x.heat_tiers([0, 2, 3, 1, 0]), [0, 2, 2, 1, 0])

    def test_snow_72h_window(self):
        self.assertEqual(x.rolling_sum([20, 20, 20, 20]), [20, 40, 60, 60])

    def test_augment_and_leap_day(self):
        run = {"dates": ["2026-02-27", "2026-02-28"], "points": [{"lat": -36.4, "lon": 148.3, "d": [{}, {}]}]}
        cl = {"-36.40,148.30": {"tmax": [250] * 366, "tmin": [100] * 366}}
        daily = {"-36.40,148.30": {"2026-02-27": (38.0, 12.0, 30.0), "2026-02-28": (39.0, 13.0, 30.0)}}
        x.augment_run(run, TH, daily=daily, climate=cl)
        self.assertEqual([d["heat"] for d in run["points"][0]["d"]], [1, 1])  # +13 then +14 max with warm nights: the pair holds at the lower, Minor
        self.assertEqual([d["snow72"] for d in run["points"][0]["d"]], [30.0, 60.0])

    def test_no_climatology_skips_heat_only(self):
        run = {"dates": ["2026-02-27"], "points": [{"lat": -1.0, "lon": 1.0, "d": [{}]}]}
        x.CLIMATE = "/nonexistent"
        msg = x.augment_run(run, TH, daily={"-1.00,1.00": {"2026-02-27": (40, 20, 0)}})
        self.assertEqual(run["points"][0]["d"][0]["heat"], 0)
        self.assertIn("no climatology", msg)

    def test_climatology_means(self):
        dates = ["1991-03-05", "1992-03-05", "1991-03-06"]
        m = c.day_means(dates, [20.0, 22.0, None])
        self.assertEqual(m[c.climate_index(3, 5)], 210)


if __name__ == "__main__":
    unittest.main()
