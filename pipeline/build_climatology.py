#!/usr/bin/env python3
"""Build data/climatology.json: the 1991-2020 average daily max and min temperature for every
model grid point and calendar day. Needed to measure heatwave anomalies ("+12C above average").
Run once, then yearly if at all.

    python3 pipeline/build_climatology.py                  # SILO (default)
    python3 pipeline/build_climatology.py --source open-meteo

Two sources:

  silo (DEFAULT) -- SILO DataDrill, Queensland Government. Gridded Australian daily climate
      interpolated from Bureau station records, 1889 to present, published under CC-BY 4.0, so it
      is free and fine for a commercial product with attribution. One request per grid point,
      about 2.6 s each, so roughly an hour for the 1,342-point grid. This is NOT a Bureau licence
      and none is claimed: it is Queensland's open re-publication.

  open-meteo -- the ERA5 archive. Keyed, it needs the API Professional or Enterprise plan: on the
      Standard plan customer-archive-api returns
        403 {"reason":"This API endpoint requires the API Professional or Enterprise plan"}
      which is what failed the 19 Oct 2026 run. Unkeyed it falls back to the free host, which
      works but is non-commercial-use-only, so it is not an option for this product. Kept for the
      day the plan changes.

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

# SILO DataDrill. username/password are SILO's documented API convention, not credentials and not
# anyone's address — deliberately generic so no personal email is sent to a third party.
SILO_URL = "https://www.longpaddock.qld.gov.au/cgi-bin/silo/DataDrillDataset.php"
SILO_USER = "apirequest"
SILO_ATTRIBUTION = ("SILO, Queensland Department of Environment, Science and Innovation, "
                    "CC-BY 4.0 (https://www.longpaddock.qld.gov.au/silo/)")


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


def fetch_silo(lat, lon):
    """One grid point's daily max/min from SILO DataDrill. Returns (dates, tmax, tmin).

    comment=XN asks for max_temp and min_temp only. Rows whose value is missing come back blank
    and are passed through as None, which day_means() already skips.
    """
    url = ("%s?format=csv&username=%s&password=%s&lat=%s&lon=%s&start=%s&finish=%s&comment=XN"
           % (SILO_URL, SILO_USER, SILO_USER, lat, lon,
              START.replace("-", ""), END.replace("-", "")))
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                text = r.read().decode("utf-8", "replace")
            dates, tmax, tmin = [], [], []
            for line in text.splitlines():
                parts = line.split(",")
                # a data row starts with a numeric latitude; the header and any prose do not
                if len(parts) < 7:
                    continue
                try:
                    float(parts[0])
                except ValueError:
                    continue
                dates.append(parts[2].strip())
                tmax.append(_num(parts[3]))
                tmin.append(_num(parts[5]))
            if len(dates) < 3650:  # under 10 years back is not a usable 30-year normal
                raise ValueError("only %d rows returned" % len(dates))
            return dates, tmax, tmin
        except Exception as e:
            print("  SILO retry %d for %s,%s: %s" % (attempt + 1, lat, lon, e), file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("SILO failed for %s,%s after 5 tries; re-run to resume" % (lat, lon))


def _num(s):
    s = (s or "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def main():
    source = "silo"
    if "--source" in sys.argv:
        source = sys.argv[sys.argv.index("--source") + 1]
    if source not in ("silo", "open-meteo"):
        raise SystemExit("--source must be silo or open-meteo")

    key = os.environ.get("OPENMETEO_KEY", "")
    grid = json.load(open(GRID))
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [p for p in grid if point_key(*p) not in cache]
    print("%d points, %d to fetch (source: %s)" % (len(grid), len(todo), source))

    if source == "silo":
        for n, p in enumerate(todo, start=1):
            dates, tmax, tmin = fetch_silo(p[0], p[1])
            cache[point_key(*p)] = {"tmax": day_means(dates, tmax),
                                    "tmin": day_means(dates, tmin)}
            if n % 10 == 0 or n == len(todo):
                json.dump(cache, open(CACHE, "w"))
                print("  %d/%d" % (n, len(todo)), flush=True)
    else:
        if not key:
            raise SystemExit("open-meteo without a key uses the free non-commercial host; refusing. "
                             "Use --source silo, or set OPENMETEO_KEY on a Professional plan.")
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            res = fetch(batch, key)
            for p, r in zip(batch, res):
                t = r["daily"]["time"]
                cache[point_key(*p)] = {"tmax": day_means(t, r["daily"]["temperature_2m_max"]),
                                        "tmin": day_means(t, r["daily"]["temperature_2m_min"])}
            json.dump(cache, open(CACHE, "w"))
            print("  %d/%d" % (min(i + BATCH, len(todo)), len(todo)), flush=True)
            time.sleep(1)

    out = {"period": [START, END], "units": "tenths of C", "smooth_days": SMOOTH,
           "source": source, "points": cache}
    if source == "silo":
        out["attribution"] = SILO_ATTRIBUTION
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print("wrote %s (%d points, source %s)" % (OUT, len(cache), source))


if __name__ == "__main__":
    main()
