"""Tests for the morning 3-day severe email (pipeline/morning_email.py). Stdlib only."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alert_scan  # noqa: E402
import morning_email as M  # noqa: E402

TH = {"rules": [
    {"id": "storms_minor", "level": 1, "label": "Severe storms (MDT)"},
    {"id": "wind_mod", "level": 2, "label": "Destructive winds (125+ km/h)"},
    {"id": "rain3_minor", "level": 1, "label": "Persistent rain (3-day region bar)"},
    {"id": "gust_minor", "level": 1, "label": "Damaging wind gusts (90+ km/h)"},
]}
DATES = ["2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22"]


def rec(**kw):
    base = {"cape": 0, "shear": 0, "scp": 0, "ship": 0, "dcape": 0, "rain": 0, "hail": 0, "wind": 0, "cat": 0, "stp": 0}
    base.update(kw)
    return base


def event(date, pts, rules, first_seen=None):
    return {"date": date, "lead": DATES.index(date) + 1, "points": ["%.2f,%.2f" % p for p in pts], "rules": rules,
            "districts": ["Test District"], "first_seen": first_seen}


def run_with(points):
    return {"run_date": "2026-11-18T20:00:00Z", "model": "ecmwf_ifs025", "dates": DATES,
            "points": [{"lat": lat, "lon": lon, "d": d} for (lat, lon), d in points.items()]}


class Districts:
    features = []


REAL_DISTRICTS = alert_scan.Districts(alert_scan.DISTRICTS)


class MorningEmailTest(unittest.TestCase):
    def test_same_area_over_three_days_is_one_item(self):
        evs = [event(d, [(-30.0, 146.0), (-30.7, 146.0)], ["storms_minor", "wind_mod"]) for d in DATES[:3]]
        items = M.build_items(evs, TH)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["dates"], DATES[:3])
        self.assertEqual(items[0]["level"], 2)

    def test_day_four_is_left_out(self):
        self.assertEqual(M.build_items([event(DATES[3], [(-30.0, 146.0)], ["storms_minor"])], TH), [])

    def test_far_apart_clusters_do_not_chain_into_one_area(self):
        # Clusters 1.4 degrees apart in a line, on alternating days: plain linkage made one item
        # 11 degrees long out of these. The span cap must stop it.
        evs = [event(DATES[i % 3], [(-15.0 - 1.4 * i, 134.0)], ["storms_minor"]) for i in range(9)]
        items = M.build_items(evs, TH)
        self.assertGreater(len(items), 1)
        for it in items:
            lats = [p[0] for p in it["pts"]]
            self.assertLessEqual(max(lats) - min(lats), M.MAX_SPAN_DEG)

    def test_storms_and_rain_in_one_cluster_give_two_items(self):
        items = M.build_items([event(DATES[0], [(-36.4, 146.7)] * 1, ["storms_minor", "rain3_minor"])], TH)
        self.assertEqual(sorted(it["fam"] for it in items), ["rain", "storms"])

    def test_higher_tier_ranks_first(self):
        evs = [event(DATES[0], [(-36.0, 147.0), (-36.5, 147.0), (-36.0, 147.5)], ["rain3_minor"]),
               event(DATES[1], [(-20.0, 139.0)], ["wind_mod"])]
        items = M.build_items(evs, TH)
        self.assertEqual(items[0]["fam"], "storms")

    def test_one_spot_one_day_minor_is_a_smaller_signal(self):
        it = M.build_items([event(DATES[0], [(-20.0, 139.0)], ["storms_minor"])], TH)[0]
        self.assertFalse(M.is_main(it))

    def test_compose_reads_like_the_brief(self):
        pts = [(-30.0, 146.0), (-30.7, 146.0), (-30.0, 146.7)]
        run = run_with({p: [rec(cape=2500, shear=40, wind=2, cat=3, rain=12, dcape=900, scp=3)] * 4 for p in pts})
        subject, body = M.compose(run, TH, [event(DATES[1], pts, ["storms_minor", "wind_mod"])], REAL_DISTRICTS, ["test data"])
        self.assertIn("Severe storms possible in western NSW", subject)
        self.assertIn("At a glance:", body)
        self.assertIn("Post Alert writer", body)
        self.assertIn("Type: Severe thunderstorms (two outlines). Impact potential: Moderate.", body)
        self.assertIn("Valid from: Fri 20 Nov 8 am AWST (11 am AEDT)", body)
        notes = body.split("Notes for Claude (paste): ")[1].split("\n")[0]
        self.assertIn("destructive wind gusts of 125 km/h or more", notes)
        for jargon in ("SCP", "DCAPE", "SHIP", "STP"):  # the writer would copy these into member text
            self.assertNotIn(jargon, notes)

    def test_nothing_found_still_sends_a_short_email(self):
        subject, body = M.compose(run_with({}), TH, [], Districts(), [])
        self.assertEqual(subject, "3-day severe watch: nothing at SWPA level")
        self.assertIn("Nothing in the next 3 days reaches an SWPA threshold.", body)

    def test_areas_are_named_by_district(self):
        pl = M.Places(REAL_DISTRICTS)
        self.assertEqual(pl.area_name([(-31.5, 145.0)]), "western NSW")  # near Cobar: Josh's own example
        self.assertEqual(pl.area_name([(-36.4, 146.7)]), "north-east VIC")  # Beechworth
        self.assertEqual(pl.area_name([(-12.5, 131.0)]), "the Top End")
        self.assertEqual(pl.area_name([(-16.9, 145.6)]), "Far North QLD")  # Cairns, which the compass got wrong
        self.assertEqual(pl.area_name([(-27.9, 141.5), (-27.9, 141.5), (-28.8, 139.5), (-28.8, 139.5)]),
                         "the Channel Country and north-east SA")

    def test_offshore_points_fall_back_to_a_compass(self):
        pl = M.Places(REAL_DISTRICTS)
        self.assertTrue(pl.area_name([(-12.0, 124.0)]).endswith("WA"))  # Timor Sea, north of the Kimberley

    def test_daily_key_is_the_perth_date(self):
        at = dt.datetime(2026, 11, 19, 20, 5, tzinfo=dt.timezone.utc)  # 4:05 am Fri in Perth
        self.assertEqual(M.daily_key(at), "morning-severe/2026-11-20")

    def test_no_key_means_no_send_and_no_error(self):
        old = os.environ.pop("RESEND_API_KEY", None)
        try:
            self.assertIn("RESEND_API_KEY is not set", M.send("s", "b", "k"))
        finally:
            if old is not None:
                os.environ["RESEND_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
