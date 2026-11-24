import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import episodes as E


def ev(date, rules, pts, level=1, centroid=(-27.0, 152.0), id=None, first_seen="2026-10-19T00:00:00Z"):
    return {"date": date, "rules": rules, "points": pts, "level": level, "n_points": len(pts), "centroid": list(centroid),
            "id": id or date.replace("-", "") + "-1", "first_seen": first_seen, "districts": ["Darling Downs"]}


class T(unittest.TestCase):
    def test_week_of_rain_is_one_episode(self):
        evs = [ev("2026-10-2%d" % d, ["rain5_minor"], ["-27.0,152.0", "-27.7,152.0"], level=1 + (d == 3)) for d in range(1, 6)]
        eps = E.annotate(evs)
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0]["kind"], "multi_day")
        self.assertEqual(eps[0]["n_event_days"], 5)
        self.assertEqual(eps[0]["peak_level"], 2)
        self.assertEqual(eps[0]["peak_date"], "2026-10-23")
        self.assertTrue(all(e["episode_id"] == eps[0]["episode_id"] for e in evs))

    def test_accumulation_event_starts_at_the_beginning_of_its_window(self):
        a = dict(ev("2026-10-24", ["rain5_minor"], ["-27.0,152.0"]), start_date="2026-10-20")
        eps = E.link_events([a])
        self.assertEqual(eps[0]["first_date"], "2026-10-20")
        self.assertEqual(eps[0]["first_detected"], "2026-10-24")
        self.assertEqual(eps[0]["span_days"], 5)

    def test_single_day_is_a_one_off(self):
        eps = E.link_events([ev("2026-10-21", ["hail_minor"], ["-27.0,152.0"])])
        self.assertEqual(eps[0]["kind"], "one_off")

    def test_one_dry_day_gap_still_one_episode_but_two_does_not(self):
        a = ev("2026-10-20", ["rain_minor"], ["-27.0,152.0"])
        self.assertEqual(len(E.link_events([a, ev("2026-10-22", ["rain_minor"], ["-27.0,152.0"])])), 1)
        self.assertEqual(len(E.link_events([a, ev("2026-10-23", ["rain_minor"], ["-27.0,152.0"])])), 2)

    def test_different_hazards_or_places_stay_apart(self):
        a = ev("2026-10-20", ["rain_minor"], ["-27.0,152.0"])
        self.assertEqual(len(E.link_events([a, ev("2026-10-21", ["fire_minor"], ["-27.0,152.0"])])), 2)
        far = ev("2026-10-21", ["rain_minor"], ["-35.0,140.0"], centroid=(-35.0, 140.0))
        self.assertEqual(len(E.link_events([a, far])), 2)

    def test_storm_rule_families_group_together(self):
        a = ev("2026-10-20", ["storms_minor"], ["-27.0,152.0"])
        b = ev("2026-10-21", ["hail_minor"], ["-27.0,152.0"])
        self.assertEqual(len(E.link_events([a, b])), 1)

    def test_episode_id_is_stable_when_early_days_drop_out(self):
        evs = [ev("2026-10-2%d" % d, ["rain_minor"], ["-27.0,152.0"], id="2026102%d-1" % d,
                  first_seen="2026-10-18T20:00:00Z") for d in range(0, 4)]
        first = E.annotate(evs)[0]["episode_id"]
        self.assertEqual(first, "E-20261020-1")
        # next morning: the first day has passed; events carry the id their stored copies had
        self.assertEqual(E.annotate(evs[1:])[0]["episode_id"], "E-20261020-1")


if __name__ == "__main__":
    unittest.main()
