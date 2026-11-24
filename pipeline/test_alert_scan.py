"""Tests for pipeline/alert_scan.py. Run: python3 -m unittest pipeline.test_alert_scan -v
(or python3 pipeline/test_alert_scan.py). Synthetic grid, no network, no real data."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alert_scan as A  # noqa: E402

TH = {
    "scan_days": [1, 7], "neighbour_deg": 1.1,
    "rules": [
        {"id": "sc", "label": "Supercell", "level": 2, "days": [1, 7], "min_points": 2,
         "when": [{"field": "scp", "op": ">=", "value": 4}]},
        {"id": "rain", "label": "Rain", "level": 3, "days": [1, 2], "min_points": 1,
         "when": [{"field": "rain", "op": ">=", "value": 80}]},
    ],
    "escalation": {"area_factor": 1.5, "area_min_extra_points": 3, "shift_deg": 1.5},
}
DATES = ["2026-11-01", "2026-11-02", "2026-11-03"]
NO_DISTRICTS = A.Districts("/nonexistent")


def blank():
    return {"cape": 0, "shear": 0, "scp": 0, "ship": 0, "dcape": 0, "rain": 0, "hail": 0, "wind": 0, "cat": 0}


def make_run(hot, run_date="2026-10-31T20:00:00Z"):
    """5x5 grid at 0.72 deg. hot = {(row, col, day_idx): {field: value}}"""
    pts = []
    for r in range(5):
        for c in range(5):
            pts.append({"lat": -25 - 0.72 * r, "lon": 150 + 0.72 * c, "tropical": False,
                        "d": [dict(blank(), **hot.get((r, c, d), {})) for d in range(3)]})
    return {"run_date": run_date, "dates": DATES, "days": DATES, "model": "m", "second_opinion": "s", "points": pts}


def square(r0, c0, n, day, **vals):
    return {(r, c, day): vals for r in range(r0, r0 + n) for c in range(c0, c0 + n)}


class Scan(unittest.TestCase):
    def run_scan(self, hot, state=None):
        return A.scan(make_run(hot), TH, state or {}, NO_DISTRICTS)

    def test_quiet_run_has_no_events(self):
        rep, st = self.run_scan({})
        self.assertEqual(rep["changes"], [])
        self.assertEqual(st["events"], [])

    def test_single_point_below_min_points_is_ignored(self):
        rep, _ = self.run_scan({(2, 2, 0): {"scp": 9}})
        self.assertEqual(rep["changes"], [])

    def test_min_points_met_gives_one_new_event(self):
        rep, _ = self.run_scan(square(1, 1, 2, 0, scp=5))
        self.assertEqual([c[0] for c in rep["changes"]], ["NEW"])
        self.assertEqual(rep["changes"][0][1]["n_points"], 4)

    def test_touching_points_cluster_distant_ones_do_not(self):
        hot = dict(square(0, 0, 2, 0, scp=5)); hot.update(square(3, 3, 2, 0, scp=5))
        rep, _ = self.run_scan(hot)
        self.assertEqual(len(rep["changes"]), 2)

    def test_one_point_rule_with_level(self):
        rep, _ = self.run_scan({(2, 2, 1): {"rain": 100}})
        ev = rep["changes"][0][1]
        self.assertEqual((ev["level"], ev["rules"]), (3, ["rain"]))

    def test_rule_day_window_respected(self):
        rep, _ = self.run_scan({(2, 2, 2): {"rain": 100}})  # day 3, rule is days 1-2
        self.assertEqual(rep["changes"], [])

    def test_missing_field_never_fires(self):
        run = make_run(square(1, 1, 2, 0, scp=5))
        for p in run["points"]:
            for d in p["d"]:
                d.pop("scp", None)
        rep, _ = A.scan(run, TH, {}, NO_DISTRICTS)
        self.assertEqual(rep["changes"], [])


class Memory(unittest.TestCase):
    def nights(self, *hots):
        state, out = {}, []
        for h in hots:
            rep, state = A.scan(make_run(h), TH, state, NO_DISTRICTS)
            out.append([c[0] for c in rep["changes"]])
        return out

    def test_unchanged_event_is_silent_after_first_email(self):
        h = square(1, 1, 2, 0, scp=5)
        self.assertEqual(self.nights(h, h, h), [["NEW"], [], []])

    def test_growth_escalates_once_then_quiet(self):
        small, big = square(1, 1, 2, 0, scp=5), square(0, 0, 4, 0, scp=5)
        self.assertEqual(self.nights(small, big, big), [["NEW"], ["ESCALATED"], []])

    def test_small_creep_does_not_escalate(self):
        a = square(1, 1, 2, 0, scp=5); b = dict(a); b[(1, 3, 0)] = {"scp": 5}
        self.assertEqual(self.nights(a, b), [["NEW"], []])

    def test_higher_level_escalates(self):
        a = square(1, 1, 2, 0, scp=5); b = dict(a); b[(1, 1, 0)] = {"scp": 5, "rain": 100}
        self.assertEqual(self.nights(a, b), [["NEW"], ["ESCALATED"]])

    def test_shrinking_is_weakened_and_disappearing_is_cleared(self):
        big, small = square(0, 0, 4, 0, scp=5), square(0, 0, 2, 0, scp=5)
        self.assertEqual(self.nights(big, small, {}), [["NEW"], ["WEAKENED"], ["CLEARED"]])

    def test_cleared_is_only_sent_once(self):
        h = square(1, 1, 2, 0, scp=5)
        self.assertEqual(self.nights(h, {}, {}), [["NEW"], ["CLEARED"], []])

    def test_event_that_was_never_told_is_not_cleared(self):
        state = {"events": [{"id": "x", "date": "2026-11-01", "points": ["a"], "told": None, "districts": [],
                             "n_points": 1}]}
        rep, _ = A.scan(make_run({}), TH, state, NO_DISTRICTS)
        self.assertEqual(rep["changes"], [])

    def test_separate_dates_are_separate_events(self):
        h = square(1, 1, 2, 0, scp=5); h.update(square(1, 1, 2, 1, scp=5))
        rep, _ = A.scan(make_run(h), TH, {}, NO_DISTRICTS)
        self.assertEqual(sorted(c[1]["date"] for c in rep["changes"]), ["2026-11-01", "2026-11-02"])

    def test_past_dates_drop_out_of_state(self):
        state = {"events": [{"id": "old", "date": "2026-10-01", "points": ["a"], "told": {"n_points": 1}}]}
        _, st = A.scan(make_run({}), TH, state, NO_DISTRICTS)
        self.assertEqual(st["events"], [])


class Geometry(unittest.TestCase):
    def test_point_in_polygon_with_hole(self):
        g = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                                                 [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
        self.assertTrue(A.in_geometry(2, 2, g))
        self.assertFalse(A.in_geometry(5, 5, g))
        self.assertFalse(A.in_geometry(12, 2, g))

    def test_local_window_uses_local_time(self):
        self.assertIn("AEST", A.local_window("2026-06-01", -27.5, 152))   # QLD, no DST
        self.assertIn("10:00", A.local_window("2026-06-01", -27.5, 152))
        self.assertIn("AWST", A.local_window("2026-06-01", -31.9, 115.9))
        self.assertIn("AEDT", A.local_window("2026-12-01", -33.9, 151.2))  # Sydney in DST


class Email(unittest.TestCase):
    def test_heartbeat_when_quiet(self):
        rep, _ = A.scan(make_run({}), TH, {}, NO_DISTRICTS)
        subject, body = A.compose_email(make_run({}), TH, rep, "note")
        self.assertIn("nothing new", subject)
        self.assertIn("not an", body)

    def test_alert_subject_counts_changes(self):
        run = make_run(square(1, 1, 2, 0, scp=5))
        rep, _ = A.scan(run, TH, {}, NO_DISTRICTS)
        subject, body = A.compose_email(run, TH, rep, "note")
        self.assertIn("1 episode", subject)
        self.assertIn("NEW", body)


class PublishedAndEpisodes(unittest.TestCase):
    TH2 = {"scan_days": [1, 7], "neighbour_deg": 1.1,
           "rules": [{"id": "storms_x", "label": "Storms", "level": 1, "days": [1, 7], "min_points": 1, "source": "published",
                      "when": [{"field": "cat", "op": ">=", "value": 3}]},
                     {"id": "rain_x", "label": "Rain", "level": 1, "days": [1, 7], "min_points": 2,
                      "when": [{"field": "rain", "op": ">=", "value": 40}]}],
           "escalation": {"area_factor": 1.5, "area_min_extra_points": 3, "shift_deg": 1.5}}

    def published_with(self, hot):
        import copy
        pub = copy.deepcopy(make_run(hot))
        pub["manual"] = {"published_at": "2026-10-31T21:00:00Z"}
        return pub

    def test_storm_rule_reads_the_published_map_not_the_raw_run(self):
        raw = make_run({})                                   # raw model: nothing
        pub = self.published_with({(2, 2, 0): {"cat": 3}})    # Josh edited one point up to MDT
        rep, _ = A.scan(raw, self.TH2, {}, NO_DISTRICTS, pub)
        self.assertEqual(len(rep["events"]), 1)
        self.assertEqual(rep["events"][0]["basis"], "published map")
        rep0, _ = A.scan(raw, self.TH2, {}, NO_DISTRICTS, None)   # no published map: falls back to raw, which is quiet
        self.assertEqual(rep0["events"], [])

    def test_raw_only_storm_signal_is_ignored_when_published_map_disagrees(self):
        raw = make_run({(2, 2, 0): {"cat": 3}})              # model says MDT
        pub = self.published_with({})                         # Josh's map says no
        rep, _ = A.scan(raw, self.TH2, {}, NO_DISTRICTS, pub)
        self.assertEqual(rep["events"], [])

    def test_non_storm_rules_still_use_the_raw_run(self):
        raw = make_run(square(1, 1, 2, 0, rain=50))
        pub = self.published_with({})
        rep, _ = A.scan(raw, self.TH2, {}, NO_DISTRICTS, pub)
        self.assertEqual(len(rep["events"]), 1)
        self.assertEqual(rep["events"][0]["basis"], "raw model run")

    def test_rolling_rain_sums_the_days_ending_on_each_day(self):
        run = make_run({(0, 0, 0): {"rain": 30}, (0, 0, 1): {"rain": 30}, (0, 0, 2): {"rain": 30}})
        A.add_rolling_rain(run)
        self.assertEqual([d["rain3"] for d in run["points"][0]["d"]], [30, 60, 90])

    def test_three_days_of_rain_is_one_multi_day_episode(self):
        hot = {}
        for d in range(3):
            hot.update(square(1, 1, 2, d, rain=50))
        run = make_run(hot)
        rep, _ = A.scan(run, self.TH2, {}, NO_DISTRICTS)
        self.assertEqual(len(rep["events"]), 3)               # three daily detections ...
        self.assertEqual(len(rep["episodes"]), 1)             # ... one episode
        self.assertEqual(rep["episodes"][0]["kind"], "multi_day")
        subject, body = A.compose_email(run, self.TH2, rep, "note")
        self.assertIn("1 episode", subject)
        self.assertIn("1 multi-day", subject)
        self.assertIn("MULTI-DAY EPISODE", body)
        self.assertEqual(body.count("MULTI-DAY EPISODE"), 1)

    def test_five_day_rule_event_starts_four_days_before_it_fires(self):
        th = dict(self.TH2, rules=[{"id": "rain5_x", "label": "5-day rain", "level": 1, "days": [1, 7], "min_points": 1,
                                    "window_days": 5, "when": [{"field": "rain5", "op": ">=", "value": 100}]}])
        run = make_run({})
        for p in run["points"][:1]:
            p["d"][2]["rain5"] = 120
        rep, _ = A.scan(run, th, {}, NO_DISTRICTS)
        ev = rep["events"][0]
        self.assertEqual(ev["date"], "2026-11-03")
        self.assertEqual(ev["start_date"], "2026-11-01")      # clipped to the first day of the run

    def test_a_single_day_stays_a_one_off(self):
        rep, _ = A.scan(make_run(square(1, 1, 2, 0, rain=50)), self.TH2, {}, NO_DISTRICTS)
        self.assertEqual(rep["episodes"][0]["kind"], "one_off")

    def test_episode_id_survives_into_the_next_scan(self):
        hot = {}
        for d in range(3):
            hot.update(square(1, 1, 2, d, rain=50))
        run = make_run(hot)
        rep, state = A.scan(run, self.TH2, {}, NO_DISTRICTS)
        rep2, _ = A.scan(make_run(hot, run_date="2026-11-01T20:00:00Z"), self.TH2, state, NO_DISTRICTS)
        self.assertEqual(rep2["episodes"][0]["episode_id"], rep["episodes"][0]["episode_id"])
        self.assertEqual(rep2["changes"], [])                  # nothing changed, so nothing to email


if __name__ == "__main__":
    unittest.main()
