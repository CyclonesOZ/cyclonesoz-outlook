"""Our own ECMWF grids for the morning scan: 0.25 deg (lands ~3:55 am AWST) or 0.1 deg (~6 am).

The outlook (build_outlook.R) samples Open-Meteo at 1,342 points 0.72 deg apart, which is why the
morning email's areas came out as boxes. The model viewer's pipeline already holds the full ECMWF
grids, with the severe set worked out (model-worker/severe.py): 8x the cells at 0.25 deg and 52x at
0.1 deg. This reads them, for Australia only, and boils each Perth day (8 am to 8 am AWST, 00Z to
00Z) down to the numbers the hazard rules need.

Frames come from models.cyclonesoz.com (16-bit PNG, value = q * scale + offset, 65535 = none).
Severe fields are paid there, so a script needs MODEL_GATE_KEY (Actions secret; on Josh's Mac it is
read from ~/.cyclonesoz/models.env). MODEL_FRAMES_DIR reads a model-worker out/ folder instead.
The key is sent as a header only and never printed.
"""
import concurrent.futures as cf
import datetime as dt
import io
import json
import os
import urllib.request

import numpy as np
from PIL import Image

BASE = os.environ.get("MODEL_BASE_URL") or "https://models.cyclonesoz.com"
NODATA = 65535
# Australia and its coastal waters, a little wider than the Post Alert page's bounds (108-160E, 8-45S)
BOX = (108.0, -46.0, 160.0, -8.0)
DAY_FIELDS = ["tp", "cp", "gust", "tcwv", "mucape", "mucin", "shear6", "scp", "stp", "ship",
              "dcape", "lr03", "frzl", "t500", "t700", "r700", "u500", "v500", "u850", "v850", "t2m"]


def _gate_key():
    k = os.environ.get("MODEL_GATE_KEY")
    if k:
        return k
    try:
        with open(os.path.expanduser("~/.cyclonesoz/models.env")) as f:
            for line in f:
                if line.startswith("MODEL_GATE_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return None


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "cyclonesoz-alert-scan"})
    key = _gate_key()
    if key and url.startswith(BASE):
        req.add_header("X-Gate-Key", key)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _hours(a, b):
    return int(round((b - a).total_seconds() / 3600))


class ModelRun:
    """One complete ECMWF run (`ifs-0p25/2026111912` or `ifs-0p1/...`), cropped to BOX."""

    def __init__(self, key, manifest, frames_dir=None, cache_dir=None):
        self.key, self.m = key, manifest
        self.frames_dir, self.cache_dir = frames_dir, cache_dir
        self.res = float(manifest["resolution"])
        self.run = dt.datetime.fromisoformat(manifest["run"].replace("Z", "+00:00"))
        w, s, e, n = manifest["bbox"]
        W, H = manifest["width"], manifest["height"]
        # rows run north to south (checked against the land-sea mask: row 0 is 12N)
        lons = w + np.arange(W) * (e - w) / (W - 1)
        lats = n - np.arange(H) * (n - s) / (H - 1)
        c = (lons >= BOX[0] - 1e-6) & (lons <= BOX[2] + 1e-6)
        r = (lats >= BOX[1] - 1e-6) & (lats <= BOX[3] + 1e-6)
        self.cols = slice(int(np.argmax(c)), int(len(c) - np.argmax(c[::-1])))
        self.rows = slice(int(np.argmax(r)), int(len(r) - np.argmax(r[::-1])))
        self.lons, self.lats = lons[self.cols], lats[self.rows]
        self._mem = {}

    @property
    def label(self):
        return "ECMWF %s %s" % ("9 km" if self.res < 0.2 else "25 km", self.run.strftime("%d/%HZ"))

    def steps(self, field):
        return self.m["fields"][field]["steps"]

    def has(self, field):
        return field in self.m["fields"]

    def _raw(self, field, step):
        name = "%s/%03d.png" % (field, step)
        if self.frames_dir:
            with open(os.path.join(self.frames_dir, self.key, name), "rb") as f:
                return f.read()
        path = os.path.join(self.cache_dir, self.key, name) if self.cache_dir else None
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        data = _get("%s/%s/%s" % (BASE, self.key, name))
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
        return data

    def frame(self, field, step):
        k = (field, step)
        if k not in self._mem:
            a = np.asarray(Image.open(io.BytesIO(self._raw(field, step))))
            q = ((a[..., 0].astype(np.uint32) << 8) | a[..., 1])[self.rows, self.cols]
            f = self.m["fields"][field]
            v = q.astype(np.float32) * float(f["scale"]) + float(f["offset"])
            v[q == NODATA] = np.nan
            self._mem[k] = v
        return self._mem[k]

    def prefetch(self, pairs, workers=12):
        todo = [p for p in pairs if p not in self._mem]
        with cf.ThreadPoolExecutor(workers) as ex:
            list(ex.map(lambda p: self.frame(*p), todo))

    def acc(self, field, step):
        """An accumulated field (tp, cp: mm since the run started) at a step; step 0 is 0."""
        if step <= 0:
            return np.zeros((len(self.lats), len(self.lons)), np.float32)
        return self.frame(field, step)

    def day_steps(self, field, day):
        """Steps of `field` inside the Perth day starting `day` (date): (00Z, 00Z next day]."""
        s0 = _hours(self.run, dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc))
        return [s for s in self.steps(field) if s0 < s <= s0 + 24], s0

    def covers(self, day):
        s0 = _hours(self.run, dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc))
        return s0 >= 0 and s0 + 24 in self.steps("tp")

    def day(self, day):
        """Per-cell numbers for one Perth day, for the hazard rules (fine_areas.py)."""
        need = []
        for f in DAY_FIELDS:
            if self.has(f):
                need += [(f, s) for s in self.day_steps(f, day)[0]]
        s0 = self.day_steps("tp", day)[1]
        need += [(f, s) for f in ("tp", "cp") for s in (s0, s0 + 24) if s > 0]
        self.prefetch(need)
        out = {"date": day.isoformat()}
        out["rain"] = np.maximum(self.acc("tp", s0 + 24) - self.acc("tp", s0), 0)
        out["crain"] = np.maximum(self.acc("cp", s0 + 24) - self.acc("cp", s0), 0)
        # gust is the max since the previous step, so every step in the day counts (hourly to +90 h)
        out["gust"] = np.nanmax([self.frame("gust", s) for s in self.day_steps("gust", day)[0]], axis=0) * 3.6
        out["pw"] = np.nanmax([self.frame("tcwv", s) for s in self.day_steps("tcwv", day)[0]], axis=0)
        out["t2max"] = np.nanmax([self.frame("t2m", s) for s in self.day_steps("t2m", day)[0]], axis=0)
        # Severe set: 3-hourly. The outlook averages its 6 most severe hours (TOPN, sev_score); with
        # 3-hourly frames that is the 2 most severe steps.
        steps, _ = self.day_steps("scp", day)
        stack = {f: np.stack([self.frame(f, s) for s in steps]) for f in
                 ("mucape", "mucin", "shear6", "scp", "stp", "ship", "dcape", "lr03", "frzl", "t500", "t700", "r700",
                  "u500", "v500", "u850", "v850") if self.has(f)}
        for f in stack:
            stack[f] = np.nan_to_num(stack[f], nan=0.0)
        sev = 2 * stack["scp"] + 2 * stack["stp"] + 2 * stack["ship"] + stack["mucape"] / 500 + stack["shear6"] * 1.94384 / 20
        order = np.argsort(-sev, axis=0)
        top = order[:2]
        take = lambda a, idx: np.take_along_axis(a, idx[None], 0)[0]
        for f in ("mucape", "mucin", "shear6", "scp", "stp", "ship", "dcape", "lr03"):
            out[f] = (take(stack[f], top[0]) + take(stack[f], top[1])) / 2
        # hail: the single peak-SHIP step, with its own CAPE (as the outlook does)
        pk = np.argmax(stack["ship"], axis=0)
        out["ship_peak"] = take(stack["ship"], pk)
        out["cape_at_ship"] = take(stack["mucape"], pk)
        frz = np.where(stack["frzl"] > 0, stack["frzl"], 1e9)
        out["frzl"] = np.minimum(take(frz, top[0]), take(frz, top[1]))
        out["t500"] = np.minimum(take(stack["t500"], top[0]), take(stack["t500"], top[1]))
        # 700 hPa dew point depression from T and RH (Magnus), for the downdraft rules
        t7, rh7 = take(stack["t700"], top[0]), np.clip(take(stack["r700"], top[0]), 1, 100)
        g = np.log(rh7 / 100) + 17.62 * t7 / (243.12 + t7)
        out["dd700"] = t7 - 243.12 * g / (17.62 - g)
        # DCP (Evans and Doswell), mean 850-500 wind standing in for the 0-6 km mean wind
        mw = np.hypot((stack["u850"] + stack["u500"]) / 2, (stack["v850"] + stack["v500"]) / 2) * 1.94384
        mw = take(mw, top[0])
        out["dcp"] = (out["dcape"] / 980) * (out["mucape"] / 2000) * (out["shear6"] * 1.94384 / 20) * (mw / 16)
        return out


def latest_run(prefer=("ifs-0p1", "ifs-0p25"), max_age_h=40, now=None, frames_dir=None, cache_dir=None):
    """The newest complete ECMWF run, 0.1 deg when the newest run has it, else 0.25 deg."""
    frames_dir = frames_dir or os.environ.get("MODEL_FRAMES_DIR")
    if frames_dir:
        runs = []
        for model in prefer:
            d = os.path.join(frames_dir, model)
            for r in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                mf = os.path.join(d, r, "manifest.json")
                if os.path.exists(mf):
                    runs.append({"key": "%s/%s" % (model, r), "status": json.load(open(mf)).get("status")})
    else:
        runs = json.loads(_get(BASE + "/index.json"))["runs"]
    now = now or dt.datetime.now(dt.timezone.utc)
    best = None
    for r in runs:
        model, _, stamp = r["key"].partition("/")
        if model not in prefer or r.get("status") != "complete":
            continue
        t = dt.datetime.strptime(stamp, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        if (now - t).total_seconds() / 3600 > max_age_h:
            continue
        rank = (t, -prefer.index(model))
        if best is None or rank > best[0]:
            best = (rank, r["key"])
    if not best:
        return None
    key = best[1]
    if frames_dir:
        m = json.load(open(os.path.join(frames_dir, key, "manifest.json")))
    else:
        m = json.loads(_get("%s/%s/manifest.json" % (BASE, key)))
    return ModelRun(key, m, frames_dir=frames_dir, cache_dir=cache_dir)
