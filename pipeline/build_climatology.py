#!/usr/bin/env python3
"""Build data/climatology.json: the 1991-2020 average daily max and min temperature for every
model grid point and calendar day, from the Open-Meteo ERA5 archive. Needed to measure heatwave
anomalies ("+12C above average"). Run once (a few hundred requests), then yearly if at all.

    OPENMETEO_KEY=... python3 pipeline/build_climatology.py

Resumable: progress is cached in data/.climatology_cache.json, so a rate-limit stop can simply be
re-run. Stdlib only. Values are stored in tenths of a degree, 366 entries per point (index by
climate_index()), smoothed with a +-7 day circular window."""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID = os.path.join(ROOT, "data", "grid.json")
OUT = os.path.join(ROOT, "data", "climatology.json")
CACHE = os.path.join(ROOT, "data", ".climatology_cache.json")
START, END, BATCH, SMOOTH = "1991-01-01", "2020-12-31", 10, 7
LEAP = 2000  # reference leap year: every month/day gets a stable 0..365 index


def climate_index(month, day):
    return (dt.date(LEAP, month, day) - dt.date(LEAP, 1, 1)).days


def point_key(lat, lon):
    return "%.2f,%.2f" % (lat, lon)


def day_means(dates, values):
    """Mean per calendar day (366 slots) ignoring missing values, then a circular +-SMOOTH smooth."""
    sums, n = [0.0] * 366, [0] * 366
    for d, v in zip(dates, values):
        if v is None:
            continue
        i = climate_index(int(d[5:7]), int(d[8:10]))
        sums[i] += v
        n[i] += 1
    raw = [sums[i] / n[i] if n[i] else None for i in range(366)]
    out = []
    for i in range(366):
        win = [raw[(i + k) % 366] for k in range(-SMOOTH, SMOOTH + 1)]
        win = [w for w in win if w is not None]
        out.append(round(sum(win) / len(win) * 10) if win else None)
    return out


def fetch(points, key):
    host = "customer-archive-api.open-meteo.com" if key else "archive-api.open-meteo.com"
    url = ("https://%s/v1/archive?latitude=%s&longitude=%s&start_date=%s&end_date=%s"
           "&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
           % (host, ",".join(str(p[0]) for p in points), ",".join(str(p[1]) for p in points), START, END))
    if key:
        url += "&apikey=" + key
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                d = json.loads(r.read())
            return d if isinstance(d, list) else [d]
        except urllib.error.HTTPError as e:
            body = e.read()[:200]
            if e.code == 429 and b"Daily" in body:
                raise SystemExit("Daily API limit reached; re-run later (progress is saved).")
            print("  HTTP %s, retry %d: %s" % (e.code, attempt + 1, body), file=sys.stderr)
        except Exception as e:
            print("  error, retry %d: %s" % (attempt + 1, e), file=sys.stderr)
        time.sleep(5 * (attempt + 1))
    raise SystemExit("giving up on a batch; re-run to resume")


def main():
    key = os.environ.get("OPENMETEO_KEY", "")
    grid = json.load(open(GRID))
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [p for p in grid if point_key(*p) not in cache]
    print("%d points, %d to fetch (%s endpoint)" % (len(grid), len(todo), "keyed" if key else "free"))
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        res = fetch(batch, key)
        for p, r in zip(batch, res):
            t = r["daily"]["time"]
            cache[point_key(*p)] = {"tmax": day_means(t, r["daily"]["temperature_2m_max"]),
                                    "tmin": day_means(t, r["daily"]["temperature_2m_min"])}
        json.dump(cache, open(CACHE, "w"))
        print("  %d/%d" % (min(i + BATCH, len(todo)), len(todo)))
        time.sleep(1)
    json.dump({"period": [START, END], "units": "tenths of C", "smooth_days": SMOOTH, "points": cache},
              open(OUT, "w"), separators=(",", ":"))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
