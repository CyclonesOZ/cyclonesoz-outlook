"""Check the thresholds against the Front Line alerts Josh has actually issued.

Josh, 23 Oct 2026: "Compare it to what I've previously issued for frontline alerts to make sure
that you're not hallucinating."

For each issued alert this finds the archived model run for its date, takes the grid points
within RADIUS_KM of the alert's anchor postcodes, and reports what the model showed there
against what the thresholds would now fire. A threshold that misses an alert Josh issued is
too high; one that fires hard where he issued nothing is too low.

    python3 pipeline/calibrate_against_issued.py

Read-only: loads archived runs and prints a table. Sends nothing, writes nothing.
"""
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alert_scan as a
from regions import Regions, resolve_value

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RADIUS_KM = 50.0  # same join radius proposals.py uses for postcode -> firing cell
TIER_RANK = {"minor": 1, "moderate": 2, "significant": 3, "major": 4}
LEVEL_TIER = {1: "minor", 2: "moderate", 3: "significant", 4: "major"}


def km(lat1, lon1, lat2, lon2):
    dlat = (lat2 - lat1) * 111.0
    dlon = (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def load():
    with open(os.path.join(ROOT, "alerts", "issued-calibration.json")) as f:
        cal = json.load(f)
    with open(os.path.join(ROOT, "data", "postcodes", "postcodes.json")) as f:
        pcs = json.load(f)
    th = a.load_json(os.path.join(ROOT, "alerts", "thresholds.json"))
    return cal, pcs, th, Regions.load()


def run_for(date_str):
    p = os.path.join(ROOT, "docs", "archive", "raw", "%s.json" % date_str)
    return a.load_run(p) if os.path.exists(p) else None


def main():
    cal, pcs, th, R = load()
    bars = th.get("region_bars")
    rules = th["rules"]

    print("Thresholds vs the alerts Josh actually issued")
    print("archived run = the model run for the alert's own date; peak = max over its area, days 1-5")
    print()
    hdr = "%-14s %-21s %-6s  %-18s %7s %5s  %-26s %s"
    print(hdr % ("alert", "hazard", "his", "region(s)", "rain", "cat", "rules that would fire", "verdict"))
    print("-" * 132)

    misses, hits, nodata = 0, 0, 0
    for al in cal["alerts"]:
        date = al["issued_at"][:10]
        run = run_for(date)
        coords = [(pcs[p]["lat"], pcs[p]["lon"]) for p in al["anchors"]
                  if p in pcs and pcs[p].get("lat") is not None]
        regs = sorted({R.region_for(la, lo) for la, lo in coords})
        if run is None or not coords:
            print(hdr % (al["alert_no"], al["hazard"], al["max_tier"], ",".join(regs)[:18],
                         "-", "-", "(no archived run)", "SKIP"))
            nodata += 1
            continue

        # grid points within RADIUS_KM of any anchor postcode
        near = [pi for pi, p in enumerate(run["points"])
                if any(km(p["lat"], p["lon"], la, lo) <= RADIUS_KM for la, lo in coords)]
        if not near:
            print(hdr % (al["alert_no"], al["hazard"], al["max_tier"], ",".join(regs)[:18],
                         "-", "-", "(no grid points near)", "SKIP"))
            nodata += 1
            continue

        ndays = min(5, len(run["dates"]))
        peak_rain = peak_cat = 0
        fired = {}
        for pi in near:
            p = run["points"][pi]
            reg = R.region_for(p["lat"], p["lon"])
            for di in range(min(ndays, len(p["d"]))):
                rec = p["d"][di]
                peak_rain = max(peak_rain, rec.get("rain") or 0)
                peak_cat = max(peak_cat, rec.get("cat") or 0)
                for r in rules:
                    if (r["days"][0] - 1 <= di <= r["days"][1] - 1
                            and a.rule_fires(r, rec, reg, bars, p["d"], di)):
                        fired[r["id"]] = max(fired.get(r["id"], 0), r["level"])

        got_level = max(fired.values()) if fired else 0
        his = TIER_RANK[al["max_tier"]]
        if not fired:
            verdict = "MISS (model quiet here)"
            misses += 1
        elif got_level >= his:
            verdict = "ok"
            hits += 1
        else:
            verdict = "under by %d tier" % (his - got_level)
            hits += 1
        names = ",".join(sorted(fired)) or "-"
        print(hdr % (al["alert_no"], al["hazard"], al["max_tier"], ",".join(regs)[:18],
                     "%.0fmm" % peak_rain, "%.0f" % peak_cat, names[:26], verdict))

    print("-" * 132)
    print("fired: %d   missed: %d   skipped: %d   of %d issued alerts"
          % (hits, misses, nodata, len(cal["alerts"])))
    print()
    print("'his' is the highest tier in Josh's own assignment. 'under by N' means the scan would")
    print("have raised the event but at a lower tier than he chose - a prompt to review, not a bug:")
    print("he sees the draft and sets the tier. A MISS is the one that matters: the model showed")
    print("nothing over that area, so no threshold change would surface it.")


if __name__ == "__main__":
    main()
