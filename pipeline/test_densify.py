import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manual_compose as m

LAT0, LON0, S = -20.0, 130.0, 0.72
DATES = ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]


def rec(cat=0, **kw):
    r = {"cat": cat, "pregate": 0, "conditional": False, "cape": 100 * cat, "shear": 10, "scp": 0, "rain": 1.0,
         "hatch": 0, "tprob": 80 if cat >= 3 else (20 if cat >= 1 else 0), "hail": 0, "flood": 0, "fire": 0,
         "wind": 0, "pop": 10}
    r.update(kw)
    return r


def run(cats, days=5):
    """cats: {(row, col): cat or a function of day}; points on the model's 0.72 degree lattice."""
    pts = []
    for (r, c), v in sorted(cats.items()):
        d = [rec(v(j) if callable(v) else v) for j in range(days)]
        pts.append({"lat": round(LAT0 - r * S, 2), "lon": round(LON0 + c * S, 2), "tropical": False, "d": d})
    return {"run_date": "2026-10-31T20:00:00Z", "days": ["d%d" % i for i in range(days)], "dates": DATES[:days],
            "points": pts}


def block(n, cat=0):
    return {(r, c): cat for r in range(n) for c in range(n)}


class Densify(unittest.TestCase):
    def test_full_lattice_has_four_times_the_squares(self):
        out, _ = m.densify(run(block(3)))
        self.assertEqual(len(out["points"]), 25)               # (2*3-1)**2: 9 model points + 16 added
        self.assertEqual(out["grid_step"], m.GRID_STEP)
        self.assertEqual(sum(1 for p in out["points"] if p.get("interp")), 16)

    def test_model_points_untouched_and_first(self):
        raw = run(block(3, 2))
        out, _ = m.densify(raw)
        self.assertEqual(out["points"][:9], raw["points"])
        self.assertTrue(all(not p.get("interp") for p in out["points"][:9]))

    def test_added_points_sit_halfway(self):
        out, _ = m.densify(run(block(2)))
        added = {m.pkey(p["lat"], p["lon"]) for p in out["points"] if p.get("interp")}
        self.assertEqual(added, {m.pkey(LAT0 - 0.36, LON0), m.pkey(LAT0, LON0 + 0.36),
                                 m.pkey(LAT0 - 0.36, LON0 + 0.72), m.pkey(LAT0 - 0.72, LON0 + 0.36),
                                 m.pkey(LAT0 - 0.36, LON0 + 0.36)})

    def test_idempotent(self):
        out, _ = m.densify(run(block(3)))
        again, _ = m.densify(out)
        self.assertIs(again, out)

    def test_edge_of_coverage(self):
        # an L of three model points: the cell between them still gets its middle point (three
        # corners), but a diagonal pair with no neighbours between them does not
        three = m.densify(run({(0, 0): 0, (1, 0): 0, (0, 1): 0}))[0]
        self.assertEqual(len(three["points"]), 3 + 2 + 1)      # two edges, one middle
        diag = m.densify(run({(0, 0): 0, (1, 1): 0}))[0]
        self.assertEqual(len(diag["points"]), 2)
        irregular = run({(0, 0): 0, (0, 1): 0})
        irregular["points"][1]["lon"] += 0.2                   # off the lattice: left alone
        self.assertIs(m.densify(irregular)[0], irregular)

    def test_uniform_field_stays_uniform(self):
        out, _ = m.densify(run(block(5, 2)))
        for p in out["points"]:
            for j, r in enumerate(p["d"]):
                # the model's own points keep 2; on days 5-8 (coarse scale) an added point reads
                # Marginal as "chance of storm" (1), the same coarse class
                self.assertEqual((r["cat"], r["tprob"]), (1 if p.get("interp") and j >= m.FINE_DAYS else 2, 20))

    def test_lone_point_is_not_grown_into_a_halo(self):
        cats = block(5); cats[(2, 2)] = 3
        out, _ = m.densify(run(cats))
        added = [p for p in out["points"] if p.get("interp")]
        self.assertTrue(all(p["d"][0]["cat"] == 0 for p in added))

    def test_solid_patch_carries_its_inside_and_not_its_outside(self):
        cats = block(7)
        for r in range(2, 5):
            for c in range(2, 5):
                cats[(r, c)] = 2
        out, _ = m.densify(run(cats))
        by = {m.pkey(p["lat"], p["lon"]): p for p in out["points"]}
        inside = by[m.pkey(LAT0 - 3 * S, LON0 + 3 * S + 0.36)]  # between two patch points, mid-patch
        outside = by[m.pkey(LAT0 - 0.36, LON0 + 0.36)]
        self.assertEqual(inside["d"][0]["cat"], 2)
        self.assertEqual(outside["d"][0]["cat"], 0)

    def test_added_points_obey_the_model_rules(self):
        cats = block(5); cats[(2, 2)] = 3; cats[(2, 3)] = 3; cats[(3, 2)] = 2
        raw = run(cats)
        for p in raw["points"]:                                # give the patch damaging wind and hail
            if p["d"][0]["cat"] >= 2:
                p["d"][0].update(wind=1, hail=1)
        out, _ = m.densify(raw)
        for p in out["points"]:
            for j, r in enumerate(p["d"]):
                c = r["cat"]
                self.assertTrue(c < 1 or r["tprob"] >= 20)
                self.assertTrue(c < 3 or r["tprob"] >= 80)
                self.assertLessEqual(r["hail"], c)
                self.assertTrue(r["wind"] == 0 or c >= 2)
                self.assertTrue(r["wind"] <= 1 or c >= 3)
                self.assertTrue(not r["hatch"] or c >= 2)

    def test_days_five_to_eight_are_coarse(self):
        cats = {k: (lambda j: 4 if j == 4 else 0) for k in block(5)}
        out, _ = m.densify(run(cats))
        self.assertEqual({p["d"][4]["cat"] for p in out["points"]}, {4})

    def test_numbers_are_means(self):
        raw = run(block(2))
        for p, cape in zip(raw["points"], (0, 100, 200, 300)):
            p["d"][0]["cape"] = cape
            p["d"][0]["rain"] = cape / 100.0
        by = {m.pkey(p["lat"], p["lon"]): p for p in m.densify(raw)[0]["points"]}
        mid = by[m.pkey(LAT0 - 0.36, LON0 + 0.36)]["d"][0]
        self.assertEqual((mid["cape"], mid["rain"]), (150, 1.5))
        self.assertIsInstance(mid["cape"], int)

    def test_frames_follow_the_points(self):
        raw = run(block(3, 1))
        cols = ["cat", "tprob", "hail", "wind", "flood", "cape", "shear", "ship", "rain"]
        frames = {1: {"day": 1, "times": ["t"] * 2, "cols": cols, "points": [
            {"lat": p["lat"], "lon": p["lon"], "f": [[1, 20, 0, 0, 0, 100, 10, 0, 1.0]] * 2} for p in raw["points"]]}}
        out, fo = m.densify(raw, frames)
        self.assertEqual({m.pkey(p["lat"], p["lon"]) for p in out["points"]},
                         {m.pkey(p["lat"], p["lon"]) for p in fo[1]["points"]})
        self.assertEqual(fo[1]["points"][:9], frames[1]["points"])
        self.assertEqual(fo[1]["points"][-1]["f"][0], [1, 20, 0, 0, 0, 100, 10, 0, 1.0])

    def test_parents(self):
        out, _ = m.densify(run(block(2)))
        par = m.node_parents(out["points"])
        self.assertEqual(sorted(len(v) for v in par.values()), [2, 2, 2, 2, 4])
        self.assertTrue(all(i >= 4 for i in par) and all(q < 4 for v in par.values() for q in v))


class ComposeOnTheFinerLattice(unittest.TestCase):
    def test_no_edits_is_the_densified_run(self):
        raw = run(block(4, 1))
        out, _, rep = m.compose(raw)
        self.assertEqual(out["points"], m.densify(raw)[0]["points"])
        self.assertTrue(all(x["source"] == "raw" for x in rep["days"]))

    def test_an_edit_on_the_finer_lattice_applies_to_its_squares(self):
        raw = m.densify(run(block(4)))[0]
        pts = raw["points"]
        target = next(i for i, p in enumerate(pts) if p.get("interp"))
        cat = [0] * len(pts)
        cat[target] = 2
        edits = {"points": [[p["lat"], p["lon"]] for p in pts], "dates": {DATES[0]: {"scale": "fine", "cat": cat}}}
        out, _, rep = m.compose(raw, None, edits)
        self.assertEqual(out["points"][target]["d"][0]["cat"], 2)
        self.assertEqual(sum(p["d"][0]["cat"] > 0 for p in out["points"]), 1)
        self.assertEqual(rep["days"][0]["changed"], 1)

    def test_an_edit_made_on_the_old_lattice_carries_over(self):
        raw = run(block(7))
        edit_cat = []
        for p in raw["points"]:
            edit_cat.append(0)
        # the forecaster painted a 3x3 patch of the model's own squares
        for i, p in enumerate(raw["points"]):
            r = round((LAT0 - p["lat"]) / S); c = round((p["lon"] - LON0) / S)
            if 2 <= r <= 4 and 2 <= c <= 4:
                edit_cat[i] = 2
        legacy = {"version": 1, "points": [[p["lat"], p["lon"]] for p in raw["points"]],
                  "dates": {DATES[0]: {"scale": "fine", "cat": edit_cat}}}
        out, _, rep = m.compose(raw, None, legacy)
        self.assertEqual(len(out["points"]), len(m.densify(raw)[0]["points"]))
        self.assertEqual(rep["days"][0]["missing"], 0)         # every added square got a call
        by = {m.pkey(p["lat"], p["lon"]): p for p in out["points"]}
        self.assertEqual(by[m.pkey(LAT0 - 3 * S, LON0 + 3 * S + 0.36)]["d"][0]["cat"], 2)   # inside the patch
        self.assertEqual(by[m.pkey(LAT0 - 0.36, LON0 + 0.36)]["d"][0]["cat"], 0)            # far from it
        # the model's own squares carry exactly what was painted
        for i, p in enumerate(raw["points"]):
            self.assertEqual(out["points"][i]["d"][0]["cat"], edit_cat[i])

    def test_an_untouched_legacy_edit_changes_nothing(self):
        raw = run(block(6))
        raw["points"][14]["d"][0].update(cat=2, tprob=20)
        legacy = {"points": [[p["lat"], p["lon"]] for p in raw["points"]],
                  "dates": {DATES[0]: {"scale": "fine", "cat": [p["d"][0]["cat"] for p in raw["points"]]}}}
        out, _, rep = m.compose(raw, None, legacy)
        dense = m.densify(raw)[0]
        self.assertEqual([p["d"][0]["cat"] for p in out["points"]], [p["d"][0]["cat"] for p in dense["points"]])
        self.assertEqual(rep["days"][0]["changed"], 0)


if __name__ == "__main__":
    unittest.main()
