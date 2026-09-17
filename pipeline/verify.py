#!/usr/bin/env python3
"""
Observational verification feed for the convective outlook.

WHY THIS EXISTS
---------------
Until now every calibration decision in this project has been made by comparing our own
output against our own output -- typically "what did the day-1 run say" used as a stand-in
for truth. That can only ever measure whether the forecast is internally consistent across
lead times. It cannot tell us whether day 1 is right, so it cannot produce a skill score,
and a threshold tuned that way is tuned against our own bias.

This script pulls a genuinely independent, observational rainfall analysis and scores every
archived forecast against it.

SOURCE
------
NOAA CPC Global Unified Gauge-Based Analysis of Daily Precipitation, served by NOAA PSL over
OPeNDAP. Chosen after checking the alternatives:

  * GPATS / Vaisala GLD360 lightning  -- the ideal verifier, but both are commercial.
  * Himawari-8/9                      -- carries NO lightning mapper (that is GOES, over the
                                         Americas). Its L2 cloud products are ~750 MB per
                                         10-minute slot, far too heavy for a CI job.
  * Blitzortung                       -- raw archive access is granted to contributing station
                                         operators only.
  * BoM Severe Storms Archive         -- free and authoritative for severe reports, but its CSV
                                         export needs a state-scoped query and coverage thins
                                         out badly in recent years. Worth adding later as a
                                         severe-specific verifier; not a base layer.
  * ERA5 / any reanalysis             -- still a model. Better than a forecast, but it would
                                         repeat the same mistake in a quieter voice.

CPC is rain-gauge based, so it is a real measurement rather than a model field. It is free,
needs no account, subsets to ~55 KB for the Australian domain via OPeNDAP, carries about two
days of latency, and runs back to 1979 -- which means the existing run archive can be scored
retrospectively instead of waiting weeks for data to accumulate.

WHAT IT CAN AND CANNOT VERIFY
-----------------------------
CAN:    whether convective rainfall occurred in a grid cell on a given day. That is exactly
        the quantity the TSTM category and the rain trigger are asserting, so it directly
        scores the 2mm gate and the lead-scaled trigger that replaces it.
CANNOT: hail size, wind gusts or tornado occurrence. A rain gauge cannot tell MRGL from MDT.
        Severe-intensity verification needs lightning or storm reports -- see the BoM note
        above. Every score this script emits is therefore a STORM-OCCURRENCE score, and is
        labelled as such. Do not read it as a severe-weather skill score.

CAVEAT ON RESOLUTION: CPC is 0.5 deg, our grid is 0.72 deg. Each of our points takes the
nearest CPC cell. Gauge density over inland Australia is genuinely sparse, so an isolated
storm can fall between gauges and read as a miss. That biases the false-alarm rate UP, i.e.
this verifier is harsh on us rather than flattering, which is the safer direction to err.

USAGE
    python3 pipeline/verify.py                  # score every archived date that is now observable
    python3 pipeline/verify.py --date 2026-09-10
    python3 pipeline/verify.py --backfill       # re-score the whole archive from scratch
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE    = os.path.join(ROOT, "docs", "archive")
OBS_DIR    = os.path.join(ARCHIVE, "obs")
SKILL_PATH = os.path.join(ARCHIVE, "skill.json")

OPENDAP = ("https://psl.noaa.gov/thredds/dodsC/Datasets/cpc_global_precip/"
           "precip.%d.nc.ascii?precip[%d:1:%d][%d:1:%d][%d:1:%d]")

# CPC grid geometry: lat runs 89.75 -> -89.75 and lon 0.25 -> 359.75, both at 0.5 deg,
# with the value stamped at the cell CENTRE.
CPC_LAT0, CPC_LON0, CPC_STEP = 89.75, 0.25, 0.5
# Australian window, a little wider than the outlook grid so every point has a neighbour.
I0, I1 = 198, 270     # lat  -9.25 .. -45.25
J0, J1 = 222, 311     # lon 111.25 .. 155.75

# A day counts as a storm day for scoring if the gauge analysis recorded at least this much.
# 1mm is "measurable rain fell"; 10mm is closer to "something convective happened". Both are
# scored, because the first is what the TSTM category claims and the second is what a user
# would recognise as a storm.
WET_MM, CONV_MM = 1.0, 10.0

# CPC lands roughly two days behind real time; leave a margin so we never score a partial day.
LATENCY_DAYS = 3


def log(msg):
    print(msg, flush=True)


def fetch_cpc(date):
    """Return {(lat,lon): mm} for the Australian window on `date`, or None if unavailable."""
    t = (date - dt.date(date.year, 1, 1)).days
    url = OPENDAP % (date.year, t, t, I0, I1, J0, J1)
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:
        log("  CPC fetch failed for %s: %s" % (date, e))
        return None
    if "precip.precip" not in body:
        log("  CPC returned no data array for %s (likely beyond the archive's end)" % date)
        return None

    # The .ascii response is: a DDS header, then the data array one row per (time,lat),
    # then the coordinate MAPS (time, lat, lon). Parse the array and the two axes separately
    # rather than trusting a flat number sweep, which would silently absorb the axes.
    arr_txt = body.split("precip.precip", 1)[1]
    lat_txt = arr_txt.split("precip.lat", 1)[1] if "precip.lat" in arr_txt else ""
    lon_txt = arr_txt.split("precip.lon", 1)[1] if "precip.lon" in arr_txt else ""
    arr_only = arr_txt.split("precip.time", 1)[0]

    rows = []
    for line in arr_only.splitlines():
        if "," not in line:
            continue
        # each data line is "[t][lat], v, v, v, ..."
        vals = line.split(",")[1:]
        row = []
        for v in vals:
            v = v.strip()
            if not v:
                continue
            try:
                row.append(float(v))
            except ValueError:
                row = []
                break
        if row:
            rows.append(row)
    if not rows:
        log("  CPC parse produced no rows for %s" % date)
        return None

    lats = [float(x) for x in re.findall(r"-?\d+\.\d+", lat_txt.split("precip.lon")[0])] if lat_txt else []
    lons = [float(x) for x in re.findall(r"-?\d+\.\d+", lon_txt)] if lon_txt else []
    if len(lats) != len(rows) or not lons or len(lons) != len(rows[0]):
        # Fall back to deriving the axes from the requested index window. The geometry is
        # fixed and documented above, so this is safe, but prefer the served axes when sane.
        lats = [CPC_LAT0 - CPC_STEP * i for i in range(I0, I1 + 1)]
        lons = [CPC_LON0 + CPC_STEP * j for j in range(J0, J1 + 1)]
        if len(lats) != len(rows) or len(lons) != len(rows[0]):
            log("  CPC axis/row mismatch for %s (%d rows x %d cols)" % (date, len(rows), len(rows[0])))
            return None

    out = {}
    for la, row in zip(lats, rows):
        for lo, v in zip(lons, row):
            if v is None or v < -1e30 or v > 2000:
                continue          # missing value (ocean, or no gauge)
            out[(round(la, 2), round(lo, 2))] = v
    return out


def nearest(obs, lat, lon):
    """Nearest CPC cell centre to one of our grid points, or None if that cell has no gauge."""
    la = round(round((CPC_LAT0 - lat) / CPC_STEP) * CPC_STEP * -1 + CPC_LAT0, 2)
    lo = round(round((lon - CPC_LON0) / CPC_STEP) * CPC_STEP + CPC_LON0, 2)
    return obs.get((la, lo))


def load_runs():
    runs = {}
    for fn in sorted(os.listdir(ARCHIVE)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", fn):
            continue
        try:
            with open(os.path.join(ARCHIVE, fn)) as f:
                runs[fn[:-5]] = json.load(f)
        except Exception as e:
            log("  skipping unreadable archive %s: %s" % (fn, e))
    return runs


def parse_day_label(label, run_date):
    """'Thu 24 Sep' -> a date, resolved against the run date so December/January works."""
    m = re.match(r"[A-Za-z]{3}\s+(\d{1,2})\s+([A-Za-z]{3})", label.strip())
    if not m:
        return None
    day, mon = int(m.group(1)), m.group(2).lower()
    months = ["jan", "feb", "mar", "apr", "may", "jun",
              "jul", "aug", "sep", "oct", "nov", "dec"]
    if mon not in months:
        return None
    mi = months.index(mon) + 1
    for yr in (run_date.year, run_date.year + 1):
        try:
            d = dt.date(yr, mi, day)
        except ValueError:
            continue
        if 0 <= (d - run_date).days <= 370:
            return d
    return None


def score(runs, obs_by_date):
    """Contingency tables per lead, plus the raw counts needed to recompute anything later."""
    per_lead = {}
    for run_key, run in sorted(runs.items()):
        try:
            run_date = dt.date(*[int(x) for x in run_key.split("-")])
        except ValueError:
            continue
        days = run.get("days") or []
        for lead, label in enumerate(days):
            valid = parse_day_label(label, run_date)
            if valid is None or valid not in obs_by_date:
                continue
            obs = obs_by_date[valid]
            b = per_lead.setdefault(lead, {
                "lead": lead, "runs": 0, "points": 0, "no_gauge": 0,
                "wet": {"hit": 0, "miss": 0, "fa": 0, "cn": 0},
                "conv": {"hit": 0, "miss": 0, "fa": 0, "cn": 0},
                "sev_fc": 0, "sev_obs_conv": 0,
            })
            b["runs"] += 1
            for p in run.get("points", []):
                d = p.get("d", [])
                if lead >= len(d):
                    continue
                rec = d[lead] or {}
                cat = rec.get("cat") or 0
                mm = nearest(obs, p["lat"], p["lon"])
                b["points"] += 1
                if mm is None:
                    b["no_gauge"] += 1
                    continue
                for key, thr in (("wet", WET_MM), ("conv", CONV_MM)):
                    o = mm >= thr
                    f = cat >= 1
                    t = b[key]
                    t["hit" if (f and o) else "fa" if f else "miss" if o else "cn"] += 1
                if cat >= 2:
                    b["sev_fc"] += 1
                    if mm >= CONV_MM:
                        b["sev_obs_conv"] += 1
    for b in per_lead.values():
        for key in ("wet", "conv"):
            t = b[key]
            h, m, f = t["hit"], t["miss"], t["fa"]
            t["pod"] = round(h / (h + m), 3) if (h + m) else None          # probability of detection
            t["far"] = round(f / (h + f), 3) if (h + f) else None          # false alarm ratio
            t["csi"] = round(h / (h + m + f), 3) if (h + m + f) else None  # critical success index
            t["bias"] = round((h + f) / (h + m), 3) if (h + m) else None   # frequency bias
        b["sev_conv_rate"] = (round(b["sev_obs_conv"] / b["sev_fc"], 3)
                              if b["sev_fc"] else None)
    return [per_lead[k] for k in sorted(per_lead)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="verify one date only (YYYY-MM-DD)")
    ap.add_argument("--backfill", action="store_true",
                    help="refetch every observable date, ignoring what is already cached")
    args = ap.parse_args()

    os.makedirs(OBS_DIR, exist_ok=True)
    runs = load_runs()
    if not runs:
        log("No archived runs found -- nothing to verify.")
        return 0
    log("Archived runs: %d (%s .. %s)" % (len(runs), min(runs), max(runs)))

    # Every valid date any archived run made a claim about.
    wanted = set()
    for run_key, run in runs.items():
        try:
            rd = dt.date(*[int(x) for x in run_key.split("-")])
        except ValueError:
            continue
        for label in (run.get("days") or []):
            v = parse_day_label(label, rd)
            if v:
                wanted.add(v)

    cutoff = dt.date.today() - dt.timedelta(days=LATENCY_DAYS)
    if args.date:
        wanted = {dt.date(*[int(x) for x in args.date.split("-")])}
    else:
        wanted = {d for d in wanted if d <= cutoff}

    obs_by_date = {}
    fetched = 0
    for d in sorted(wanted):
        cache = os.path.join(OBS_DIR, "%s.json" % d.isoformat())
        if os.path.exists(cache) and not args.backfill:
            with open(cache) as f:
                raw = json.load(f)
            obs_by_date[d] = {(float(k.split(",")[0]), float(k.split(",")[1])): v
                              for k, v in raw["cells"].items()}
            continue
        log("Fetching CPC gauge analysis for %s ..." % d)
        obs = fetch_cpc(d)
        if obs is None:
            continue
        wet = sum(1 for v in obs.values() if v >= WET_MM)
        log("  %d land cells, %d wet (>=%.0fmm), max %.1f mm"
            % (len(obs), wet, WET_MM, max(obs.values()) if obs else 0))
        with open(cache, "w") as f:
            json.dump({"date": d.isoformat(), "source": "NOAA CPC gauge analysis (0.5 deg)",
                       "cells": {"%s,%s" % k: round(v, 2) for k, v in obs.items()}}, f)
        obs_by_date[d] = obs
        fetched += 1

    if not obs_by_date:
        log("No observations available yet -- nothing scored.")
        return 0

    rows = score(runs, obs_by_date)
    out = {
        "generated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "NOAA CPC Global Unified Gauge-Based Analysis of Daily Precipitation (0.5 deg)",
        "verifies": "storm-day occurrence only (rainfall). NOT severe intensity -- a gauge "
                    "cannot distinguish MRGL from MDT.",
        "thresholds_mm": {"wet": WET_MM, "convective": CONV_MM},
        "dates_verified": sorted(d.isoformat() for d in obs_by_date),
        "by_lead": rows,
    }
    with open(SKILL_PATH, "w") as f:
        json.dump(out, f, indent=1)

    log("")
    log("Storm-day skill vs CPC gauge analysis -- %d dates, %d newly fetched"
        % (len(obs_by_date), fetched))
    log("Scored on the >=%.0fmm threshold: did measurable rain fall where we drew a storm." % WET_MM)
    log("bias 1.0 = we drew exactly as many storm-days as occurred. Above 1.0 is over-forecasting.")
    log("")
    log("%-5s %8s %7s %7s %7s %7s    %s" %
        ("day", "points", "POD", "FAR", "CSI", "bias", "obs base rate"))
    for r in rows:
        w = r["wet"]
        n = w["hit"] + w["miss"] + w["fa"] + w["cn"]
        base = (w["hit"] + w["miss"]) / n * 100 if n else 0
        log("%-5d %8d %7s %7s %7s %7s %12.2f%%" %
            (r["lead"] + 1, r["points"], w["pod"], w["far"], w["csi"], w["bias"], base))

    # The 10mm table is kept in skill.json but not printed: through a dry September its base
    # rate sits near 0.02%, which makes every derived score meaningless. It becomes readable
    # once a wet season supplies enough events, and is the closer proxy for "a storm" when it does.
    conv_events = sum(r["conv"]["hit"] + r["conv"]["miss"] for r in rows)
    log("")
    log("(>=%.0fmm convective threshold: only %d observed events so far -- too few to score. "
        "It will become usable over the wet season.)" % (CONV_MM, conv_events))
    log("")
    log("NOT VERIFIED HERE: hail size, wind gusts, tornadoes. A rain gauge cannot separate")
    log("MRGL from MDT, so nothing above is a severe-weather skill score.")
    log("Wrote %s" % os.path.relpath(SKILL_PATH, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
