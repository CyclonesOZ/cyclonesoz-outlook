#!/usr/bin/env python3
"""Build data/fine_climatology.npz: the two climate maps the morning scan's heavy-rain rule needs,
on a 0.1 deg grid over Australia (fine_grid.BOX), one layer per calendar month.

  rain_mean  1991-2020 average monthly rainfall (mm). SILO monthly_rain grids, Queensland
             Government, CC BY 4.0 (https://www.longpaddock.qld.gov.au/silo/), averaged from its
             0.05 deg grid. Not a Bureau product and no Bureau licence is claimed.
  pw_thr     precipitable water (mm) above which the atmosphere can support heavy rain.

pw_thr comes from Josh's own lines (20 Nov 2026, drawn on the severe watch map): 70 mm across the
Top End and Cape York, 60 mm from the Pilbara to central QLD, 50 mm across the middle and down the
NSW coast, 40 mm across the south. Those were drawn by hand, for one season. To make them follow
the real geography and work all year, they are fitted as a straight-line scaling of November's
precipitable-water climate (NCEP/NCAR Reanalysis 1, daily, 1991-2020, NOAA PSL, public domain):
the climate gives the shape, his lines give the levels. In drier months the threshold falls with
the climate. High ground holds less water than the 2.5 deg reanalysis sees, so the threshold is
lowered with height (water vapour scale height 2.2 km).

Run once (inputs are ~700 MB; download lists in the docstring of main()). Needs numpy, scipy, h5py.
"""
import argparse
import glob
import json
import math
import os

import h5py
import numpy as np
from scipy import ndimage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "fine_climatology.npz")
BOX = (108.0, -46.0, 160.0, -8.0)
RES = 0.1
H_VAPOUR = 2200.0
REF_MONTH = 11  # Josh drew the lines in November

# Josh's lines, read off his drawing in screen pixels and converted with the map's own scale
# (Web Mercator; Darwin, Sydney, Melbourne, Brisbane and Perth labels used as control points).
JOSH_LINES_PX = {
    70: [(710, 135), (760, 165), (850, 197), (910, 200), (980, 180), (1060, 157), (1130, 180), (1190, 230),
         (1260, 280), (1320, 320), (1420, 360), (1500, 378), (1590, 377)],
    60: [(508, 235), (540, 300), (600, 370), (700, 410), (800, 430), (940, 460), (1020, 480), (1120, 505),
         (1200, 540), (1300, 590), (1380, 618), (1440, 605), (1480, 580), (1560, 560), (1725, 550)],
    50: [(232, 683), (500, 690), (780, 695), (900, 710), (1050, 730), (1200, 752), (1300, 770), (1380, 795),
         (1430, 830), (1475, 855), (1490, 920), (1510, 970), (1550, 1000), (1650, 1020), (1730, 1030)],
    40: [(318, 1090), (600, 1130), (900, 1160), (1200, 1170), (1450, 1185), (1620, 1190)],
}


def px_to_ll(x, y):
    lon = 130.84 + (x - 877) / 34.2
    merc = -0.2182 + (y - 127) / -1954.9
    return math.degrees(2 * math.atan(math.exp(merc)) - math.pi / 2), lon


def josh_points(step_deg=0.25):
    """Points every ~step_deg along each line, with its value."""
    out = []
    for v, line in JOSH_LINES_PX.items():
        ll = [px_to_ll(*p) for p in line]
        for (a, b) in zip(ll, ll[1:]):
            n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / step_deg))
            for k in range(n):
                t = k / n
                out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), v))
    return out


def grid():
    lons = np.round(np.arange(BOX[0], BOX[2] + RES / 2, RES), 2)
    lats = np.round(np.arange(BOX[3], BOX[1] - RES / 2, -RES), 2)  # north to south, like the frames
    return lats, lons


def silo_mean(files, lats, lons):
    acc = np.zeros((12, 681, 841)); n = np.zeros((12, 681, 841))
    for p in sorted(files):
        with h5py.File(p, "r") as f:
            q = f["monthly_rain"][:]
            ok = q != f["monthly_rain"].attrs["_FillValue"][0]
            v = q * f["monthly_rain"].attrs["scale_factor"][0] + f["monthly_rain"].attrs["add_offset"][0]
            acc += np.where(ok, v, 0); n += ok
            slat, slon = f["lat"][:], f["lon"][:]
    mean = np.where(n > 0, acc / np.maximum(n, 1), np.nan)            # (12, lat S->N, lon)
    # 0.05 -> 0.1 deg: our points sit on SILO's; average the 3x3 SILO cells around each (land only)
    land = np.isfinite(mean)
    s = ndimage.uniform_filter(np.where(land, mean, 0), size=(1, 3, 3), mode="nearest")
    w = ndimage.uniform_filter(land.astype(float), size=(1, 3, 3), mode="nearest")
    smooth = np.where(land, s / np.maximum(w, 1e-9), np.nan)
    out = np.full((12, len(lats), len(lons)), np.nan, np.float32)
    r = np.rint((lats - slat[0]) / 0.05).astype(int); c = np.rint((lons - slon[0]) / 0.05).astype(int)
    okr = (r >= 0) & (r < len(slat)); okc = (c >= 0) & (c < len(slon))
    sub = smooth[:, r[okr]][:, :, c[okc]]
    out[:, np.where(okr)[0][:, None], np.where(okc)[0][None, :]] = sub
    # Ocean and islands outside SILO's land mask: carry the nearest land value out ~1 deg, so the
    # rule still works on the coast and offshore islands; beyond that the rule uses the floor bars.
    for m in range(12):
        a = out[m]
        bad = np.isnan(a)
        idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
        filled = a[tuple(idx)]
        dist = ndimage.distance_transform_edt(bad) * RES
        out[m] = np.where(bad & (dist <= 1.0), filled, a)
    return out


def pw_percentiles(files, pcts):
    """{month: (lat S->N, lon, {p: field})} over the Australian region, from NCEP daily PW."""
    days = {m: [] for m in range(1, 13)}
    import datetime as dt
    for p in sorted(files):
        with h5py.File(p, "r") as f:
            lat, lon = f["lat"][:], f["lon"][:]
            r = (lat <= -2.5) & (lat >= -52.5); c = (lon >= 100) & (lon <= 167.5)
            v = f["pr_wtr"][:, r, :][:, :, c]
            t = f["time"][:]
            base = dt.datetime(1800, 1, 1)
            for k in range(len(t)):
                days[(base + dt.timedelta(hours=float(t[k]))).month].append(v[k])
            plat, plon = lat[r], lon[c]
    out = {}
    for m, arrs in days.items():
        a = np.stack(arrs)
        out[m] = {p: np.percentile(a, p, axis=0) for p in pcts}
    return plat, plon, out


def to_grid(field, plat, plon, lats, lons):
    """Smooth 2.5 deg field onto the 0.1 deg grid (cubic spline through the coarse values)."""
    order = np.argsort(plat)
    f = field[order]; la = plat[order]
    yi = np.interp(lats, la, np.arange(len(la)))
    xi = np.interp(lons, plon, np.arange(len(plon)))
    Y, X = np.meshgrid(yi, xi, indexing="ij")
    return ndimage.map_coordinates(f.astype(np.float64), [Y, X], order=3, mode="nearest").astype(np.float32)


def elevation(orog_npy, model_bbox, lats, lons, ncep_hgt=None):
    """Height (m) on our grid from a model-worker orog.npy, and the same smoothed to 2.5 deg."""
    z = np.load(orog_npy)
    w, s, e, n = model_bbox
    H, W = z.shape
    yi = (n - lats) / (n - s) * (H - 1); xi = (lons - w) / (e - w) * (W - 1)
    Y, X = np.meshgrid(yi, xi, indexing="ij")
    zf = ndimage.map_coordinates(np.nan_to_num(z.astype(np.float64)), [Y, X], order=1)
    zf = np.maximum(zf, 0)
    zc = ndimage.gaussian_filter(zf, sigma=2.5 / RES / 2.355 * 2)  # ~2.5 deg footprint, as the reanalysis sees it
    return zf.astype(np.float32), zc.astype(np.float32)


def main():
    """Inputs (download once, any folder):
      SILO   https://s3-ap-southeast-2.amazonaws.com/silo-open-data/Official/annual/monthly_rain/<YYYY>.monthly_rain.nc
      NCEP   https://downloads.psl.noaa.gov/Datasets/ncep.reanalysis.dailyavgs/surface/pr_wtr.eatm.<YYYY>.nc
      for YYYY 1991..2020, and an orog.npy from model-worker/out/ifs-0p1/<run>/ (model terrain)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--silo", required=True, help="folder of SILO monthly_rain .nc files")
    ap.add_argument("--pw", required=True, help="folder of NCEP pr_wtr.eatm .nc files")
    ap.add_argument("--orog", required=True, help="model-worker orog.npy (0.1 deg run)")
    ap.add_argument("--orog-bbox", default="50,-52,180,12")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    lats, lons = grid()

    rain = silo_mean(glob.glob(os.path.join(a.silo, "*.monthly_rain.nc")), lats, lons)

    pcts = [75, 90, 95]
    plat, plon, P = pw_percentiles(glob.glob(os.path.join(a.pw, "pr_wtr.eatm.*.nc")), pcts)
    zf, zc = elevation(a.orog, [float(x) for x in a.orog_bbox.split(",")], lats, lons)
    lift = np.exp(-(zf - zc) / H_VAPOUR)

    # Josh's lines as a straight-line scaling of the climate: thr = a + b * P(cell). Fitted on
    # November, when he drew them. The reanalysis reads drier than ECMWF (its own 99.5th
    # percentile sits ~12 mm under his lines), so a raw percentile cannot match; a and b absorb that.
    pts = josh_points()
    ii = np.array([np.argmin(abs(lats - p[0])) for p in pts]); jj = np.array([np.argmin(abs(lons - p[1])) for p in pts])
    want = np.array([p[2] for p in pts], np.float64)
    fits = []
    for p in pcts:
        x = (to_grid(P[REF_MONTH][p], plat, plon, lats, lons) * lift)[ii, jj]
        (a0, b0), *_ = np.linalg.lstsq(np.vstack([np.ones_like(x), x]).T, want, rcond=None)
        pred = a0 + b0 * x
        fits.append((float(np.sqrt(np.mean((pred - want) ** 2))), p, float(a0), float(b0),
                     {v: round(float(pred[want == v].mean()), 1) for v in JOSH_LINES_PX}))
    fits.sort()
    rmse, p_best, a0, b0, by_line = fits[0]
    ref = to_grid(P[REF_MONTH][p_best], plat, plon, lats, lons) * lift
    # Other months: the threshold comes down when the air is drier than in November, never up.
    # (The wet season's own percentiles would push the north past 80 mm, a value the model seldom
    # reaches, which would switch the rule off right when it matters.)
    pw_thr = np.stack([a0 + b0 * np.minimum(to_grid(P[m][p_best], plat, plon, lats, lons) * lift, ref)
                       for m in range(1, 13)])

    meta = {
        "built": __import__("datetime").date.today().isoformat(),
        "grid": {"box": BOX, "res": RES, "rows": "north to south"},
        "rain_mean": "SILO monthly_rain 1991-2020 mean, mm per month (CC BY 4.0, Queensland Government)",
        "pw_thr": ("%.1f + %.2f x the %sth percentile of daily precipitable water for the month (NCEP/NCAR R1, "
                   "1991-2020, NOAA PSL, public domain), capped at November's, lowered with height (scale "
                   "height %d m). Fitted to Josh's 20 Nov 2026 lines." % (a0, b0, p_best, H_VAPOUR)),
        "fit": {"percentile": p_best, "a": round(a0, 2), "b": round(b0, 3), "rmse_mm": round(rmse, 2),
                "mean_on_each_line_mm": by_line,
                "others": [{"p": f[1], "rmse": round(f[0], 2)} for f in fits[1:]]},
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    np.savez_compressed(a.out, lats=lats, lons=lons,
                        rain_mean=np.nan_to_num(np.round(rain * 10), nan=65535).clip(0, 65535).astype(np.uint16),
                        pw_thr=np.round(pw_thr * 10).clip(0, 65534).astype(np.uint16),
                        meta=json.dumps(meta))
    print(json.dumps(meta, indent=1))
    print("wrote %s (%.1f MB)" % (a.out, os.path.getsize(a.out) / 1e6))


if __name__ == "__main__":
    main()
