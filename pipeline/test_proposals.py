import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proposals as p

TH = {"rules": [{"id": "a", "label": "Large hail (3-6 cm)"}]}
PC = {"4350": {"lat": -27.56, "lon": 151.95}, "4000": {"lat": -27.47, "lon": 153.03}, "9999": {"lat": None, "lon": None}}
EV = {"id": "e1", "date": "2026-10-20", "lead": 3, "level": 3, "rules": ["a"], "points": ["-27.60,151.90"],
      "bbox": [-27.6, 151.9, -27.6, 151.9], "centroid": [-27.6, 151.9], "n_points": 1, "districts": ["Darling Downs"]}


class T(unittest.TestCase):
    def test_nearby_postcodes_only(self):
        r = p.build_proposals([EV], TH, PC)[0]
        self.assertEqual(r["postcodes"], {"4350": "significant"})  # Brisbane is ~110 km away; no-coordinate row skipped
        self.assertEqual(r["tier"], "significant")
        self.assertEqual(r["suggested_title"], "Large hail (3-6 cm) for Darling Downs")

    def test_highest_tier_first(self):
        lo = dict(EV, level=1, id="e0")
        self.assertEqual([x["id"] for x in p.build_proposals([lo, EV], TH, PC)], ["e1", "e0"])



class Episodes(unittest.TestCase):
    def test_week_of_events_is_one_proposal_with_union_postcodes_and_max_tier(self):
        mk = lambda d, lvl, pts: dict(EV, date=d, level=lvl, id=d, episode_id="E-1", points=pts, bbox=[-27.6, 151.9, -27.6, 151.9])
        evs = [mk("2026-10-20", 1, ["-27.60,151.90"]), mk("2026-10-21", 3, ["-27.60,151.90"]), mk("2026-10-22", 1, ["-27.60,151.90"])]
        out = p.build_episode_proposals(evs, TH, PC)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "multi_day")
        self.assertEqual((out[0]["first_date"], out[0]["last_date"]), ("2026-10-20", "2026-10-22"))
        self.assertEqual(out[0]["postcodes"], {"4350": "significant"})   # highest tier reached, once
        self.assertEqual(len(out[0]["days"]), 3)


if __name__ == "__main__":
    unittest.main()
