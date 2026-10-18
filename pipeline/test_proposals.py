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


if __name__ == "__main__":
    unittest.main()
