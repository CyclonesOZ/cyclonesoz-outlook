"""Fine hazard areas and hand-drawn-style outlines for the morning severe email.

Josh, 20 Nov 2026, on the morning map: "can we make the shapes and polygon drafts a lot better? maybe
increase the pixel volume by 2 or 3 orders of magnitude", with a drawing of the five Channel Country
boxes as one smooth outline round them, and:
  - "Damaging winds often are overstated by the model by 20-40%"
  - heavy rain: "a risk develops when the models have substantial rainfall relative to the seasonal
    average, and when the atmosphere is conducive ... like when PWAT gets above those values" (his
    70/60/50/40 mm lines; data/fine_climatology.npz turns them into a map for every month)
  - hail: he caps the published outlook at small hail, but large and very large count when they emerge
  - "those tiny little one or two pixel zones either need to be automatically merged into a
    neighbouring bigger severe weather zone or hidden from the map unless situated within a major
    metro centre"

How it fits: the outlook (0.72 deg) still decides WHETHER there is a storm area; that is Josh's own
tuned product. Our ECMWF grids (fine_grid.py, 0.25 or 0.1 deg) decide WHERE inside it, and set the
refined wind, hail and rain numbers. Outlines are drawn on a 0.025 deg raster (~830x the outlook's
cells): the hazard cells are closed up (gaps under ~1.8 deg filled), given a margin, and smoothed, so
an area comes out as one rounded shape round its cells, the way a forecaster draws it. The model's
real detail stops at its own grid; the fine raster is for clean edges, not invented detail.
"""
import math
import os

import numpy as np
from scipy import ndimage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIM = os.path.join(ROOT, "data", "fine_climatology.npz")

DRAW_RES = 0.025          # outline raster, degrees
# Outlines the way the public storm outlook draws its bands (docs/index.html: a smoothed indicator
# field, contoured, then Chaikin corner-cutting), so they come out round, not as boxes with rounded
# corners. sigma is the blur in degrees, level the contour; Josh's Channel Country drawing set the
# margin (~0.3-0.5 deg round the boxes) and how far apart two patches can be and still be one shape.
OUTER = {"sigma": 0.5, "level": 0.14}
INNER = {"sigma": 0.25, "level": 0.3}
# Then each ring is cut to a few control points and corner-cut smooth, and finished at about the
# point count of Josh's own hand-drawn outlines (Post Alert thins those to ~60), so "Move corners"
# works on it the same way. Josh, 20 Nov: "nice and smooth, like the polygons I've been producing".
CHAIKIN = 3
RING_POINTS = (24, 64)    # min, max points per finished ring
POINT_EVERY = 0.2         # degrees of perimeter per point
MIN_BLOB_KM2 = 2500       # hazard area a separate blob needs to be drawn (about three 25 km cells)
TINY_ITEM_KM2 = 5000      # an item this small is merged into a bigger one near it, or hidden
TINY_MERGE_DEG = 2.0      # ...if a bigger one of the same kind is within ~200 km
FOOTPRINT = 0.8           # fine cells count for an item within this of its outlook points, degrees
LEAD_SEV_K = [1.0, 0.875, 1.0]   # build_outlook.R LEAD_SEV_K, days 1-3

# Major metro centres: a tiny area is still shown over one of these (Josh, 20 Nov). Radius km.
METROS = [("Sydney", -33.87, 151.21, 50), ("Melbourne", -37.81, 144.96, 50), ("Brisbane", -27.47, 153.03, 45),
          ("Perth", -31.95, 115.86, 45), ("Adelaide", -34.93, 138.60, 40), ("Gold Coast", -28.02, 153.40, 30),
          ("Canberra", -35.28, 149.13, 25), ("Newcastle", -32.93, 151.78, 25), ("Sunshine Coast", -26.65, 153.07, 30),
          ("Wollongong", -34.42, 150.89, 20), ("Hobart", -42.88, 147.33, 25), ("Geelong", -38.15, 144.36, 20),
          ("Townsville", -19.26, 146.82, 20), ("Cairns", -16.92, 145.77, 20), ("Darwin", -12.46, 130.84, 25),
          ("Toowoomba", -27.56, 151.95, 15)]


def near_metro(lat, lon):
    for name, la, lo, r in METROS:
        if math.hypot(lat - la, (lon - lo) * math.cos(math.radians(la))) * 111.2 <= r:
            return name
    return None


# --------------------------------------------------------------------------- climate

class Climate:
    def __init__(self, path=CLIM):
        z = np.load(path)
        self.lats, self.lons = z["lats"], z["lons"]
        self.rain = z["rain_mean"].astype(np.float32) / 10
        self.rain[z["rain_mean"] == 65535] = np.nan
        self.pw = z["pw_thr"].astype(np.float32) / 10

    def on(self, lats, lons, month):
        i = np.clip(np.rint((self.lats[0] - lats) / (self.lats[0] - self.lats[1])).astype(int), 0, len(self.lats) - 1)
        j = np.clip(np.rint((lons - self.lons[0]) / (self.lons[1] - self.lons[0])).astype(int), 0, len(self.lons) - 1)
        return self.rain[month - 1][np.ix_(i, j)], self.pw[month - 1][np.ix_(i, j)]


# --------------------------------------------------------------------------- hazard rules

def _rain_cat(mm):
    return np.select([mm >= 150, mm >= 75, mm >= 35, mm >= 10], [4, 3, 2, 1], 0)


def categorise(d, sev_k=1.0, strict=None, trig=2.0):
    """build_outlook.R categorise_vals(), cell by cell. 0 none, 1 TSTM, 2 MRGL, 3 MDT, 4 HIGH.
    Inputs differ a little: ECMWF MUCAPE and 0-6 km bulk shear stand in for thundeR's MU CAPE and
    effective shear, and severe.py's SCP/STP/SHIP for thundeR's. Agreement with the outlook is
    measured in test_fine_areas.py, not assumed."""
    cape, shr = d["mucape"], d["shear6"] * 1.94384
    scp, stp, ship = d["scp"] * sev_k, d["stp"] * sev_k, d["ship"] * sev_k
    capeS, rain = cape * sev_k, d["rain"]
    floor = np.clip(200 + 300 * (d["t500"] + 20) / 12, 200, 500)
    c = (cape >= floor).astype(np.int8)
    sev_env = ((capeS >= 1000) & (shr >= 25)) | ((capeS >= 800) & (shr >= 30)) | (scp > 3)
    c = np.where(sev_env, np.maximum(c, 2), c)
    mdt = (scp >= 6) | (stp >= 2) | ((capeS >= 1500) & ((ship >= 2) | (scp >= 6))) | ((capeS >= 3000) & sev_env)
    c = np.where(mdt, np.maximum(c, 3), c)
    high = ((scp >= 8) & ((ship >= 1.5) | (stp >= 2))) | (stp >= 5) | ((scp >= 6) & (ship >= 3))
    c = np.where(high, 4, c)
    capped, no_trig, rc = d["mucin"] <= -75, rain < trig, _rain_cat(rain)
    sig = (stp >= 2) | (ship >= 1.5) | (rc >= 3)
    pre = np.where(capped & (c >= 3), c - 1, c)
    pre = np.where(sig & (pre >= 1), np.maximum(pre, 3), pre)
    strict = np.zeros_like(c, bool) if strict is None else strict
    c = np.where(no_trig, 0, pre)
    cond = no_trig & (rain >= 0.1 * trig) & (pre >= 2) & ~strict
    c = np.where(cond, np.maximum(1, np.minimum(2, pre - 1)), c)
    c = np.maximum(c, rc)
    c = np.where(strict & (rain < 1.5 * trig), np.minimum(c, 1), c)
    return c.astype(np.int8), cond


def hail_tier(d, cat):
    """build_outlook.R hail_tier() on the peak-SHIP step, NOT capped by the category (Josh, 20 Nov:
    large and very large count when they emerge). Still needs storms (cat >= 1)."""
    ship, cape = d["ship_peak"], d["cape_at_ship"]
    t = np.select([ship >= 3, ship >= 1.5, (ship >= 0.5) & (cape >= 300)], [3, 2, 1], 0)
    t = np.where((d["frzl"] > 4900) & (t >= 1), t - 1, t)   # a very high freezing level melts hail out
    return np.where(cat >= 1, t, 0).astype(np.int8)


def wind_potential(d, cat):
    """Convective gust potential, km/h, from build_outlook.R wind_tier()'s own breakpoints made
    continuous: the downdraft route (DCAPE 700/1000/1300 -> 90/125/160 km/h, with its support
    conditions) and the derecho route (DCP 0.5/1.5/3 -> 90/125/160, DCAPE >= 500)."""
    dcape, dd700, lr03, dcp, cape = d["dcape"], d["dd700"], d["lr03"], d["dcp"], d["mucape"]
    shr = d["shear6"] * 1.94384
    support = (dd700 >= 8) | (lr03 >= 7) | (shr >= 25)
    wd = np.where(support & (dcape >= 700), 90 + (dcape - 700) * 35 / 300, 0)
    to_160 = ((dcape >= 1000) & (shr >= 30) & (cape >= 1000)) | ((dcape >= 1300) & (dd700 >= 12))
    wd = np.where(to_160, wd, np.minimum(wd, 124.9))
    wd = np.where((shr >= 40) & (cape >= 2000), wd, np.minimum(wd, 159.9))
    wp = np.where((dcp >= 0.5) & (dcape >= 500), np.interp(dcp, [0.5, 1.5, 3.0, 6.0], [90, 125, 160, 180]), 0)
    w = np.maximum(wd, wp)
    w = np.where(cat >= 1, w, 0)
    return np.where(cat >= 3, w, np.minimum(w, 124.9))   # 125+ still needs the moderate gate


def tier_kmh(v):
    return np.select([v >= 160, v >= 125, v >= 90], [3, 2, 1], 0).astype(np.int8)


def rain_tiers(rain24, rain72, pw, pw_thr, normal):
    """Heavy rain tier 0-4: rain well above the month's normal AND air moist enough to make it.
    Either the day (24 h) or the 3 days (72 h) can carry it; floors stop a desert's 10 mm counting."""
    n = np.where(np.isfinite(normal), np.maximum(normal, 5), np.inf)  # offshore beyond the coast: floors only
    p24, p72 = rain24 / n, rain72 / n
    t = np.zeros(rain24.shape, np.int8)
    for lvl, (f24, r24, f72, r72) in enumerate([(30, 0.5, 50, 1.0), (60, 1.0, 100, 1.5),
                                                (100, 1.5, 150, 2.0), (200, 2.5, 300, 3.0)], 1):
        hit = ((rain24 >= f24) & ((p24 >= r24) | ~np.isfinite(n))) | ((rain72 >= f72) & ((p72 >= r72) | ~np.isfinite(n)))
        t = np.where(hit, lvl, t)
    return np.where(pw >= pw_thr, t, 0).astype(np.int8)


class FineDays:
    """Hazard grids for the email's days from one model run."""

    def __init__(self, mrun, dates, cfg=None, climate=None, strict_of=None):
        import datetime as dt
        cfg = cfg or {}
        self.run, self.dates = mrun, [d for d in dates if mrun.covers(dt.date.fromisoformat(d))]
        self.lats, self.lons = mrun.lats, mrun.lons
        self.res = mrun.res
        self.wind_k = float(cfg.get("wind_overstatement", 1.3))
        self.gust_k = float(cfg.get("gust_overstatement", 1.3))
        clim = climate or Climate()
        self.days = []
        raw = [mrun.day(dt.date.fromisoformat(d)) for d in self.dates]
        strict = strict_of(self.lats, self.lons) if strict_of else None
        rain72 = sum(r["rain"] for r in raw)
        pw3 = np.max([r["pw"] for r in raw], axis=0) if raw else None
        for i, (date, r) in enumerate(zip(self.dates, raw)):
            month = int(date[5:7])
            normal, pw_thr = clim.on(self.lats, self.lons, month)
            cat, cond = categorise(r, LEAD_SEV_K[min(i, 2)], strict)
            hail = hail_tier(r, cat)
            w = wind_potential(r, cat) / self.wind_k
            wind = tier_kmh(w)
            gust = r["gust"] / self.gust_k
            # 3-day rain counts on the days it falls (10 mm+), and needs the moist air on one of them
            rt72 = rain_tiers(np.zeros_like(rain72), rain72, pw3, pw_thr, normal)
            rt = np.maximum(rain_tiers(r["rain"], np.zeros_like(rain72), r["pw"], pw_thr, normal),
                            np.where(r["rain"] >= 10, rt72, 0))
            # upgrade as the outlook does: Large hail or a damaging gust lifts a firm TSTM day to MRGL
            cat = np.where((cat == 1) & ~cond & ((hail >= 2) | (wind >= 1)), 2, cat)
            storms = (cat >= 3) | ((hail >= 2) & (cat >= 2)) | ((wind >= 1) & (cat >= 3))
            danger = storms & ((cat >= 4) | (hail >= 3) | ((wind >= 2) & (cat >= 3)) | ((r["stp"] >= 2) & (cat >= 3)))
            self.days.append({
                "date": date, "cat": cat, "hail": hail, "wind": wind, "wind_kmh": w, "gust": gust,
                "rain": r["rain"], "rain72": rain72, "pw": r["pw"], "pw3": pw3, "pw_thr": pw_thr, "normal": normal,
                "rain_tier": rt, "storms": storms, "storms_inner": danger, "stp": r["stp"],
                "cape": r["mucape"], "shear": r["shear6"] * 1.94384,
                "fam": {"storms": storms.astype(np.int8) + danger, "rain": rt,
                        "wind": tier_kmh(gust)},
            })

    def day_index(self, date):
        return self.dates.index(date) if date in self.dates else None

    def ij(self, lat, lon):
        return (int(np.clip(np.rint((self.lats[0] - lat) / self.res), 0, len(self.lats) - 1)),
                int(np.clip(np.rint((lon - self.lons[0]) / self.res), 0, len(self.lons) - 1)))

    def box(self, lat, lon, half):
        i0, j0 = self.ij(lat + half, lon - half)
        i1, j1 = self.ij(lat - half, lon + half)
        return slice(i0, i1 + 1), slice(j0, j1 + 1)


# --------------------------------------------------------------------------- the outlook, refined

def patch_run(run, fine, step):
    """The raw outlook with its wind, hail, gust and rain numbers replaced by the fine ones over each
    point's own 0.72 deg box, so the scan's rules and the email's words use the refined values.
    Returns a copy; the run itself is untouched (the forecaster scan keeps reading it)."""
    import copy
    out = copy.deepcopy(run)
    half = step / 2
    for p in out["points"]:
        rs, cs = fine.box(p["lat"], p["lon"], half)
        for di, date in enumerate(out["dates"]):
            k = fine.day_index(date)
            if k is None or di >= len(p["d"]):
                continue
            f, rec = fine.days[k], p["d"][di]
            sub = lambda a: a[rs, cs]
            share = lambda a, lvl: float(np.mean(sub(a) >= lvl))
            # The level a share of the box reaches, not its single worst cell: a 0.72 deg box holds ~50
            # cells at 0.1 deg, and the worst of 50 always runs hot.
            rec["wind"] = max([lvl for lvl in (1, 2, 3) if share(f["wind"], lvl) >= 0.15], default=0)
            rec["wind_kmh"] = int(round(float(np.percentile(sub(f["wind_kmh"]), 85))))
            rec["hail"] = max([lvl for lvl in (1, 2, 3) if share(f["hail"], lvl) >= 0.1], default=0)
            rec["gust"] = round(float(np.percentile(sub(f["gust"]), 90)), 1)
            # rain tier: the level a fifth of the box reaches, so one wet cell is not an area
            rec["rain_risk"] = max([lvl for lvl in range(1, 5) if share(f["rain_tier"], lvl) >= 0.2], default=0)
            rec["rain"] = round(float(np.percentile(sub(f["rain"]), 90)), 1)
            rec["pw"] = round(float(sub(f["pw"]).max()), 1)
            rec["pw_thr"] = round(float(np.median(sub(f["pw_thr"]))), 1)
            nrm = sub(f["normal"])
            rec["normal"] = round(float(np.nanmedian(nrm)), 1) if np.isfinite(nrm).any() else None
    out["fine"] = fine.run.label
    return out


def morning_rules(th):
    """thresholds.json for the morning email when the fine grids are in: heavy rain by the new rule
    (rain_risk), the old fixed-bar rain rules out. Wind, hail and gust rules stay; patch_run has
    already refined the numbers they read."""
    th = dict(th)
    rules = [r for r in th["rules"] if not r["id"].startswith(("rain_", "rain3_"))]
    labels = ["Heavy rain (above the month's normal, moist air)", "Very heavy rain (double the month's normal)",
              "Extreme rain (well above normal)", "Exceptional rain"]
    for lvl, lab in enumerate(labels, 1):
        rules.append({"id": "rainf_%d" % lvl, "label": lab, "level": lvl, "days": [1, 3],
                      "min_points": 2 if lvl <= 2 else 1, "when": [{"field": "rain_risk", "op": ">=", "value": lvl}]})
    th["rules"] = rules
    return th


# --------------------------------------------------------------------------- outlines

def _douglas_peucker(pts, tol):
    if len(pts) < 4:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        seg = q - p
        L = np.hypot(*seg)
        mid = pts[a + 1:b]
        d = mid - p
        dist = np.abs(seg[0] * d[:, 1] - seg[1] * d[:, 0]) / L if L > 0 else np.hypot(d[:, 0], d[:, 1])
        k = int(np.argmax(dist))
        if dist[k] > tol:
            keep[a + 1 + k] = True
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return pts[keep]


def smooth_ring(r):
    """A traced contour -> a smooth ring of evenly spaced points: Douglas-Peucker down to its main
    turns (tolerance grows with the shape's size), Chaikin corner-cutting, then resampled."""
    r = np.asarray(r, float)
    if len(r) > 1 and np.allclose(r[0], r[-1]):
        r = r[:-1]
    if len(r) < 4:
        return None
    size = math.sqrt(abs(_ring_area(np.vstack([r, r[:1]]))))
    tol = min(0.25, max(0.05, 0.06 * size))
    ctrl = _douglas_peucker(np.vstack([r, r[:1]]), tol)[:-1]
    if len(ctrl) < 4:
        ctrl = r[np.linspace(0, len(r) - 1, 6).astype(int)]
    pts = ctrl
    for _ in range(CHAIKIN):
        nxt = np.roll(pts, -1, axis=0)
        cut = np.empty((len(pts) * 2, 2))
        cut[0::2] = 0.75 * pts + 0.25 * nxt
        cut[1::2] = 0.25 * pts + 0.75 * nxt
        pts = cut
    closed = np.vstack([pts, pts[:1]])
    seg = np.hypot(*np.diff(closed, axis=0).T)
    per = float(seg.sum())
    n = int(np.clip(round(per / POINT_EVERY), *RING_POINTS))
    at = np.concatenate([[0], np.cumsum(seg)])
    t = np.linspace(0, per, n, endpoint=False)
    return np.column_stack([np.interp(t, at, closed[:, 0]), np.interp(t, at, closed[:, 1])])


def _ring_area(r):
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _inside(pt, ring):
    x, y = pt
    xs, ys = ring[:, 0], ring[:, 1]
    xj, yj = np.roll(xs, 1), np.roll(ys, 1)
    c = ((ys > y) != (yj > y)) & (x < (xj - xs) * (y - ys) / np.where(yj - ys == 0, 1e-12, yj - ys) + xs)
    return bool(np.count_nonzero(c) % 2)


class Canvas:
    """A 0.025 deg raster round a set of model cells, for drawing outlines."""

    def __init__(self, lat0, lat1, lon0, lon1, pad=2.0):
        self.n = math.ceil(lat0 + pad); self.s = math.floor(lat1 - pad)
        self.w = math.floor(lon0 - pad); self.e = math.ceil(lon1 + pad)
        self.lats = np.arange(self.n, self.s - DRAW_RES / 2, -DRAW_RES)
        self.lons = np.arange(self.w, self.e + DRAW_RES / 2, DRAW_RES)
        self.kx = math.cos(math.radians((self.n + self.s) / 2))   # lon degrees -> lat-degree lengths

    def paint(self, cells, res):
        """Boolean raster: True where the centre falls in one of the model cells (lat, lon, res)."""
        m = np.zeros((len(self.lats), len(self.lons)), bool)
        h = res / 2
        for lat, lon in cells:
            i0 = int(np.searchsorted(-self.lats, -(lat + h))); i1 = int(np.searchsorted(-self.lats, -(lat - h)))
            j0 = int(np.searchsorted(self.lons, lon - h)); j1 = int(np.searchsorted(self.lons, lon + h))
            m[i0:i1, j0:j1] = True
        return m

    def shape(self, mask, sigma, level, clip=None):
        """The hazard cells blurred into a smooth field, scaled so `level` is its edge (0.5 after
        scaling). clip: an outer shape's field; this one is held inside it."""
        if not np.any(mask):
            return None
        z = ndimage.gaussian_filter(mask.astype(np.float32), sigma=(sigma / DRAW_RES, sigma / DRAW_RES / self.kx), mode="constant")
        z = z * (0.5 / level)
        if clip is not None:
            z = np.where(clip >= 0.5, z, np.minimum(z, 0.45))
        return z

    def rings(self, z, level=0.5):
        """Outer rings (holes dropped) of the area where z >= level, smoothed, as arrays of [lon, lat]."""
        import contourpy
        if z is None or (z >= level).sum() == 0:
            return []
        zz = np.pad(z, 1)
        lons = np.concatenate([[self.lons[0] - DRAW_RES], self.lons, [self.lons[-1] + DRAW_RES]])
        lats = np.concatenate([[self.lats[0] + DRAW_RES], self.lats, [self.lats[-1] - DRAW_RES]])
        gen = contourpy.contour_generator(lons, lats, zz, line_type=contourpy.LineType.Separate)
        lines = [np.asarray(l) for l in gen.lines(level) if len(l) >= 4]
        rings = []
        for l in sorted(lines, key=lambda r: -abs(_ring_area(r))):
            if any(_inside(l[0], o) for o in rings):
                continue  # a hole (or something inside a ring we already have)
            rings.append(l)
        return [r for r in (smooth_ring(r) for r in rings) if r is not None]


def km2(n_cells, res, lat):
    return n_cells * (res * 111.2) ** 2 * math.cos(math.radians(lat))


def item_support(it, fine, run_step):
    """Model cells (lat, lon) carrying the item's hazard on its days, near its outlook points, and
    those carrying the dangerous tier. Falls back to the outlook's own boxes when the fine grid has
    nothing there (fire, heat, frost, snow and cyclones are not on the fine grid at all)."""
    fam = it["fam"]
    pts = it["pts"]
    if fine is None or fam not in ("storms", "rain", "wind"):
        return None
    lat = np.array([p[0] for p in pts])
    # footprint: within ~0.8 deg (round, not a box) of the outlook points that fired
    seeds = np.ones((len(fine.lats), len(fine.lons)), bool)
    for la, lo in pts:
        seeds[fine.ij(la, lo)] = False
    kx = math.cos(math.radians(float(np.mean(lat))))
    near = ndimage.distance_transform_edt(seeds, sampling=(fine.res, fine.res * kx)) <= FOOTPRINT
    hit = np.zeros_like(near); danger = np.zeros_like(near)
    lvl_in = (it["level"] if fam != "storms" else None)
    for date in it["dates"]:
        k = fine.day_index(date)
        if k is None:
            continue
        f = fine.days[k]["fam"][fam]
        hit |= (f >= 1) & near
        if fam == "storms":
            danger |= (f >= 2) & near
        elif lvl_in and lvl_in > 1:
            danger |= (f >= lvl_in) & near
    I, J = np.nonzero(hit)
    cells = list(zip(fine.lats[I].tolist(), fine.lons[J].tolist()))
    I, J = np.nonzero(danger)
    inner = list(zip(fine.lats[I].tolist(), fine.lons[J].tolist()))
    return {"cells": cells, "inner": inner, "res": fine.res,
            "km2": km2(len(cells), fine.res, float(np.mean(lat))) if cells else 0.0}


def outline(it, support, run_step):
    """{'outer': [ring...], 'inner': [ring...], 'km2': area of hazard, 'fine': bool}.
    Rings are arrays of [lon, lat]."""
    pts = it["pts"]
    inner_pts = sorted(it.get("inner") or ())
    fine = bool(support and support["cells"])
    boxes = [tuple(p) for p in pts]          # the outlook's own boxes always sit inside the outline
    cells = support["cells"] if fine else []
    inner_cells = support["inner"] if fine else []
    res = support["res"] if fine else run_step
    area = support["km2"] if fine else km2(len(boxes), run_step, float(np.mean([p[0] for p in pts])))
    allc = boxes + cells
    lats = [c[0] for c in allc]; lons = [c[1] for c in allc]
    cv = Canvas(max(lats), min(lats), min(lons), max(lons))
    m = cv.paint(boxes, run_step) | (cv.paint(cells, res) if cells else False)
    z = cv.shape(m, **OUTER)
    outer = cv.rings(z)
    inner = []
    im = (cv.paint(inner_cells, res) if inner_cells else False) | (cv.paint(inner_pts, run_step) if inner_pts else False)
    if im is not False and np.any(im):
        inner = cv.rings(cv.shape(im, clip=z, **INNER))
    cells = allc
    # Separate blobs need hazard of their own: a lone cell or two is dropped unless it sits on a metro
    # centre (Josh, 20 Nov). Blobs that hold one of the outlook's points always stay.
    keep = []
    for r in outer:
        n_in = sum(1 for c in cells if _inside((c[1], c[0]), r))
        has_pt = any(_inside((p[1], p[0]), r) for p in pts)
        blob_km2 = km2(n_in, res, float(np.mean(r[:, 1])))
        c_lat, c_lon = float(np.mean(r[:, 1])), float(np.mean(r[:, 0]))
        if has_pt or blob_km2 >= MIN_BLOB_KM2 or near_metro(c_lat, c_lon):
            keep.append(r)
    inner = [r for r in inner if any(_inside(r[0], o) for o in keep)]
    return {"outer": keep, "inner": inner, "km2": area, "fine": fine}


def pack_rings(rings, q=100):
    """[[x0, y0, dx1, dy1, ...], ...] in hundredths of a degree (lon, lat), first point not repeated."""
    out = []
    for r in rings:
        a = np.rint(np.asarray(r) * q).astype(int)
        if len(a) > 1 and (a[0] == a[-1]).all():
            a = a[:-1]
        keep = [a[0]]
        for p in a[1:]:
            if (p != keep[-1]).any():
                keep.append(p)
        a = np.array(keep)
        if len(a) < 3:
            continue
        d = np.vstack([a[:1], np.diff(a, axis=0)])
        out.append(d.reshape(-1).tolist())
    return out


def unpack_rings(packed, q=100):
    out = []
    for flat in packed:
        a = np.cumsum(np.array(flat, float).reshape(-1, 2), axis=0) / q
        out.append(a)
    return out
