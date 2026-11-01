#!/usr/bin/env python3
"""Lays the forecaster's edited categories over the raw model outlook.

The manual outlook (planned 7 Oct 2026, built 8 Oct): the pipeline still builds the model outlook
every night, but the daily category map that members see is the forecaster's. Each morning Josh
edits the 8 days of categories in the edit room (editroom/), and publishing writes his category
grid for every date to docs/archive/manual/edits.json. Each night the pipeline builds a fresh raw
run and this module lays the newest edits over it, so:

  * the day that has passed drops off and today is day 1 (edits are keyed by DATE, not day index);
  * a date nobody has edited yet (the new day 8, or every date after a run of skipped mornings)
    comes straight from the raw run;
  * everything other than the category -- rain, flood, fire, the hover numbers -- stays fresh from
    the raw run. Only the category is held, plus the fields a member's summary reads alongside it
    (thunderstorm chance, hail, wind), which are brought into line with it at EVERY point of a day
    he published, so a summary can never say "Chance of thunderstorms" where his map shows no risk.
    (The model alone often has a 20%+ thunderstorm chance at points it gives no category.)

On every day the forecaster has published, each point's 3-hourly frames are capped at his category
for that day, so the 3-hourly view never shows storms where his map shows none, or a worse category
than his. Where he raised a category the frames stay as the model has them (nothing is invented).
Days nobody has edited yet keep the model's frames.

Days 5-8 are edited on the public map's coarse scale (none / chance of storm / chance of severe
storm). When such a date moves into days 1-4 it has to become a full category, so each point is
re-based on the fresh raw run within what the coarse edit allowed, and the edit room flags that
day for review.

Used two ways, with the same code, so the published product and the edit room's preview can never
disagree:
  * by pipeline/build_outlook.R, when MANUAL_OUTLOOK is TRUE:  python3 pipeline/manual_compose.py --pipeline
  * by editroom/server.py, imported, for the draft preview and the publish itself.
Standard library only (it runs on the GitHub runner and on macOS's bundled python3 3.9).
"""
import datetime as _dt
import json
import math
import os
import re
import subprocess
import sys
import tempfile

RAW_DIR = "docs/archive/raw"
EDITS_PATH = "docs/archive/manual/edits.json"
OUTLOOK_PATH = "docs/outlook.json"
FRAME_DIR = "docs/archive/frames"

FINE_DAYS = 4            # days 1-4 use TSTM/MRGL/MDT/HIGH; 5-8 the coarse scale (docs/index.html)
NO_STORM_TPROB = 19      # just under the viewer's and summaries' "Chance" bar of 20%
MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


# ---------------------------------------------------------------- categories ----------------
def coarse_class(cat):
    """What the public map shows for a category on days 5-8: 0 none, 1 chance of storm, 2 chance
    of severe storm. Matches COARSE in docs/index.html (thresholds 0.5 and 3.5)."""
    return 0 if cat < 1 else (2 if cat >= 4 else 1)


def rebase_coarse(edit_cat, raw_cat):
    """A coarse-scale edit for a date that is now on the full scale: keep the forecaster's call
    (none / storm / severe) and take the detail inside it from the fresh raw run."""
    cls = coarse_class(edit_cat)
    if cls == 0:
        return 0
    if cls == 1:
        return min(max(raw_cat, 1), 3)
    return min(max(raw_cat, 2), 4)


def tprob_floor(tprob, cat):
    """Same rule as tprob_floor() in build_outlook.R."""
    if cat >= 3:
        return max(tprob, 80)
    if cat >= 1:
        return max(tprob, 20)
    return tprob


def harmonise(rec, cat):
    """One point-day under the forecaster's category, with the fields a member's summary reads
    brought into line with it using the pipeline's own rules: thunderstorm chance under the
    "Chance" bar where he shows no risk and at least at it where he shows any (80%+ from Moderate
    up), hail no higher than the category, no damaging wind below Marginal and nothing above
    Damaging below Moderate. Returns rec itself when nothing needs to change."""
    raw_c = rec.get("cat", 0)
    tp = rec.get("tprob", 0) or 0
    hail = rec.get("hail", 0) or 0
    wind = rec.get("wind", 0) or 0
    if cat <= 0:
        ntp, nhail, nwind = min(tp, NO_STORM_TPROB), 0, 0
    else:
        ntp, nhail = tprob_floor(tp, cat), min(hail, cat)
        nwind = 0 if cat < 2 else (min(wind, 1) if cat < 3 else wind)
    if cat == raw_c and (ntp, nhail, nwind) == (tp, hail, wind):
        return rec
    out = dict(rec)
    out.update(cat=cat, tprob=ntp, hail=nhail, wind=nwind)
    if cat != raw_c:
        out["raw_cat"] = raw_c
        out["conditional"] = False
        if cat < 2:
            out["hatch"] = 0
    return out


def mask_frame(vals, cols, cap):
    """Caps one 3-hourly frame at the forecaster's category for that day."""
    v = list(vals)
    ci = {c: i for i, c in enumerate(cols)}
    if "cat" not in ci:
        return v
    if v[ci["cat"]] <= cap:
        # no storms on his map: no thunderstorm chance in the frames either
        if cap <= 0 and "tprob" in ci and v[ci["tprob"]] > NO_STORM_TPROB:
            v[ci["tprob"]] = NO_STORM_TPROB
        return v
    v[ci["cat"]] = cap
    if "tprob" in ci:
        v[ci["tprob"]] = min(v[ci["tprob"]], NO_STORM_TPROB) if cap <= 0 else tprob_floor(v[ci["tprob"]], cap)
    if "hail" in ci:
        v[ci["hail"]] = 0 if cap <= 0 else min(v[ci["hail"]], cap)
    if "wind" in ci:
        w = v[ci["wind"]]
        v[ci["wind"]] = 0 if cap < 2 else (min(w, 1) if cap < 3 else w)
    return v


# ---------------------------------------------------------------- dates ---------------------
def run_dates(raw):
    """ISO date of each forecast day. build_outlook.R writes "dates" since 8 Oct 2026; older files
    are dated from run_date the way the pipeline anchors day 1 (the AWST calendar date), checked
    against the day labels."""
    days = raw.get("days") or []
    if raw.get("dates"):
        return list(raw["dates"])
    t = _dt.datetime.strptime(raw["run_date"], "%Y-%m-%dT%H:%M:%SZ") + _dt.timedelta(hours=8)
    start = t.date()
    if days:
        m = re.match(r"\w+\s+(\d+)\s+(\w+)", days[0])
        if m and MONTHS.get(m.group(2)):
            for off in (0, -1, 1):
                d = start + _dt.timedelta(days=off)
                if d.day == int(m.group(1)) and d.month == MONTHS[m.group(2)]:
                    start = d
                    break
    n = len(days) or len(raw["points"][0]["d"])
    return [(start + _dt.timedelta(days=j)).isoformat() for j in range(n)]


def pkey(lat, lon):
    return "%.2f,%.2f" % (lat, lon)


# ---------------------------------------------------------------- finer lattice -------------
# Post-production only. build_outlook.R still runs its soundings on the 0.72 degree lattice and
# archive/raw keeps that run untouched. The edit room, and what it publishes, work on a lattice
# twice as fine each way -- about four times the squares -- made here from the model's own points:
#   * every model point is kept exactly as the model has it, first and in its original order;
#   * a point is added halfway between each pair of neighbouring model points, and in the middle
#     of each cell of four (three, where the cell touches the edge of the model's coverage);
#   * an added point is flagged "interp": true, so scoring and alert scans can leave it out, and
#     takes its numbers from the points around it (the mean of its neighbours) and its tiers from
#     the public map's own test (below), then is made coherent with the pipeline's rules.
# Nothing is re-run. The nightly pipeline and the edit room both go through densify(), and it is a
# no-op on a run that is already on the finer lattice.
BASE_STEP = 0.72
GRID_STEP = 0.36
ORDINAL_KEYS = {"cat", "pregate", "hail", "wind", "flood", "fire", "hatch"}   # tiers: never averaged

# The public map (docs/index.html, src/lib/stormOutlookField.js) draws a tier wherever the
# Gaussian-weighted share of nearby model points that reach it is at least that tier's level. An
# added point takes the highest tier the map would shade at ITS position, measured over the model's
# own points -- so a map drawn from the finer lattice lands where the old one did, instead of
# growing or shrinking a halo around every point. These are the map's own constants: if they change
# there, change them here.
KERNEL_SIGMA, KERNEL_RADIUS = 0.60, 2.0          # degrees
TIER_LEVEL = [0.35, 0.48, 0.58, 0.68]            # one per tier, lowest tier first
# Each added point is one more full-weight sample at its own spot, so the map's smoothing comes out a
# little sharper than it was. Held to the map's own levels (1.0) that grows shaded areas by 3-7% and
# thins out the odd borderline patch of a square or two: up to four a run, each about 1 square degree
# (measured over six runs with the app's own polygon code; shapes overlap 92-94%). Asking more of an
# added point (1.1) cancels the growth but loses more of those patches; asking less (0.8) keeps them
# and grows areas 8-16%. The edit room flags any patch you changed that the map would drop.
NODE_LEVEL_SCALE = 1.0
LADDER = {                                       # tier key -> the value each tier needs to reach
    "cat": [1, 2, 3, 4], "hail": [1, 2, 3], "wind": [1, 2, 3], "flood": [1, 2, 3], "fire": [1, 2, 3, 4],
}
COARSE_CAT_LADDER = [1, 4]                       # cat on days 5-8: chance of storm / of severe storm
TPROB_LADDER = [20, 80]                          # Chance / Likely


def _is_tier(key):
    return key in ORDINAL_KEYS or key.endswith("cat")


def ordinal(vals):
    """A tier that has no ladder (pregate, hatch ...): the neighbours' mean, rounded half up."""
    return int(math.floor(sum(vals) / float(len(vals)) + 0.5))


def _num(vals):
    """Mean of numbers: an int when every neighbour's is an int, else one decimal like the run's."""
    m = sum(vals) / float(len(vals))
    if all(isinstance(v, int) for v in vals):
        return int(math.floor(m + 0.5))
    return round(m, 1)


def _rungs(vals_w, ladder):
    """How many tiers of a ladder the public map shades at a spot, from [(value, weight)] of the
    model points around it: tier k is shaded when the weighted share of points at or above
    ladder[k] reaches TIER_LEVEL[k]."""
    tot, acc = 0.0, [0.0] * len(ladder)
    for v, w in vals_w:
        tot += w
        for k, t in enumerate(ladder):
            if v < t:
                break
            acc[k] += w
    n = 0
    if tot > 1e-9:
        for k in range(len(ladder)):
            if acc[k] / tot < TIER_LEVEL[min(k, len(TIER_LEVEL) - 1)] * NODE_LEVEL_SCALE:
                break
            n = k + 1
    return n


def _kernel_nbrs(pts, lay, lat, lon):
    """[(model point index, weight)] for the model points the public map's kernel reaches from here."""
    lat_max, lon_min, at, _, _ = lay
    inv = -1.0 / (2 * KERNEL_SIGMA ** 2)
    rf, cf = (lat_max - lat) / BASE_STEP, (lon - lon_min) / BASE_STEP
    reach = int(math.ceil(KERNEL_RADIUS / BASE_STEP))
    out = []
    for r in range(int(math.floor(rf)) - reach, int(math.ceil(rf)) + reach + 1):
        for c in range(int(math.floor(cf)) - reach, int(math.ceil(cf)) + reach + 1):
            i = at.get((r, c))
            if i is None:
                continue
            d2 = (pts[i]["lat"] - lat) ** 2 + (pts[i]["lon"] - lon) ** 2
            if d2 <= KERNEL_RADIUS ** 2:
                out.append((i, math.exp(d2 * inv)))
    return out


def _held(vals, ladder):
    """How many tiers of a ladder every one of these values reaches."""
    lo = min(vals)
    return sum(1 for t in ladder if lo >= t)


def _field_tiers(recs_w, fine, keys, parents):
    """{key: tier value} the map shades at a spot on one day, from [(record dict, weight)] and the
    spot's own neighbours (parents, a list of records); tprob comes back as "tprob_band" (0 below
    Chance, 1 Chance, 2 Likely). A point between squares that both reach a tier is never below
    it: that is what keeps a two-square patch from being thinned out of existence."""
    out = {}
    for k in keys:
        if k == "tprob":
            n = _rungs([(r.get("tprob", 0) or 0, w) for r, w in recs_w], TPROB_LADDER)
            out["tprob_band"] = max(n, _held([r.get("tprob", 0) or 0 for r in parents], TPROB_LADDER))
            continue
        ladder = COARSE_CAT_LADDER if (k == "cat" and not fine) else LADDER[k]
        n = max(_rungs([(r.get(k, 0) or 0, w) for r, w in recs_w], ladder),
                _held([r.get(k, 0) or 0 for r in parents], ladder))
        out[k] = ladder[n - 1] if n else 0
    return out


def _cohere(r, fine):
    """Brings one point-day (a dict) in line with the rules every model record already obeys:
    thunderstorm chance at least the tier's floor, hail no higher than the category, no damaging
    wind below Marginal and no more than 1 below Moderate, no hatching below Marginal. Where the
    map shades a hazard the category lifts to carry it on days 1-4, which show the hazard maps;
    on days 5-8 (category only) the hazard is cut back instead."""
    c = r.get("cat", 0)
    if fine and "cat" in r:
        need = max(r.get("hail", 0), 2 if r.get("wind", 0) > 0 else 0, 3 if r.get("wind", 0) > 1 else 0)
        c = r["cat"] = max(c, need)
    if "tprob" in r:
        r["tprob"] = tprob_floor(r["tprob"], c)
    if "hail" in r:
        r["hail"] = min(r["hail"], c)
    if "wind" in r:
        r["wind"] = 0 if c < 2 else (min(r["wind"], 1) if c < 3 else r["wind"])
    if "hatch" in r and c < 2:
        r["hatch"] = 0


def _band(tp, band):
    """A thunderstorm chance held inside the band the map shades it in."""
    return min(tp, 19) if band == 0 else (max(tp, 80) if band == 2 else min(max(tp, 20), 79))


def _blend_day(recs, nbr_recs, j):
    """One day's record for an added point: recs are its neighbours' records for the day, nbr_recs
    [(record, weight)] the model points the public map's kernel reaches from it."""
    fine = j < FINE_DAYS
    keys = [k for k in list(LADDER) + ["tprob"] if k in recs[0]]
    tiers = _field_tiers(nbr_recs, fine, keys, recs)
    out = {}
    for k, v0 in recs[0].items():
        vals = [r[k] for r in recs if k in r]
        if len(vals) < len(recs):
            continue                                  # a flag only some neighbours carry: not inherited
        if isinstance(v0, bool):
            if k != "conditional":
                out[k] = any(vals)
        elif isinstance(v0, (int, float)):
            if k in tiers:
                out[k] = tiers[k]
            else:
                out[k] = ordinal(vals) if _is_tier(k) else _num(vals)
        else:
            out[k] = v0
    if "tprob_band" in tiers and "tprob" in out:
        out["tprob"] = _band(out["tprob"], tiers["tprob_band"])
    _cohere(out, fine)
    # "conditional": the category waits on a trigger. It carries over where a neighbour holds the
    # same category; otherwise there is nothing for it to wait on.
    if "conditional" in recs[0]:
        out["conditional"] = any(r.get("conditional") and r.get("cat", 0) == out.get("cat", 0) for r in recs)
    return out


def _blend_frame(par_rows, nbr_rows, cols, fine):
    """One 3-hour frame row for an added point (columns as the frame file's "cols"); par_rows are
    its neighbours' rows for the slot, nbr_rows [(row, weight)] the kernel's."""
    ix = {c: i for i, c in enumerate(cols)}
    recs_w = [(dict(zip(cols, r)), w) for r, w in nbr_rows]
    tiers = _field_tiers(recs_w, fine, [k for k in list(LADDER) + ["tprob"] if k in ix],
                         [dict(zip(cols, r)) for r in par_rows])
    rec = {}
    for ci, name in enumerate(cols):
        vals = [r[ci] for r in par_rows]
        rec[name] = tiers[name] if name in tiers else _num(vals)
    if "tprob_band" in tiers and "tprob" in rec:
        rec["tprob"] = _band(rec["tprob"], tiers["tprob_band"])
    _cohere(rec, fine)
    return [rec[c] for c in cols]


def _base_layout(pts):
    """(lat_max, lon_min, {(row, col): index}, {row: lat}, {col: lon}) for points on one regular
    BASE_STEP lattice, or None when they are not (then nothing is densified)."""
    if not pts:
        return None
    lat_max = max(p["lat"] for p in pts)
    lon_min = min(p["lon"] for p in pts)
    at, row_lat, col_lon = {}, {}, {}
    for i, p in enumerate(pts):
        r = int(round((lat_max - p["lat"]) / BASE_STEP))
        c = int(round((p["lon"] - lon_min) / BASE_STEP))
        if (r, c) in at or abs(lat_max - r * BASE_STEP - p["lat"]) > 0.02 or abs(lon_min + c * BASE_STEP - p["lon"]) > 0.02:
            return None
        at[(r, c)] = i
        if abs(row_lat.setdefault(r, p["lat"]) - p["lat"]) > 0.011 or abs(col_lon.setdefault(c, p["lon"]) - p["lon"]) > 0.011:
            return None
    return lat_max, lon_min, at, row_lat, col_lon


def model_layout(pts):
    """_base_layout of a finer-lattice run's own model points (they come first, in their order, so
    the indices match the whole list), or None."""
    n = 0
    while n < len(pts) and not pts[n].get("interp"):
        n += 1
    if any(not p.get("interp") for p in pts[n:]):
        return None
    return _base_layout(pts[:n])


def densify(raw, frames=None):
    """(raw, frames) on the finer lattice. Returns them unchanged when they already are, or when
    the points are not on a regular lattice."""
    if raw.get("grid_step") == GRID_STEP:
        return raw, frames
    pts = raw.get("points") or []
    lay = _base_layout(pts)
    if lay is None:
        return raw, frames
    _, _, at, row_lat, col_lon = lay
    maxr = max(r for r, _ in at)
    maxc = max(c for _, c in at)
    added, meta = [], []
    for r2 in range(2 * maxr + 1):
        for c2 in range(2 * maxc + 1):
            if r2 % 2 == 0 and c2 % 2 == 0:
                continue
            r, c = r2 // 2, c2 // 2
            if r2 % 2 == 0:
                cand = [(r, c), (r, c + 1)]
            elif c2 % 2 == 0:
                cand = [(r, c), (r + 1, c)]
            else:
                cand = [(r, c), (r + 1, c), (r, c + 1), (r + 1, c + 1)]
            par = [at[k] for k in cand if k in at]
            if len(par) < (3 if len(cand) == 4 else 2):
                continue
            rows = sorted({k[0] for k in cand})
            cols = sorted({k[1] for k in cand})
            lat = round(sum(row_lat[x] for x in rows) / len(rows), 2)
            lon = round(sum(col_lon[x] for x in cols) / len(cols), 2)
            nbrs = _kernel_nbrs(pts, lay, lat, lon)
            nd = min(len(pts[i]["d"]) for i in par)
            days = []
            for j in range(nd):
                days.append(_blend_day([pts[i]["d"][j] for i in par],
                                       [(pts[i]["d"][j], w) for i, w in nbrs if j < len(pts[i]["d"])], j))
            added.append({"lat": lat, "lon": lon,
                          "tropical": sum(1 for i in par if pts[i].get("tropical")) * 2 >= len(par),
                          "d": days, "interp": True})
            meta.append((lat, lon, [pkey(pts[i]["lat"], pts[i]["lon"]) for i in par],
                         [(pkey(pts[i]["lat"], pts[i]["lon"]), w) for i, w in nbrs]))
    out = dict(raw)
    out["points"] = list(pts) + added
    out["grid_step"] = GRID_STEP
    out_frames = frames
    if frames:
        out_frames = {}
        for dn, fd in frames.items():
            cols = fd.get("cols") or []
            by = {pkey(fp["lat"], fp["lon"]): fp["f"] for fp in fd.get("points", [])}
            extra = []
            if "cat" in cols:
                fine = int(dn) <= FINE_DAYS
                for lat, lon, pkeys, nkeys in meta:
                    if any(k not in by for k in pkeys):
                        continue
                    n = min(len(by[k]) for k in pkeys)
                    extra.append({"lat": lat, "lon": lon, "f": [
                        _blend_frame([by[k][s] for k in pkeys],
                                     [(by[k][s], w) for k, w in nkeys if k in by and s < len(by[k])], cols, fine)
                        for s in range(n)]})
            g = dict(fd)
            g["points"] = list(fd.get("points", [])) + extra
            out_frames[dn] = g
    return out, out_frames


def node_parents(pts):
    """{index: [indices of the model points an added point was blended from]} for a run on the
    finer lattice (empty for a run that is not)."""
    if not pts or not any(p.get("interp") for p in pts):
        return {}
    lat_max = max(p["lat"] for p in pts)
    lon_min = min(p["lon"] for p in pts)
    cell, pos = {}, []
    for i, p in enumerate(pts):
        k = (int(round((lat_max - p["lat"]) / GRID_STEP)), int(round((p["lon"] - lon_min) / GRID_STEP)))
        cell[k] = i
        pos.append(k)
    out = {}
    for i, p in enumerate(pts):
        if not p.get("interp"):
            continue
        r, c = pos[i]
        if r % 2 == 0:
            cand = [(r, c - 1), (r, c + 1)]
        elif c % 2 == 0:
            cand = [(r - 1, c), (r + 1, c)]
        else:
            cand = [(r - 1, c - 1), (r - 1, c + 1), (r + 1, c - 1), (r + 1, c + 1)]
        out[i] = [cell[k] for k in cand if k in cell and not pts[cell[k]].get("interp")]
    return out


# ---------------------------------------------------------------- compose -------------------
def edited_cats(raw, edits):
    """The category grid that will be published for each day, before harmonising.
    Returns (cats[day][point], info[day]) where info says where each day came from."""
    dates = run_dates(raw)
    pts = raw["points"]
    ndays = len(dates)
    raw_cats = [[(p["d"][j]["cat"] if j < len(p["d"]) else 0) for p in pts] for j in range(ndays)]
    cats = [row[:] for row in raw_cats]
    info = []
    by_date = (edits or {}).get("dates") or {}
    idx = {}
    if edits and edits.get("points"):
        idx = {pkey(a, b): i for i, (a, b) in enumerate(edits["points"])}
    parents = node_parents(pts)
    lay = model_layout(pts) if parents else None
    for j, d in enumerate(dates):
        e = by_date.get(d)
        fine_now = j < FINE_DAYS
        if not e:
            info.append({"date": d, "source": "raw", "rebased": False, "changed": 0, "missing": 0})
            continue
        ecat = e.get("cat") or []
        coarse_edit = e.get("scale") == "coarse"
        rebased = coarse_edit and fine_now
        changed = missing = 0
        row = []
        for p in pts:
            k = idx.get(pkey(p["lat"], p["lon"]))
            row.append(int(ecat[k]) if k is not None and k < len(ecat) and ecat[k] is not None else None)
        # an edit made before the finer lattice has no call for the added points: each takes the
        # tier the public map would shade there from the edited model points, the same test that
        # filled the model's own tiers (so an untouched region comes out as the raw run's)
        for i, par in parents.items():
            if row[i] is None and par and all(row[q] is not None for q in par) and lay:
                ladder = COARSE_CAT_LADDER if coarse_edit else LADDER["cat"]
                nb = _kernel_nbrs(pts, lay, pts[i]["lat"], pts[i]["lon"])
                n = max(_rungs([(row[q], w) for q, w in nb if row[q] is not None], ladder),
                        _held([row[q] for q in par], ladder))
                row[i] = ladder[n - 1] if n else 0
        for i, p in enumerate(pts):
            if row[i] is None:
                missing += 1                      # a point the edit didn't cover keeps raw
                continue
            c = row[i]
            if rebased:
                c = rebase_coarse(c, raw_cats[j][i])
            c = max(0, min(4, c))
            cats[j][i] = c
            if c != raw_cats[j][i]:
                changed += 1
        info.append({"date": d, "source": "edit", "rebased": rebased, "changed": changed,
                     "missing": missing})
    return cats, raw_cats, info


def compose(raw, raw_frames=None, edits=None, published_at=None):
    """Returns (outlook, frames, report). raw_frames is {day_number: frame file dict} or None.
    The run is first put on the finer lattice (densify). With no edits the outlook and frames come
    back equal to that run's."""
    raw, raw_frames = densify(raw, raw_frames)
    cats, raw_cats, info = edited_cats(raw, edits)
    out = dict(raw)
    out["dates"] = run_dates(raw)
    new_pts = []
    for i, p in enumerate(raw["points"]):
        d_new = []
        for j, rec in enumerate(p["d"]):
            # on a day he published, every point is his call, changed or not, so every point's
            # summary fields follow his category; unedited days stay exactly as the model has them
            if j < len(info) and info[j]["source"] == "edit":
                d_new.append(harmonise(rec, cats[j][i]))
            else:
                d_new.append(rec)
        q = dict(p)
        q["d"] = d_new
        new_pts.append(q)
    out["points"] = new_pts
    if edits and (edits.get("dates") or {}):
        out["manual"] = {
            "published_at": published_at or edits.get("published_at"),
            "edited_days": [x["source"] == "edit" for x in info],
            "changed_points": [x["changed"] for x in info],
        }
    else:
        out.pop("manual", None)

    frames_out = None
    masked = 0
    if raw_frames:
        # on a day the forecaster published, every point's frames are capped at his category
        # (mask_frame leaves a frame alone when it is already at or under it); days nobody has
        # edited yet keep the model's frames untouched
        cap = {}
        for j in range(len(cats)):
            if info[j]["source"] != "edit":
                continue
            for i, p in enumerate(raw["points"]):
                cap[(j + 1, pkey(p["lat"], p["lon"]))] = cats[j][i]
        frames_out = {}
        for dn, fd in raw_frames.items():
            cols = fd.get("cols") or []
            fpts = []
            for fp in fd.get("points", []):
                c = cap.get((int(dn), pkey(fp["lat"], fp["lon"])))
                if c is None:
                    fpts.append(fp)
                    continue
                nf = [mask_frame(v, cols, c) for v in fp["f"]]
                masked += sum(1 for a, b in zip(nf, fp["f"]) if a != b)
                q = dict(fp)
                q["f"] = nf
                fpts.append(q)
            g = dict(fd)
            g["points"] = fpts
            frames_out[dn] = g
    report = {"days": info, "frames_masked": masked}
    return out, frames_out, report


# ---------------------------------------------------------------- files ---------------------
def read_json(path):
    with open(path) as f:
        return json.load(f)


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)


def load_frames(frame_dir):
    """{day_number: frame file} for d1..d8 that exist, or None when there are none."""
    fr = {}
    for n in range(1, 9):
        p = os.path.join(frame_dir, "d%d.json" % n)
        if os.path.exists(p):
            fr[n] = read_json(p)
    return fr or None


def write_frames(frame_dir, frames):
    for n, fd in frames.items():
        write_json_atomic(os.path.join(frame_dir, "d%d.json" % int(n)), fd)


def summary_line(report):
    parts = []
    for j, x in enumerate(report["days"]):
        tag = "raw" if x["source"] == "raw" else ("edit*" if x["rebased"] else "edit")
        parts.append("d%d %s %s%s" % (j + 1, x["date"][5:], tag,
                                      (" (%d changed)" % x["changed"]) if x["source"] == "edit" else ""))
    return "; ".join(parts) + "; %d frames capped" % report["frames_masked"]


def latest_edits(repo="."):
    """The newest published edits: from origin/main when it can be fetched (so a publish made while
    the nightly run was building is not lost), else the checked-out file, else None."""
    try:
        subprocess.run(["git", "-C", repo, "fetch", "-q", "--depth=1", "origin", "main"],
                       check=True, timeout=60, capture_output=True)
        r = subprocess.run(["git", "-C", repo, "show", "FETCH_HEAD:" + EDITS_PATH],
                           timeout=30, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception as e:  # network or git trouble: fall back to the working tree
        print("could not read edits from origin/main (%s); using the checked-out file" % e)
    p = os.path.join(repo, EDITS_PATH)
    return read_json(p) if os.path.exists(p) else None


def pipeline_main():
    """Nightly carry-forward: raw files (written by build_outlook.R) + newest edits -> the
    published outlook and frames. Only ever writes complete files, via atomic renames."""
    raw = read_json(os.path.join(RAW_DIR, "outlook.json"))
    raw_frames = load_frames(os.path.join(RAW_DIR, "frames"))
    edits = latest_edits(".")
    if not edits:
        print("manual outlook: no published edits yet -- publishing the raw run unchanged")
    out, frames, report = compose(raw, raw_frames, edits)
    write_json_atomic(OUTLOOK_PATH, out)
    if frames:
        write_frames(FRAME_DIR, frames)
    print("manual outlook carried forward: " + summary_line(report))


if __name__ == "__main__":
    if "--pipeline" in sys.argv:
        pipeline_main()
    else:
        print(__doc__)
