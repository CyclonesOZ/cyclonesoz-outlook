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
    (thunderstorm chance, hail, wind), which are brought into line with it so a summary can never
    say "Chance of thunderstorms" where the map shows no risk.

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
    """Brings the fields a member's summary reads into line with an edited category, using the
    pipeline's own rules: thunderstorm chance floors, hail no higher than the category, no
    damaging wind below Marginal and nothing above Damaging below Moderate."""
    out = dict(rec)
    out["raw_cat"] = rec.get("cat", 0)
    out["cat"] = cat
    out["conditional"] = False
    tp = rec.get("tprob", 0) or 0
    if cat <= 0:
        out["tprob"] = min(tp, NO_STORM_TPROB)
        out["hail"] = 0
        out["wind"] = 0
        out["hatch"] = 0
        return out
    out["tprob"] = tprob_floor(tp, cat)
    out["hail"] = min(rec.get("hail", 0) or 0, cat)
    wind = rec.get("wind", 0) or 0
    if cat < 2:
        wind = 0
        out["hatch"] = 0
    elif cat < 3:
        wind = min(wind, 1)
    out["wind"] = wind
    return out


def mask_frame(vals, cols, cap):
    """Caps one 3-hourly frame at the forecaster's category for that day."""
    v = list(vals)
    ci = {c: i for i, c in enumerate(cols)}
    if "cat" not in ci or v[ci["cat"]] <= cap:
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
        for i, p in enumerate(pts):
            k = idx.get(pkey(p["lat"], p["lon"]))
            if k is None or k >= len(ecat) or ecat[k] is None:
                missing += 1                      # a point the edit didn't cover keeps raw
                continue
            c = int(ecat[k])
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
    With no edits the outlook and frames come back equal to the raw ones."""
    cats, raw_cats, info = edited_cats(raw, edits)
    out = dict(raw)
    out["dates"] = run_dates(raw)
    new_pts = []
    for i, p in enumerate(raw["points"]):
        d_new = []
        for j, rec in enumerate(p["d"]):
            c = cats[j][i] if j < len(cats) else rec.get("cat", 0)
            d_new.append(harmonise(rec, c) if c != rec.get("cat", 0) else rec)
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
