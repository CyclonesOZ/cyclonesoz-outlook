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


class TAccumulation(unittest.TestCase):
    BARS = {"rain_3day": {"default": 60, "by_region": {"interior_arid": 40}}}
    RULE = {"id": "r3", "when": [{"field": "rain", "op": ">=", "accumulate_days": 3,
                                  "value": {"$ref_region_bar": "rain_3day"}}]}

    def series(self, *rains):
        return [{"rain": r} for r in rains]

    def test_sums_three_consecutive_days(self):
        s = self.series(20, 20, 20, 0, 0)
        self.assertTrue(a.rule_fires(self.RULE, s[0], None, self.BARS, s, 0))

    def test_single_big_day_alone_is_not_enough_below_the_bar(self):
        s = self.series(50, 0, 0)
        self.assertFalse(a.rule_fires(self.RULE, s[0], None, self.BARS, s, 0))

    def test_window_starts_at_the_evaluated_day(self):
        s = self.series(0, 0, 30, 30, 30)
        self.assertFalse(a.rule_fires(self.RULE, s[0], None, self.BARS, s, 0))
        self.assertTrue(a.rule_fires(self.RULE, s[2], None, self.BARS, s, 2))

    def test_partial_window_at_the_end_never_fires(self):
        # Two days left and a 3-day window: a partial sum must not pass as a small total.
        s = self.series(100, 100)
        self.assertFalse(a.rule_fires(self.RULE, s[0], None, self.BARS, s, 0))

    def test_without_series_an_accumulating_rule_cannot_fire(self):
        s = self.series(50, 50, 50)
        self.assertFalse(a.rule_fires(self.RULE, s[0], None, self.BARS))

    def test_missing_days_count_as_zero_not_as_a_crash(self):
        s = [{"rain": 30}, {}, {"rain": 30}]
        self.assertTrue(a.rule_fires(self.RULE, s[0], "interior_arid", self.BARS, s, 0))

    def test_region_bar_applies_to_the_accumulated_total(self):
        s = self.series(15, 15, 15)  # 45 total
        self.assertTrue(a.rule_fires(self.RULE, s[0], "interior_arid", self.BARS, s, 0))  # bar 40
        self.assertFalse(a.rule_fires(self.RULE, s[0], None, self.BARS, s, 0))            # bar 60


class TFrost(unittest.TestCase):
    """Frost had no rules at all before 23 Oct 2026. It is absolute, not an anomaly, so the
    region gate is what stops a 0 C rule being meaningless in the tropics."""

    def setUp(self):
        import extra_hazards as eh
        self.eh = eh
        with open(os.path.join(ROOT, "alerts", "thresholds.json")) as f:
            self.th = json.load(f)
        self.bars = self.th["region_bars"]
        self.rules = {r["id"]: r for r in self.th["rules"]}

    def test_streak_needs_two_consecutive_nights(self):
        self.assertEqual(self.eh.frost_nights([-1, 2, -1]), [0, 0, 0])
        self.assertEqual(self.eh.frost_nights([-1, -1, -1]), [0, 2, 3])

    def test_a_missing_night_breaks_the_streak_rather_than_counting_as_mild(self):
        self.assertEqual(self.eh.frost_nights([-1, None, -1]), [0, 0, 0])

    def test_zero_counts_as_frost(self):
        self.assertEqual(self.eh.frost_nights([0, 0]), [0, 2])

    def _fires(self, rule_id, tmin, nights, region):
        rec = {"tmin": tmin, "frost_nights": nights}
        return a.rule_fires(self.rules[rule_id], rec, region, self.bars)

    def test_cropping_country_fires_at_zero_on_the_second_night(self):
        self.assertTrue(self._fires("frost_minor", -0.5, 2, "murray_darling"))
        self.assertFalse(self._fires("frost_minor", -0.5, 1, "murray_darling"))

    def test_the_tropics_never_fire_frost(self):
        for reg in ("tropical_north", "tropical_qld_coast", "pilbara_gascoyne"):
            self.assertFalse(self._fires("frost_minor", -3, 3, reg),
                             "%s should not report frost" % reg)

    def test_alpine_needs_a_genuinely_cold_night(self):
        self.assertFalse(self._fires("frost_minor", -2, 3, "alpine"))
        self.assertTrue(self._fires("frost_minor", -6, 3, "alpine"))

    def test_severe_tier_needs_a_colder_night_than_minor(self):
        self.assertTrue(self._fires("frost_minor", -1, 2, "sa_southeast"))
        self.assertFalse(self._fires("frost_mod", -1, 2, "sa_southeast"))
        self.assertTrue(self._fires("frost_mod", -4, 2, "sa_southeast"))

    def test_severe_bar_is_colder_than_the_frost_bar_in_every_region(self):
        R = Regions.load()
        mild = self.bars["frost_tmin"]["by_region"]
        sev = self.bars["frost_tmin_severe"]["by_region"]
        for rid in [r["id"] for r in R.regions]:
            self.assertLessEqual(sev[rid], mild[rid],
                                 "%s: severe frost bar is not at or below the frost bar" % rid)


class TIssuedAlertRecall(unittest.TestCase):
    """Every Front Line alert Josh has issued must still be surfaced by the thresholds.

    This is the regression guard on Josh's instruction of 23 Oct 2026: compare against what he
    has actually issued. If a threshold change drops one of his real alerts, this fails.
    """

    def test_all_issued_alerts_with_an_archived_run_still_fire(self):
        import calibrate_against_issued as cali
        cal, pcs, th, R = cali.load()
        bars = th.get("region_bars")
        missed = []
        for al in cal["alerts"]:
            run = cali.run_for(al["issued_at"][:10])
            coords = [(pcs[p]["lat"], pcs[p]["lon"]) for p in al["anchors"]
                      if p in pcs and pcs[p].get("lat") is not None]
            if run is None or not coords:
                continue
            fired = False
            for p in run["points"]:
                if not any(cali.km(p["lat"], p["lon"], la, lo) <= cali.RADIUS_KM
                           for la, lo in coords):
                    continue
                reg = R.region_for(p["lat"], p["lon"])
                for di in range(min(5, len(p["d"]))):
                    for r in th["rules"]:
                        if (r["days"][0] - 1 <= di <= r["days"][1] - 1
                                and a.rule_fires(r, p["d"][di], reg, bars, p["d"], di)):
                            fired = True
                            break
                    if fired:
                        break
                if fired:
                    break
            if not fired:
                missed.append(al["alert_no"])
        self.assertEqual(missed, [], "thresholds would no longer surface these issued alerts: %s"
                                     % missed)


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
