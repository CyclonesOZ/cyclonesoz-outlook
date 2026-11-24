#!/usr/bin/env python3
"""
Radar verification feed for the convective outlook (layer 2 of the hindcast).

WHY
---
pipeline/verify.py scores every archived run against NOAA's rain-gauge analysis. That is
independent, but it is two days late, coarse (0.5 deg) and blind to isolated storms that fall
between gauges. The CyclonesOZ app already publishes a radar rainfall total at 0.0125 deg
(models.cyclonesoz.com/obs/accum/), the same one the Weather tab shows, corrected against
gauges. This script uses that as a second, sharper observation of where heavy rain fell.

THE CATCH THE SOURCE FORCES
---------------------------
The feed keeps ONLY the newest frame; there is no history to backfill from. So the job has two
halves that must both run every night:
  1. SNAPSHOT  fetch the 24 h total, reduce it to a few numbers per outlook grid point, and
               store them under docs/archive/radar/<valid date>.json. Radar history starts the
               day this first runs, and grows from there.
  2. SCORE     for every archived run whose valid day now has a snapshot, tally what actually
               fell under each forecast category, per lead day and per source (the raw model
               run, and the outlook as published after Josh's edits). Written to
               docs/archive/radar_skill.json.

The scorecard is a RELIABILITY table (how often does heavy rain verify under TSTM / MRGL / MDT /
HIGH?), not just hit/miss, because that is what a threshold decision needs: if MDT does not
verify more often than MRGL, MDT is not earning its tier.

WHAT IT CAN AND CANNOT SAY
--------------------------
CAN:    where heavy or widespread rain fell, to ~1 km, inside each outlook cell.
CANNOT: hail, wind gusts or tornadoes -- a rainfall total says nothing about them. It also
        cannot split convective rain from frontal rain, so a wet cool-season day reads as a
        "storm" here. Radar-less areas (outback, ocean) are excluded, not scored as dry.
        Every number is a HEAVY-RAIN score and is labelled as such. It is not a severe-weather
        skill score; hail and wind need storm reports, which are a later layer.

VALID-DAY RULE
--------------
The job runs just after 19:52 UTC (about 06:00 AEST), so the 24 h window covers the previous
local day. A snapshot is filed under the AEST date of the window's midpoint. A snapshot whose
window is short, or whose feed is stale, is refused rather than filed as a day it is not.

USAGE
    python3 pipeline/radar_obs.py             # snapshot (if new) then score everything
    python3 pipeline/radar_obs.py --score     # score only, no network
"""

import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "docs", "archive")
RAW_DIR = os.path.join(ARCHIVE, "raw")
SNAP_DIR = os.path.join(ARCHIVE, "radar")
OUT_PATH = os.path.join(ARCHIVE, "radar_skill.json")

BASE = "https://models.cyclonesoz.com/obs/accum"
WINDOW = "24h"
AEST = dt.timedelta(hours=10)
MAX_FEED_AGE_H = 3          # refuse a feed whose index is older than this
MIN_RADAR_COVER = 0.6       # a point is scored only if this much of its box has radar

# Half-width of the box around each outlook point. The grid is ~0.72 deg, so +/-0.36 deg is the
# cell the point stands for.
HALF_DEG = 0.36

# What counts as "it rained" in a cell, as the share of the box over each depth. The depths are
# 24 h totals. Provisional: set from first principles, to be re-tuned once there is a season.
OCC = {
    "wet":     ("f1",  0.10),    # >=10% of the cell over 1 mm: a wet day
    "heavy":   ("f10", 0.02),    # >=2% over 10 mm: a proper storm-sized fall
    "intense": ("f25", 0.002),   # >=0.2% over 25 mm: a localised downpour
}
MIN_FOR_SCORE = 200            # smallest cell count at which a ratio is printed as a rate

CATS = ["none", "TSTM", "MRGL", "MDT", "HIGH"]


def log(msg):
    print(msg, flush=True)


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "cyclonesoz-outlook-verify"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def now_utc():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def parse_t(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def valid_day(win):
    """The AEST date a window belongs to: the date of its midpoint."""
    start, end = parse_t(win["start"]), parse_t(win["end"])
    return ((start + (end - start) / 2) + AEST).date()


def grid_points():
    """Real (non-interpolated) outlook points, taken from the newest raw run."""
    names = sorted(f for f in os.listdir(RAW_DIR) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f))
    if not names:
        return []
    with open(os.path.join(RAW_DIR, names[-1])) as f:
        run = json.load(f)
    return [(p["lat"], p["lon"]) for p in run.get("points", []) if not p.get("interp")]


def decode(png_bytes, idx):
    """24 h radar total (mm) as a float array, NaN where there is no radar."""
    import numpy as np
    from PIL import Image
    a = np.array(Image.open(io.BytesIO(png_bytes))).astype(np.uint32)
    raw = a[..., 0] * 256 + a[..., 1]        # R high byte, G low byte; B unused
    mm = raw.astype(np.float32) * float(idx.get("scale", 0.05)) + float(idx.get("offset", 0.0))
    mm[raw == int(idx.get("nodata", 65535))] = np.nan
    return mm


def reduce_points(mm, bbox, pts):
    """Per point: [radar cover, mean mm, max mm, f1, f10, f25] over its box."""
    import numpy as np
    lon0, lat0, lon1, lat1 = bbox            # west, south, east, north
    h, w = mm.shape
    px_x = (lon1 - lon0) / (w - 1)
    px_y = (lat1 - lat0) / (h - 1)           # rows run north -> south
    out = {}
    for lat, lon in pts:
        x0 = int(round((lon - HALF_DEG - lon0) / px_x))
        x1 = int(round((lon + HALF_DEG - lon0) / px_x))
        y0 = int(round((lat1 - (lat + HALF_DEG)) / px_y))
        y1 = int(round((lat1 - (lat - HALF_DEG)) / px_y))
        x0, x1, y0, y1 = max(x0, 0), min(x1, w - 1), max(y0, 0), min(y1, h - 1)
        if x1 <= x0 or y1 <= y0:
            continue
        box = mm[y0:y1 + 1, x0:x1 + 1]
        valid = box[~np.isnan(box)]
        cover = valid.size / box.size
        if valid.size == 0:
            out["%s,%s" % (lat, lon)] = [0.0, 0, 0, 0, 0, 0]
            continue
        out["%s,%s" % (lat, lon)] = [
            round(float(cover), 2), round(float(valid.mean()), 2), round(float(valid.max()), 1),
            round(float((valid >= 1).mean()), 3), round(float((valid >= 10).mean()), 4),
            round(float((valid >= 25).mean()), 4)]
    return out


def snapshot():
    """Fetch and file today's 24 h total. Returns the valid date filed, or None."""
    try:
        idx = json.loads(get(BASE + "/index.json", 60))
    except Exception as e:
        log("  radar index unavailable: %s" % e)
        return None
    win = (idx.get("windows") or {}).get(WINDOW)
    if not win:
        log("  radar index has no %s window" % WINDOW)
        return None
    age_h = (now_utc() - parse_t(idx["generated"])).total_seconds() / 3600
    if age_h > MAX_FEED_AGE_H:
        log("  radar feed is %.1f h old -- refusing to file it as a fresh day" % age_h)
        return None
    if win.get("covered_min") != win.get("expected_min"):
        log("  %s window is short (%s of %s min) -- not filing" %
            (WINDOW, win.get("covered_min"), win.get("expected_min")))
        return None
    end_hr = parse_t(win["end"]).hour
    if not 18 <= end_hr <= 23:
        # The nightly job runs ~19:52 UTC, so the window is the previous local day. A window
        # ending at another hour is a day split across two local dates, so it would be mis-filed.
        log("  %s window ends %02d:xx UTC, outside the nightly 18-23 UTC slot -- not filing" % (WINDOW, end_hr))
        return None
    day = valid_day(win)
    path = os.path.join(SNAP_DIR, "%s.json" % day.isoformat())
    if os.path.exists(path):
        log("  radar snapshot for %s already filed" % day)
        return day
    pts = grid_points()
    if not pts:
        log("  no outlook grid to reduce onto")
        return None
    url = "%s/%s/%s.png?g=%sx%s" % (BASE, WINDOW, win["stamp"], idx["width"], idx["height"])
    try:
        mm = decode(get(url, 180), idx)
    except Exception as e:
        log("  radar frame failed: %s" % e)
        return None
    cells = reduce_points(mm, idx["bbox"], pts)
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "valid": day.isoformat(), "window": WINDOW, "start": win["start"], "end": win["end"],
            "source": "CyclonesOZ radar rainfall total (RainViewer radar, gauge-corrected)",
            "fields": ["cover", "mean_mm", "max_mm", "f1", "f10", "f25"],
            "half_deg": HALF_DEG, "cells": cells}, f, separators=(",", ":"))
    log("  filed radar snapshot for %s (%d points, %d with radar)" %
        (day, len(cells), sum(1 for v in cells.values() if v[0] >= MIN_RADAR_COVER)))
    return day


def load_snapshots():
    snaps = {}
    if not os.path.isdir(SNAP_DIR):
        return snaps
    for fn in sorted(os.listdir(SNAP_DIR)):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", fn):
            with open(os.path.join(SNAP_DIR, fn)) as f:
                snaps[fn[:-5]] = json.load(f)
    return snaps


def parse_day_label(label, run_date):
    m = re.match(r"[A-Za-z]{3}\s+(\d{1,2})\s+([A-Za-z]{3})", label.strip())
    if not m:
        return None
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    if m.group(2).lower() not in months:
        return None
    mi = months.index(m.group(2).lower()) + 1
    for yr in (run_date.year, run_date.year + 1):
        try:
            d = dt.date(yr, mi, int(m.group(1)))
        except ValueError:
            continue
        if 0 <= (d - run_date).days <= 370:
            return d
    return None


def load_runs(folder):
    runs = {}
    if not os.path.isdir(folder):
        return runs
    for fn in sorted(os.listdir(folder)):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", fn):
            try:
                with open(os.path.join(folder, fn)) as f:
                    runs[fn[:-5]] = json.load(f)
            except Exception as e:
                log("  skipping unreadable %s: %s" % (fn, e))
    return runs


def tally(runs, snaps):
    """{lead: {cat: counts}} for one source of runs, over every run-day that has a snapshot."""
    acc = {}
    for run_key, run in runs.items():
        try:
            rd = dt.date(*[int(x) for x in run_key.split("-")])
        except ValueError:
            continue
        for lead, label in enumerate(run.get("days") or []):
            v = parse_day_label(label, rd)
            snap = snaps.get(v.isoformat()) if v else None
            if not snap:
                continue
            cells = snap["cells"]
            for p in run.get("points", []):
                if p.get("interp"):
                    continue
                d = p.get("d", [])
                if lead >= len(d):
                    continue
                s = cells.get("%s,%s" % (p["lat"], p["lon"]))
                if not s or s[0] < MIN_RADAR_COVER:
                    continue
                cat = int((d[lead] or {}).get("cat") or 0)
                b = acc.setdefault(lead, {}).setdefault(cat, {"n": 0, "wet": 0, "heavy": 0, "intense": 0})
                b["n"] += 1
                fields = {"f1": s[3], "f10": s[4], "f25": s[5]}
                for name, (fld, thr) in OCC.items():
                    if fields[fld] >= thr:
                        b[name] += 1
    return acc


def rates(acc):
    rows = []
    for lead in sorted(acc):
        for cat in sorted(acc[lead]):
            b = acc[lead][cat]
            row = {"lead": lead, "cat": cat, "label": CATS[cat], "n": b["n"]}
            for name in OCC:
                row[name] = b[name]
                row[name + "_rate"] = round(b[name] / b["n"], 3) if b["n"] >= MIN_FOR_SCORE else None
            rows.append(row)
    return rows


def pooled(acc):
    """Lead days pooled: one row per category."""
    tot = {}
    for lead in acc:
        for cat, b in acc[lead].items():
            t = tot.setdefault(cat, {"n": 0, "wet": 0, "heavy": 0, "intense": 0})
            for k in t:
                t[k] += b[k]
    out = []
    for cat in sorted(tot):
        t = tot[cat]
        row = {"cat": cat, "label": CATS[cat], "n": t["n"]}
        for name in OCC:
            row[name + "_rate"] = round(t[name] / t["n"], 3) if t["n"] >= MIN_FOR_SCORE else None
        out.append(row)
    return out


def score():
    snaps = load_snapshots()
    if not snaps:
        log("No radar snapshots filed yet -- nothing to score.")
        return 0
    sources = {"raw model run": load_runs(RAW_DIR), "published (as edited)": load_runs(ARCHIVE)}
    result = {}
    for name, runs in sources.items():
        acc = tally(runs, snaps)
        result[name] = {"by_lead_cat": rates(acc), "by_cat": pooled(acc)}
    out = {
        "generated": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verifies": "HEAVY-RAIN occurrence under each forecast category, from the radar 24 h total. "
                    "NOT hail, wind or tornado, and it cannot separate frontal from convective rain.",
        "definitions": {k: "share of the cell over %s mm >= %s" % (
            {"f1": "1", "f10": "10", "f25": "25"}[v[0]], v[1]) for k, v in OCC.items()},
        "radar_days": sorted(snaps),
        "min_cells_to_show_a_rate": MIN_FOR_SCORE,
        "sources": result,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=1)

    log("")
    log("Radar reliability -- %d radar day(s): %s .. %s" % (len(snaps), min(snaps), max(snaps)))
    log("Share of cells under each category where heavy rain (>=2% of the cell over 10 mm) fell.")
    log("A rate rises with category if the categories mean something. Blank = fewer than %d cells."
        % MIN_FOR_SCORE)
    for name, r in result.items():
        log("")
        log("  %s" % name)
        log("  %-6s %8s %8s %8s %8s" % ("cat", "cells", "wet", "heavy", "intense"))
        for row in r["by_cat"]:
            fmt = lambda x: "-" if x is None else "%.1f%%" % (x * 100)
            log("  %-6s %8d %8s %8s %8s" % (row["label"], row["n"], fmt(row["wet_rate"]),
                                           fmt(row["heavy_rate"]), fmt(row["intense_rate"])))
    log("")
    log("NOT VERIFIED HERE: hail, wind, tornadoes. This is rainfall only.")
    log("Wrote %s" % os.path.relpath(OUT_PATH, ROOT))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", action="store_true", help="score only, no network")
    args = ap.parse_args()
    if not args.score:
        log("Radar snapshot ...")
        snapshot()
    return score()


if __name__ == "__main__":
    sys.exit(main())
