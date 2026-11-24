#!/usr/bin/env python3
"""Tests for radar_obs: valid-day rule, point reduction, and the reliability tally."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import radar_obs as r


class ValidDay(unittest.TestCase):
    def test_nightly_window_files_under_previous_local_day(self):
        # 19:52 UTC on the 24th is 05:52 AEST on the 25th; the window is the 24th's local day.
        win = {"start": "2026-11-23T19:52:00Z", "end": "2026-11-24T19:52:00Z"}
        self.assertEqual(r.valid_day(win), dt.date(2026, 11, 24))


class Reduce(unittest.TestCase):
    def test_box_stats_and_cover(self):
        # 1 deg x 1 deg world, 101 x 101 px. Left half no radar, a 30 mm blob on the right.
        mm = np.zeros((101, 101), dtype=np.float32)
        mm[:, :40] = np.nan
        mm[40:60, 70:90] = 30.0
        out = r.reduce_points(mm, [0.0, 0.0, 1.0, 1.0], [(0.5, 0.8)])
        cover, mean, mx, f1, f10, f25 = out["0.5,0.8"]
        self.assertEqual(mx, 30.0)
        self.assertGreater(f25, 0.0)
        self.assertEqual(cover, 1.0)               # box (0.44..1.0 lon) sits wholly on radar
        out2 = r.reduce_points(mm, [0.0, 0.0, 1.0, 1.0], [(0.5, 0.2)])
        self.assertLess(out2["0.5,0.2"][0], r.MIN_RADAR_COVER)   # mostly no radar: excluded


class Tally(unittest.TestCase):
    def test_counts_by_category_and_skips_no_radar_and_interp(self):
        run = {"days": ["Tue 24 Nov"], "points": [
            {"lat": -30, "lon": 150, "d": [{"cat": 3}]},
            {"lat": -31, "lon": 150, "d": [{"cat": 0}]},
            {"lat": -32, "lon": 150, "d": [{"cat": 3}]},
            {"lat": -33, "lon": 150, "interp": True, "d": [{"cat": 4}]}]}
        snap = {"cells": {
            "-30,150": [1.0, 20, 60, 0.9, 0.5, 0.1],     # heavy under an MDT
            "-31,150": [1.0, 0, 0, 0, 0, 0],             # dry under none
            "-32,150": [0.1, 0, 0, 0, 0, 0],             # no radar: excluded
            "-33,150": [1.0, 20, 60, 0.9, 0.5, 0.1]}}
        acc = r.tally({"2026-11-23": run}, {"2026-11-24": snap})
        self.assertEqual(acc[0][3]["n"], 1)
        self.assertEqual(acc[0][3]["heavy"], 1)
        self.assertEqual(acc[0][0]["n"], 1)
        self.assertEqual(acc[0][0]["heavy"], 0)
        self.assertNotIn(4, acc[0])               # interpolated point never scored

    def test_rate_hidden_below_minimum(self):
        acc = {0: {2: {"n": 10, "wet": 5, "heavy": 5, "intense": 5}}}
        self.assertIsNone(r.rates(acc)[0]["heavy_rate"])


if __name__ == "__main__":
    unittest.main()
