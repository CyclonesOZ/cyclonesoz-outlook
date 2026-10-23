import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alert_scan as a
import tropical as t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Four real systems from the TropicalSystem entity, 22 Oct 2026 12Z, with the categories the
# entity itself computed. Used to prove the 1-min -> 10-min conversion rather than assert my own
# arithmetic against itself.
REAL = [
    ("sh022027", 41, 1), ("ep222026", 72, 2), ("al102026", 49, 1), ("wp292026", 94, 3),
]

# A synthetic system on a track into the Queensland coast, shaped exactly like the entity's rows.
AU_SYSTEM = {
    "storm_id": "sh992026", "name": "Tropical Cyclone Test", "status": "active",
    "init_time": "2026-01-10T00:00:00.000Z",
    "current": {"lat": -14.0, "lon": 148.0, "vmax": 60},
    "consensus_track": [
        {"tau": 24, "lat": -15.0, "lon": 147.0, "r70_km": 80, "vmax_mean": 60, "member_fraction": 0.9},
        {"tau": 48, "lat": -17.0, "lon": 146.0, "r70_km": 120, "vmax_mean": 100, "member_fraction": 0.8},
        {"tau": 72, "lat": -19.0, "lon": 145.5, "r70_km": 160, "vmax_mean": 30, "member_fraction": 0.2},
    ],
}

# Hurricane off Mexico: must never raise an Australian alert.
EP_SYSTEM = {
    "storm_id": "ep222026", "name": "Tropical Cyclone Vance", "status": "active",
    "init_time": "2026-01-10T00:00:00.000Z",
    "current": {"lat": 9.8, "lon": -101.3, "vmax": 120},
    "consensus_track": [
        {"tau": 24, "lat": 10.5, "lon": -102.0, "r70_km": 100, "vmax_mean": 120, "member_fraction": 0.95},
    ],
}


def run_with(points, dates):
    return {"dates": dates, "points": [dict(lat=la, lon=lo, d=[{} for _ in dates])
                                       for la, lo in points]}


class TCategory(unittest.TestCase):
    def test_reproduces_the_entitys_own_categories(self):
        for sid, vmax, ent_cat in REAL:
            self.assertEqual(t.au_category(vmax), ent_cat, "%s miscategorised" % sid)

    def test_below_category_one_is_a_low_not_none(self):
        self.assertEqual(t.au_category(20), 0)

    def test_missing_wind_is_none_so_no_rule_fires(self):
        self.assertIsNone(t.au_category(None))

    def test_band_edges(self):
        self.assertEqual(t.au_category(34 / 0.88), 1)
        self.assertEqual(t.au_category(108 / 0.88), 5)


class TRegion(unittest.TestCase):
    def test_australian_region_accepted(self):
        self.assertTrue(t.in_au_region(-18.0, 146.0))

    def test_other_basins_rejected(self):
        self.assertFalse(t.in_au_region(9.8, -101.3))   # east Pacific
        self.assertFalse(t.in_au_region(39.5, -66.8))   # Atlantic
        self.assertFalse(t.in_au_region(21.9, 124.3))   # west Pacific, north of the AoR

    def test_relevant_uses_the_track_not_just_the_current_position(self):
        far = {"current": {"lat": 5.0, "lon": 175.0},
               "consensus_track": [{"lat": -15.0, "lon": 150.0}]}
        self.assertTrue(t.relevant(far))

    def test_a_system_entirely_outside_is_not_relevant(self):
        self.assertFalse(t.relevant(EP_SYSTEM))


class TAugment(unittest.TestCase):
    DATES = ["2026-01-10", "2026-01-11", "2026-01-12", "2026-01-13"]

    def test_point_inside_the_cone_gets_category_and_probability(self):
        run = run_with([(-17.0, 146.0)], self.DATES)
        t.augment_run(run, systems=[AU_SYSTEM])
        rec = run["points"][0]["d"][2]  # tau 48 -> 12 Jan
        self.assertEqual(rec["tc_cat"], 4)       # vmax_mean 100 kt -> 88 kt 10-min -> cat 4
        self.assertEqual(rec["tc_prob"], 0.8)

    def test_point_outside_every_cone_keeps_no_tc_fields(self):
        run = run_with([(-35.0, 140.0)], self.DATES)  # South Australia
        t.augment_run(run, systems=[AU_SYSTEM])
        for rec in run["points"][0]["d"]:
            self.assertNotIn("tc_cat", rec)
            self.assertNotIn("tc_prob", rec)

    def test_a_hurricane_in_another_basin_touches_nothing(self):
        run = run_with([(-17.0, 146.0)], self.DATES)
        t.augment_run(run, systems=[EP_SYSTEM])
        for rec in run["points"][0]["d"]:
            self.assertNotIn("tc_cat", rec)

    def test_no_systems_is_a_no_op_not_a_crash(self):
        run = run_with([(-17.0, 146.0)], self.DATES)
        msg = t.augment_run(run, systems=[])
        self.assertIn("no Australian-region systems", msg)
        self.assertNotIn("tc_cat", run["points"][0]["d"][0])

    def test_no_token_returns_no_systems_rather_than_raising(self):
        self.assertEqual(t.fetch_systems(app_id="", token=""), [])

    def test_cone_never_shrinks_below_the_floor(self):
        tiny = dict(AU_SYSTEM, consensus_track=[
            {"tau": 24, "lat": -15.0, "lon": 147.0, "r70_km": 5, "vmax_mean": 60,
             "member_fraction": 0.9}])
        run = run_with([(-15.3, 147.0)], self.DATES)  # ~33 km away: inside the 60 km floor
        t.augment_run(run, systems=[tiny])
        self.assertIn("tc_cat", run["points"][0]["d"][1])


class TRules(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "alerts", "thresholds.json")) as f:
            th = json.load(f)
        self.rules = {r["id"]: r for r in th["rules"]}
        self.bars = th["region_bars"]

    def fires(self, rid, rec):
        return a.rule_fires(self.rules[rid], rec, "tropical_north", self.bars)

    def test_tiers_follow_the_rulebook(self):
        low = {"tc_cat": 0, "tc_prob": 0.9}
        cat1 = {"tc_cat": 1, "tc_prob": 0.9}
        cat3 = {"tc_cat": 3, "tc_prob": 0.9}
        cat5 = {"tc_cat": 5, "tc_prob": 0.9}
        self.assertTrue(self.fires("tc_minor", low))
        self.assertFalse(self.fires("tc_mod", low))
        self.assertTrue(self.fires("tc_mod", cat1))
        self.assertFalse(self.fires("tc_sig", cat1))
        self.assertTrue(self.fires("tc_sig", cat3))
        self.assertFalse(self.fires("tc_major", cat3))
        self.assertTrue(self.fires("tc_major", cat5))

    def test_low_ensemble_agreement_is_gated_out(self):
        self.assertFalse(self.fires("tc_minor", {"tc_cat": 0, "tc_prob": 0.2}))

    def test_rarer_tiers_accept_lower_agreement(self):
        # The rulebook lowers the probability bar for rarer events; a cat 5 at 25% still shows.
        self.assertTrue(self.fires("tc_major", {"tc_cat": 5, "tc_prob": 0.25}))
        self.assertFalse(self.fires("tc_minor", {"tc_cat": 5, "tc_prob": 0.25}))

    def test_absent_fields_never_fire(self):
        for rid in ("tc_minor", "tc_mod", "tc_sig", "tc_major"):
            self.assertFalse(self.fires(rid, {}), "%s fired on a point with no cyclone" % rid)


if __name__ == "__main__":
    unittest.main()
