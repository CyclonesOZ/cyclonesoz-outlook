"""Heatwave and snow checks for the alert scan. Adds two per-point, per-day fields to a raw run:

    heat   0-4   heatwave tier (0 none, 1 Minor, 2 Moderate, 3 Significant, 4 Major)
    snow72 cm    snowfall summed over the 72 hours ending that day

so the ordinary rules in alerts/thresholds.json can use them. Heatwave: daily max above the
1991-2020 average for that calendar day by the tier's threshold, with the overnight minimum above
its average too, on at least two consecutive days. Needs data/climatology.json
(pipeline/build_climatology.py). Any failure leaves the run untouched; the scan never depends on this.
Stdlib only."""
import datetime as dt
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIMATE = os.path.join(ROOT, "data", "climatology.json")
BATCH = 50


def climate_index(month, day):
    return (dt.date(2000, month, day) - dt.date(2000, 1, 1)).days


def day_level(tmax_anom, tmin_anom, thresholds):
    """Highest heat tier whose max-anomaly threshold is met, if the night was also above average."""
    if tmax_anom is None or tmin_anom is None or tmin_anom <= 0:
        return 0
    lvl = 0
    for i, t in enumerate(thresholds, start=1):
        if tmax_anom >= t:
            lvl = i
    return lvl


def heat_tiers(levels, min_days=2):
    """Per-day tier given per-day levels: a day's tier is the best (over runs of >= min_days that
    include it) of the lowest level in the run. Only min_days == 2 is needed, kept general."""
    n, out = len(levels), [0] * len(levels)
    for i in range(n - min_days + 1):
        run = levels[i:i + min_days]
        t = min(run)
        for j in range(i, i + min_days):
            out[j] = max(out[j], t)
    return out


def rolling_sum(values, window_days=3):
    out = []
    for i in range(len(values)):
        w = [v for v in values[max(0, i - window_days + 1):i + 1] if v is not None]
        out.append(round(sum(w), 1))
    return out


def fetch_daily(points, key=""):
    """{point_key: {date: (tmax, tmin, snow_cm)}} from the Open-Meteo forecast, in local dates."""
    host = "customer-api.open-meteo.com" if key else "api.open-meteo.com"
    res = {}
    for i in range(0, len(points), BATCH):
        b = points[i:i + BATCH]
        url = ("https://%s/v1/forecast?latitude=%s&longitude=%s&daily=temperature_2m_max,"
               "temperature_2m_min,snowfall_sum&forecast_days=8&timezone=auto"
               % (host, ",".join(str(p["lat"]) for p in b), ",".join(str(p["lon"]) for p in b)))
        if key:
            url += "&apikey=" + key
        with urllib.request.urlopen(url, timeout=120) as r:
            d = json.loads(r.read())
        for p, x in zip(b, d if isinstance(d, list) else [d]):
            dd = x["daily"]
            res["%.2f,%.2f" % (p["lat"], p["lon"])] = {
                t: (a, b_, s) for t, a, b_, s in zip(dd["time"], dd["temperature_2m_max"],
                                                     dd["temperature_2m_min"], dd["snowfall_sum"])}
    return res


def frost_nights(mins, consecutive=2):
    """Per-day count of consecutive nights at or below 0 C ending that day.

    Frost damage is cumulative: one cold night after a warm week does far less than two or three
    in a row, which is why the rulebook asks for 2+ nights at every tier. The count lets a rule
    say "this is the Nth night in a row", and `consecutive` is the minimum before any day reports.
    A missing night breaks the run rather than being treated as mild.
    """
    out, streak = [], 0
    for v in mins:
        streak = streak + 1 if (v is not None and v <= 0) else 0
        out.append(streak if streak >= consecutive else 0)
    return out


def augment_run(run, th, daily=None, climate=None, key=""):
    """Add 'heat', 'snow72', 'tmin' and 'frost_nights' to every point-day.

    Returns a one-line status for the email. 'tmin' and 'frost_nights' need only the Open-Meteo
    forecast, not the climatology, so frost works even while heatwave is waiting on
    data/climatology.json. Heat is an anomaly against each point's own 1991-2020 average, so it is
    already region-relative by construction; frost is absolute and is gated per region in
    thresholds.json instead.
    """
    cfg = th.get("extra_hazards", {}).get("heatwave", {})
    thr = [cfg["tmax_anomaly_c"][k] for k in ("minor", "moderate", "significant", "major")] if cfg else None
    if climate is None:
        climate = json.load(open(CLIMATE))["points"] if os.path.exists(CLIMATE) else {}
    if daily is None:
        daily = fetch_daily(run["points"], key)
    dates = run["dates"]
    for p in run["points"]:
        k = "%.2f,%.2f" % (p["lat"], p["lon"])
        obs, cl = daily.get(k, {}), climate.get(k)
        levels, snow, mins = [], [], []
        for di, date in enumerate(dates):
            tmax, tmin, sn = obs.get(date, (None, None, None))
            snow.append(sn)
            mins.append(tmin)
            lvl = 0
            if cl and thr and tmax is not None and tmin is not None:
                i = climate_index(int(date[5:7]), int(date[8:10]))
                am, an = cl["tmax"][i], cl["tmin"][i]
                if am is not None and an is not None:
                    lvl = day_level(tmax - am / 10, tmin - an / 10, thr)
            levels.append(lvl)
        tiers, s72, frosts = heat_tiers(levels), rolling_sum(snow), frost_nights(mins)
        for di, rec in enumerate(p["d"][:len(dates)]):
            rec["heat"], rec["snow72"] = tiers[di], s72[di]
            rec["frost_nights"] = frosts[di]
            if mins[di] is not None:
                rec["tmin"] = mins[di]
    return ("heat+snow+frost checked"
            + ("" if climate else " (no climatology yet: heat skipped, frost unaffected)"))
