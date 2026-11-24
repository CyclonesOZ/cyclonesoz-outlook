"""Tests for the fine hazard rules and drawn outlines (pipeline/fine_areas.py). Needs numpy, scipy,
contourpy (alert-scan.yml installs them)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fine_areas as F  # noqa: E402
import morning_email as M  # noqa: E402


def cell(**kw):
    base = {"mucape": 0.0, "shear6": 0.0, "scp": 0.0, "stp": 0.0, "ship": 0.0, "mucin": 0.0, "rain": 0.0, "t500": -10.0,
            "ship_peak": 0.0, "cape_at_ship": 0.0, "frzl": 4000.0, "dcape": 0.0, "dd700": 0.0, "lr03": 0.0, "dcp": 0.0}
    base.update(kw)
    return {k: np.array([v], float) for k, v in base.items()}


class RulesTest(unittest.TestCase):
    def test_category_follows_the_outlook(self):
        self.assertEqual(F.categorise(cell(mucape=2000, shear6=18, scp=7, rain=5))[0][0], 3)    # MDT on SCP
        self.assertEqual(F.categorise(cell(mucape=2000, shear6=18, scp=7, rain=0))[0][0], 0)    # no rain, no storms
        self.assertEqual(F.categorise(cell(mucape=2000, shear6=18, scp=7, rain=5, mucin=-120))[0][0], 2)  # capped

    def test_large_hail_is_not_capped_by_the_category(self):
        # Josh, 20 Nov: the outlook caps hail, but large and very large count when they emerge
        c = cell(ship_peak=3.2, cape_at_ship=2000, frzl=4000)
        self.assertEqual(F.hail_tier(c, np.array([2]))[0], 3)
        self.assertEqual(F.hail_tier(c, np.array([0]))[0], 0)                     # still needs storms
        self.assertEqual(F.hail_tier(cell(ship_peak=3.2, cape_at_ship=2000, frzl=5200), np.array([2]))[0], 2)  # melts

    def test_damaging_wind_is_taken_down_by_the_models_overstatement(self):
        # Thargomindah, 20 Nov 2026: the outlook called this destructive (DCP 2.1)
        c = cell(dcape=1112, dd700=12.3, shear6=18.7, mucape=2052, dcp=2.1)
        raw = F.wind_potential(c, np.array([3]))[0]
        self.assertGreaterEqual(raw, 125)
        self.assertEqual(F.tier_kmh(np.array([raw / 1.3]))[0], 1)                 # damaging, not destructive

    def test_heavy_rain_needs_rain_above_normal_and_moist_air(self):
        z = np.zeros(1)
        self.assertEqual(F.rain_tiers(np.array([70.0]), z, np.array([65.0]), np.array([55.0]), np.array([60.0]))[0], 2)
        self.assertEqual(F.rain_tiers(np.array([70.0]), z, np.array([45.0]), np.array([55.0]), np.array([60.0]))[0], 0)
        self.assertEqual(F.rain_tiers(np.array([15.0]), z, np.array([65.0]), np.array([40.0]), np.array([20.0]))[0], 0)
        self.assertEqual(F.rain_tiers(np.array([70.0]), z, np.array([80.0]), np.array([76.0]), np.array([300.0]))[0], 0)  # wet season normal

    def test_climate_maps(self):
        cl = F.Climate()
        rain, pw = cl.on(np.array([-12.46]), np.array([130.84]), 11)              # Darwin, November
        self.assertTrue(90 < rain[0, 0] < 160 and 70 < pw[0, 0] < 82)
        rain, pw = cl.on(np.array([-37.81]), np.array([144.96]), 7)               # Melbourne, July
        self.assertTrue(30 < rain[0, 0] < 70 and 30 < pw[0, 0] < 42)


class OutlineTest(unittest.TestCase):
    def test_a_traced_ring_comes_out_smooth(self):
        t = np.linspace(0, 2 * np.pi, 400, endpoint=False)
        jag = np.column_stack([140 + 2 * np.cos(t) + 0.03 * np.sign(np.sin(40 * t)), -25 + 1.5 * np.sin(t)])
        r = F.smooth_ring(jag)
        self.assertTrue(F.RING_POINTS[0] <= len(r) <= F.RING_POINTS[1])
        turn = np.diff(np.unwrap(np.arctan2(*np.diff(np.vstack([r, r[:2]]), axis=0).T[::-1])))
        self.assertLess(np.abs(turn).max(), 0.5)                                  # no sharp corners left
        back = F.unpack_rings(F.pack_rings([r]))[0]
        self.assertLess(np.abs(back - np.round(r, 2)).max(), 0.011)

    def test_metros(self):
        self.assertEqual(F.near_metro(-33.87, 151.0), "Sydney")
        self.assertIsNone(F.near_metro(-25.42, 142.65))                           # Windorah

    def test_specks_merge_into_a_neighbour_or_hide_unless_on_a_metro(self):
        def item(pts, fam="storms", level=1):
            return {"fam": fam, "level": level, "pts": pts, "events": [], "dates": ["2026-11-20"], "rules": [],
                    "first_seen": "9", "n_pc": 0}
        big = item([(-30.0, 146.0), (-30.72, 146.0), (-30.0, 146.72), (-30.72, 146.72)])
        near = item([(-30.0, 148.0)])                                            # 1.3 deg away: merged
        far = item([(-20.0, 130.0)])                                             # hidden
        sydney = item([(-33.86, 151.2)])                                         # kept
        run = {"points": [{"lat": -30.0, "lon": 146.0}, {"lat": -30.72, "lon": 146.0}]}
        kept, hidden = M.settle_tiny([big, near, far, sydney], None, run)
        self.assertEqual(hidden, 1)
        self.assertEqual(len(kept), 2)
        self.assertIn((-30.0, 148.0), kept[0]["pts"])
        self.assertIs(kept[1], sydney)


if __name__ == "__main__":
    unittest.main()
