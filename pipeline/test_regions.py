import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alert_scan as a
from regions import Regions, resolve_value

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SPEC = {
    "default": "d",
    "regions": [
        {"id": "narrow", "label": "Narrow", "lat": [-38.0, -37.0], "lon": [146.0, 147.0]},
        {"id": "broad", "label": "Broad", "lat": [-40.0, -30.0], "lon": [140.0, 150.0]},
        {"id": "d", "label": "Default", "lat": [-9.0, -8.0], "lon": [1.0, 2.0]},
    ],
}


class TRegionLookup(unittest.TestCase):
    def setUp(self):
        self.R = Regions(SPEC)

    def test_first_match_wins_so_narrow_beats_broad(self):
        self.assertEqual(self.R.region_for(-37.5, 146.5), "narrow")

    def test_broad_claims_what_narrow_does_not(self):
        self.assertEqual(self.R.region_for(-35.0, 145.0), "broad")

    def test_outside_everything_falls_to_default_never_none(self):
        self.assertEqual(self.R.region_for(-20.0, 120.0), "d")

    def test_missing_coords_fall_to_default(self):
        self.assertEqual(self.R.region_for(None, None), "d")

    def test_bounds_are_min_inclusive_max_exclusive(self):
        # A shared edge must belong to exactly one region, or a point gets two bars.
        self.assertEqual(self.R.region_for(-38.0, 146.0), "narrow")  # min edge: inside
        self.assertEqual(self.R.region_for(-37.0, 146.5), "broad")   # max edge: not narrow

    def test_default_must_be_a_declared_region(self):
        with self.assertRaises(ValueError):
            Regions({"default": "nope", "regions": [{"id": "x", "label": "X",
                                                     "lat": [0, 1], "lon": [0, 1]}]})


class TResolveValue(unittest.TestCase):
    BARS = {"rain_heavy": {"default": 75, "by_region": {"tropical_north": 100}}}

    def test_plain_number_passes_through(self):
        self.assertEqual(resolve_value(75, "anything"), 75)

    def test_inline_by_region(self):
        raw = {"default": 10, "by_region": {"alpine": 99}}
        self.assertEqual(resolve_value(raw, "alpine"), 99)
        self.assertEqual(resolve_value(raw, "interior_arid"), 10)

    def test_named_bar_resolves_per_region(self):
        ref = {"$ref_region_bar": "rain_heavy"}
        self.assertEqual(resolve_value(ref, "tropical_north", self.BARS), 100)
        self.assertEqual(resolve_value(ref, "interior_arid", self.BARS), 75)

    def test_region_none_uses_default(self):
        self.assertEqual(resolve_value({"$ref_region_bar": "rain_heavy"}, None, self.BARS), 75)

    def test_unknown_bar_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            resolve_value({"$ref_region_bar": "nope"}, "alpine", self.BARS)

    def test_dict_with_no_default_raises(self):
        with self.assertRaises(ValueError):
            resolve_value({"by_region": {"alpine": 1}}, "interior_arid")


class TRuleFiresRegional(unittest.TestCase):
    BARS = {"rain_heavy": {"default": 75, "by_region": {"interior_arid": 40,
                                                        "tropical_qld_coast": 120}}}
    RULE = {"id": "r", "when": [{"field": "rain", "op": ">=",
                                 "value": {"$ref_region_bar": "rain_heavy"}}]}

    def test_same_rain_fires_in_the_desert_and_not_on_the_tropical_coast(self):
        rec = {"rain": 50}
        self.assertTrue(a.rule_fires(self.RULE, rec, "interior_arid", self.BARS))
        self.assertFalse(a.rule_fires(self.RULE, rec, "tropical_qld_coast", self.BARS))

    def test_tropical_coast_needs_its_higher_bar(self):
        self.assertTrue(a.rule_fires(self.RULE, {"rain": 130}, "tropical_qld_coast", self.BARS))

    def test_no_region_uses_national_default(self):
        self.assertTrue(a.rule_fires(self.RULE, {"rain": 80}, None, self.BARS))
        self.assertFalse(a.rule_fires(self.RULE, {"rain": 70}, None, self.BARS))

    def test_plain_numeric_rules_still_work_unchanged(self):
        rule = {"id": "x", "when": [{"field": "cat", "op": ">=", "value": 3}]}
        self.assertTrue(a.rule_fires(rule, {"cat": 4}))
        self.assertFalse(a.rule_fires(rule, {"cat": 2}))

    def test_missing_field_never_fires(self):
        self.assertFalse(a.rule_fires(self.RULE, {}, "interior_arid", self.BARS))


class TRealConfig(unittest.TestCase):
    """The shipped regions.json and thresholds.json must agree with each other."""

    def setUp(self):
        self.R = Regions.load()
        with open(os.path.join(ROOT, "alerts", "thresholds.json")) as f:
            self.th = json.load(f)

    def test_every_grid_point_gets_a_region_by_an_explicit_box(self):
        with open(os.path.join(ROOT, "data", "grid.json")) as f:
            grid = json.load(f)

        def explicit(lat, lon):
            for r in self.R.regions:
                lo_lat, hi_lat = r["lat"]
                lo_lon, hi_lon = r["lon"]
                if lo_lat <= lat < hi_lat and lo_lon <= lon < hi_lon:
                    return r["id"]
            return None

        unmatched = [(lat, lon) for lat, lon in grid if explicit(lat, lon) is None]
        self.assertEqual(unmatched, [], "%d grid points match no region box and would silently "
                                        "take the default bar: %s" % (len(unmatched), unmatched[:5]))

    def test_no_region_is_dead_weight(self):
        with open(os.path.join(ROOT, "data", "grid.json")) as f:
            grid = json.load(f)
        used = {self.R.region_for(lat, lon) for lat, lon in grid}
        unused = [r["id"] for r in self.R.regions if r["id"] not in used]
        self.assertEqual(unused, [], "regions with no grid points: %s" % unused)

    def test_every_bar_covers_every_region(self):
        for name, bar in self.th["region_bars"].items():
            missing = [r["id"] for r in self.R.regions if r["id"] not in bar["by_region"]]
            self.assertEqual(missing, [], "bar %r has no value for %s" % (name, missing))

    def test_every_referenced_bar_exists(self):
        bars = self.th["region_bars"]
        for rule in self.th["rules"]:
            for c in rule["when"]:
                v = c["value"]
                if isinstance(v, dict) and "$ref_region_bar" in v:
                    self.assertIn(v["$ref_region_bar"], bars,
                                  "rule %s refers to a missing bar" % rule["id"])

    def test_extreme_bar_is_above_heavy_bar_everywhere(self):
        heavy = self.th["region_bars"]["rain_heavy"]
        extreme = self.th["region_bars"]["rain_extreme"]
        for rid in [r["id"] for r in self.R.regions]:
            h = heavy["by_region"][rid]
            x = extreme["by_region"][rid]
            self.assertGreater(x, h, "%s: extreme bar %s is not above heavy bar %s" % (rid, x, h))
        self.assertGreater(extreme["default"], heavy["default"])

    def test_cities_land_in_the_right_region(self):
        for name, lat, lon, want in [
            ("Darwin", -12.46, 130.84, "tropical_north"),
            ("Cairns", -16.92, 145.77, "tropical_qld_coast"),
            ("Perth", -31.95, 115.86, "southwest_wa"),
            ("Alice Springs", -23.70, 133.88, "interior_arid"),
            ("Sydney", -33.87, 151.21, "nsw_qld_coast"),
            ("Brisbane", -27.47, 153.03, "nsw_qld_coast"),
            ("Adelaide", -34.93, 138.60, "sa_southeast"),
            ("Hobart", -42.88, 147.33, "tasmania"),
            ("Melbourne", -37.81, 144.96, "vic_south"),
            ("Mount Hotham", -36.98, 147.13, "alpine"),
        ]:
            self.assertEqual(self.R.region_for(lat, lon), want, "%s went to the wrong region" % name)


if __name__ == "__main__":
    unittest.main()
