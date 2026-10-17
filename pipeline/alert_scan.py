#!/usr/bin/env python3
"""Alert scan: check the raw model run against alerts/thresholds.json and tell Josh.

Step 1 of the postcode-alert system. It only DETECTS and EMAILS the forecaster. It publishes
nothing to subscribers and writes nothing the map reads.

    python3 pipeline/alert_scan.py                 # scan the latest raw run, update state, email
    python3 pipeline/alert_scan.py --dry-run       # print the email, touch no state, send nothing
    python3 pipeline/alert_scan.py --backtest      # replay every raw run in the archive, show noise

Reads  docs/archive/raw/outlook.json  (the untouched model run, NOT the published outlook, which
       carries the forecaster's own edits and would mostly alert him to his own forecast).
Writes docs/archive/alerts/state.json (open events + what he was last told) and last_email.txt.

Forecast "days" are 00Z-24Z UTC (see build_outlook.R om_url): 10am-10am in QLD, 11am-11am in
NSW/VIC in daylight saving, 8am-8am in WA. Windows are printed in local time for that reason.

Email goes out over SMTP when ALERT_SMTP_HOST is set (ALERT_SMTP_PORT, ALERT_SMTP_USER,
ALERT_SMTP_PASS, ALERT_FROM, ALERT_TO); otherwise it is only printed and saved. A heartbeat email
is sent every run so silence can never be mistaken for "nothing found".

Standard library only, so it runs before any R package install and cannot break the build.
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import smtplib
import sys
from email.message import EmailMessage
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "docs", "archive", "raw", "outlook.json")
PUBLISHED = os.path.join(ROOT, "docs", "outlook.json")
DISTRICTS = os.path.join(ROOT, "docs", "districts.geo.json")
THRESHOLDS = os.path.join(ROOT, "alerts", "thresholds.json")
STATE_DIR = os.path.join(ROOT, "docs", "archive", "alerts")
STATE = os.path.join(STATE_DIR, "state.json")
LAST_EMAIL = os.path.join(STATE_DIR, "last_email.txt")

CAT_NAMES = {0: "none", 1: "TSTM", 2: "MRGL", 3: "MDT", 4: "HIGH"}
OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
}


# --------------------------------------------------------------------------- rules

def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_run(path):
    """Load a raw run. Runs archived before 8 Oct 2026 have no 'dates'; day 1 is the AWST date in
    the archive file name (the pipeline anchors day 1 to the AWST calendar date)."""
    run = load_json(path)
    if "dates" not in run:
        d0 = dt.date.fromisoformat(os.path.basename(path)[:10])
        run["dates"] = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(len(run["days"]))]
    return run


def rule_fires(rule, rec):
    """True if every condition holds. A missing field never fires (no guessing)."""
    for c in rule["when"]:
        v = rec.get(c["field"])
        if v is None or not OPS[c["op"]](v, c["value"]):
            return False
    return True


def point_key(p):
    return "%.2f,%.2f" % (p["lat"], p["lon"])


def triggered_points(run, th):
    """{day_index: {point_index: [rule ids]}} for the days the scan covers."""
    lo, hi = th["scan_days"]
    out = {}
    for di in range(lo - 1, min(hi, len(run["dates"]))):
        hits = {}
        for pi, p in enumerate(run["points"]):
            if di >= len(p["d"]):
                continue
            rec = p["d"][di]
            ids = [r["id"] for r in th["rules"]
                   if r["days"][0] - 1 <= di <= r["days"][1] - 1 and rule_fires(r, rec)]
            if ids:
                hits[pi] = ids
        if hits:
            out[di] = hits
    return out


# --------------------------------------------------------------------------- geometry

def deg_dist(a, b):
    """Rough distance in degrees, longitude shrunk by latitude."""
    mlat = math.radians((a[0] + b[0]) / 2)
    return math.hypot(a[0] - b[0], (a[1] - b[1]) * math.cos(mlat))


def clusters(points, idxs, neighbour_deg):
    """Connected groups of triggered points that touch (incl. diagonals on the model grid)."""
    idxs = list(idxs)
    seen, groups = set(), []
    for start in idxs:
        if start in seen:
            continue
        seen.add(start)
        stack, group = [start], []
        while stack:
            cur = stack.pop()
            group.append(cur)
            a = (points[cur]["lat"], points[cur]["lon"])
            for o in idxs:
                if o not in seen and deg_dist(a, (points[o]["lat"], points[o]["lon"])) <= neighbour_deg:
                    seen.add(o)
                    stack.append(o)
        groups.append(sorted(group))
    return groups


def in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_geometry(x, y, geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        if in_ring(x, y, poly[0]) and not any(in_ring(x, y, h) for h in poly[1:]):
            return True
    return False


class Districts:
    def __init__(self, path):
        try:
            self.features = load_json(path)["features"]
        except (OSError, ValueError, KeyError):
            self.features = []

    def name_at(self, lat, lon):
        for f in self.features:
            if in_geometry(lon, lat, f["geometry"]):
                return f["properties"]["name"]
        return None

    def names_for(self, pts):
        """Districts covered by the points, most points first."""
        counts = {}
        for lat, lon in pts:
            n = self.name_at(lat, lon)
            if n:
                counts[n] = counts.get(n, 0) + 1
        return [n for n, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def tz_for(lat, lon):
    """Crude state-level zone so windows read in local time. Good enough for a label, not for
    routing; the postcode layer will use real boundaries."""
    if lon < 129:
        return ZoneInfo("Australia/Perth")
    if lat > -26 and lon < 138:
        return ZoneInfo("Australia/Darwin")
    if lon < 141 and lat <= -26:
        return ZoneInfo("Australia/Adelaide")
    if lat > -29 and lon >= 138:
        return ZoneInfo("Australia/Brisbane")
    return ZoneInfo("Australia/Sydney")


def local_window(date_str, lat, lon):
    start = dt.datetime.fromisoformat(date_str).replace(tzinfo=dt.timezone.utc)
    tz = tz_for(lat, lon)
    a, b = start.astimezone(tz), (start + dt.timedelta(days=1)).astimezone(tz)
    return "%s %s to %s %s" % (a.strftime("%a %-d %b %H:%M"), a.tzname(), b.strftime("%a %-d %b %H:%M"), b.tzname())


# --------------------------------------------------------------------------- events

def build_events(run, th, districts, published=None):
    """One event per (date, connected cluster) that satisfies some rule's min_points."""
    pts = run["points"]
    rules = {r["id"]: r for r in th["rules"]}
    pub_idx = {d: i for i, d in enumerate(published["dates"])} if published else {}
    events = []
    for di, hits in sorted(triggered_points(run, th).items()):
        date = run["dates"][di]
        for group in clusters(pts, hits, th["neighbour_deg"]):
            per_rule = {}
            for pi in group:
                for rid in hits[pi]:
                    per_rule.setdefault(rid, []).append(pi)
            live = {rid: m for rid, m in per_rule.items() if len(m) >= rules[rid]["min_points"]}
            if not live:
                continue
            members = sorted({pi for m in live.values() for pi in m})
            recs = [pts[pi]["d"][di] for pi in members]
            lat = sum(pts[pi]["lat"] for pi in members) / len(members)
            lon = sum(pts[pi]["lon"] for pi in members) / len(members)
            ev = {
                "date": date,
                "lead": di + 1,
                "points": [point_key(pts[pi]) for pi in members],
                "n_points": len(members),
                "centroid": [round(lat, 2), round(lon, 2)],
                "bbox": [round(min(pts[pi]["lat"] for pi in members), 2), round(min(pts[pi]["lon"] for pi in members), 2),
                         round(max(pts[pi]["lat"] for pi in members), 2), round(max(pts[pi]["lon"] for pi in members), 2)],
                "rules": sorted(live, key=lambda r: (-rules[r]["level"], r)),
                "level": max(rules[r]["level"] for r in live),
                "districts": districts.names_for([(pts[pi]["lat"], pts[pi]["lon"]) for pi in members])[:6],
                "peak": {f: max(r.get(f, 0) for r in recs) for f in
                         ("cape", "shear", "scp", "ship", "dcape", "rain", "hail", "wind", "cat")},
                "window_local": local_window(date, lat, lon),
            }
            if date in pub_idx:
                j = pub_idx[date]
                ev["published_cat_max"] = max(pts_pub["d"][j].get("cat", 0)
                                              for pts_pub in published["points"]
                                              if point_key(pts_pub) in set(ev["points"]) and j < len(pts_pub["d"]))
            events.append(ev)
    return events


def overlap(a, b):
    sa, sb = set(a["points"]), set(b["points"])
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def match_events(new, old):
    """Pair tonight's events with last scan's, same date, best overlap first. Returns
    (pairs, unmatched_new, unmatched_old)."""
    cand = sorted(((overlap(n, o), i, j) for i, n in enumerate(new) for j, o in enumerate(old)
                   if n["date"] == o["date"]), reverse=True)
    used_n, used_o, pairs = set(), set(), []
    for ov, i, j in cand:
        if ov > 0 and i not in used_n and j not in used_o:
            used_n.add(i)
            used_o.add(j)
            pairs.append((i, j))
    return (pairs, [i for i in range(len(new)) if i not in used_n],
            [j for j in range(len(old)) if j not in used_o])


def classify(ev, told, esc):
    """Compare an event with what the forecaster was last told. None = nothing worth an email."""
    if told is None:
        return "NEW"
    if ev["level"] > told["level"]:
        return "ESCALATED"
    if ev["n_points"] >= told["n_points"] * esc["area_factor"] and ev["n_points"] - told["n_points"] >= esc["area_min_extra_points"]:
        return "ESCALATED"
    if ev["level"] < told["level"] or ev["n_points"] * esc["area_factor"] <= told["n_points"]:
        return "WEAKENED"
    if deg_dist(ev["centroid"], told["centroid"]) >= esc["shift_deg"]:
        return "SHIFTED"
    return None


def told_snapshot(ev):
    return {k: ev[k] for k in ("level", "n_points", "centroid", "rules")}


def scan(run, th, state, districts, published=None):
    """Returns (report, new_state). report = {'changes': [...], 'quiet': [...], 'events': [...]}"""
    events = build_events(run, th, districts, published)
    today = run["dates"][0]
    old = [e for e in state.get("events", []) if e["date"] >= today]
    pairs, fresh, gone = match_events(events, old)
    esc = th["escalation"]
    keep, changes, quiet = [], [], []
    stamp = {e["date"]: 0 for e in old}
    for e in old:
        stamp[e["date"]] += 1

    def new_id(date):
        stamp[date] = stamp.get(date, 0) + 1
        return "%s-%d" % (date.replace("-", ""), stamp[date])

    for i, j in pairs:
        ev, prev = events[i], old[j]
        ev["id"], ev["first_seen"] = prev["id"], prev.get("first_seen", run["run_date"])
        told = prev.get("told")
        status = classify(ev, told, esc)
        if status:
            ev["told"] = told_snapshot(ev)
            ev["prev"] = told
            changes.append((status, ev))
        else:
            ev["told"] = told
            quiet.append(ev)
        keep.append(ev)
    for i in fresh:
        ev = events[i]
        ev["id"], ev["first_seen"] = new_id(ev["date"]), run["run_date"]
        ev["told"] = told_snapshot(ev)
        ev["prev"] = None
        changes.append(("NEW", ev))
        keep.append(ev)
    for j in gone:
        prev = old[j]
        if prev.get("told"):
            changes.append(("CLEARED", prev))
    order = {"NEW": 0, "ESCALATED": 1, "SHIFTED": 2, "WEAKENED": 3, "CLEARED": 4}
    changes.sort(key=lambda c: (order[c[0]], c[1]["date"]))
    new_state = {"run_date": run["run_date"], "events": sorted(keep, key=lambda e: (e["date"], e["id"]))}
    return {"changes": changes, "quiet": quiet, "events": keep}, new_state


# --------------------------------------------------------------------------- email

def fmt_peak(p):
    return "CAPE %d, shear %d kt, SCP %.1f, SHIP %.1f, DCAPE %d, rain up to %.0f mm; hail tier %d, wind tier %d" % (
        p["cape"], p["shear"], p["scp"], p["ship"], p["dcape"], p["rain"], p["hail"], p["wind"])


def describe(status, ev, rule_labels):
    lines = ["%s  %s  (day %d)" % (status, dt.date.fromisoformat(ev["date"]).strftime("%a %-d %b"), ev["lead"])]
    if status == "CLEARED":
        lines.append("   No longer triggered by the latest run. Last told: %d points, %s."
                     % (ev["told"]["n_points"], ", ".join(ev["told"]["rules"])))
        lines.append("   Was in: " + (", ".join(ev["districts"]) or "unnamed district"))
        return "\n".join(lines)
    lines.append("   Where:   " + (", ".join(ev["districts"]) or "no named district") +
                 "  [centre %.1f, %.1f; %d model points]" % (ev["centroid"][0], ev["centroid"][1], ev["n_points"]))
    lines.append("   Window:  %s  (model day is 00Z-24Z)" % ev["window_local"])
    lines.append("   Why:     " + "; ".join(rule_labels.get(r, r) for r in ev["rules"]))
    lines.append("   Peak:    " + fmt_peak(ev["peak"]))
    if "published_cat_max" in ev:
        lines.append("   Your published map: up to %s over these points" % CAT_NAMES.get(ev["published_cat_max"], "?"))
    if ev.get("prev"):
        pv = ev["prev"]
        lines.append("   Was:     %d points, level %d  ->  now %d points, level %d"
                     % (pv["n_points"], pv["level"], ev["n_points"], ev["level"]))
    return "\n".join(lines)


def compose_email(run, th, report, mode_note):
    labels = {r["id"]: r["label"] for r in th["rules"]}
    ch, q = report["changes"], report["quiet"]
    run_local = dt.datetime.fromisoformat(run["run_date"].replace("Z", "+00:00")).astimezone(ZoneInfo("Australia/Perth"))
    head = ("ACTION: %d alert(s) to look at" % len(ch)) if ch else "Scan complete: nothing new"
    subject = "[Front Line] %s (model run %s AWST)" % (head, run_local.strftime("%a %-d %b %H:%M"))
    body = [mode_note, "",
            "Model: %s, second opinion %s. Run %s UTC. Scanned days %d-%d against alerts/thresholds.json."
            % (run["model"], run["second_opinion"], run["run_date"], th["scan_days"][0], th["scan_days"][1]), ""]
    if ch:
        for status, ev in ch:
            body += [describe(status, ev, labels), ""]
    else:
        body += ["No new, escalated, shifted, weakened or cleared events since the last email.", ""]
    if q:
        body.append("Still open, unchanged since you were last told (%d):" % len(q))
        for ev in q:
            body.append("   %s  %s  %d pts  %s" % (ev["id"], ev["date"], ev["n_points"], ", ".join(ev["districts"][:3]) or "-"))
        body.append("")
    body += ["This is an automatic detection from the raw model run. It is not a forecast and not an",
             "official warning. It does not know about your edits to the map."]
    return subject, "\n".join(body)


def send(subject, body):
    host, to = os.environ.get("ALERT_SMTP_HOST"), os.environ.get("ALERT_TO")
    if not host or not to:
        return False
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("ALERT_FROM") or os.environ.get("ALERT_SMTP_USER") or to
    msg.set_content(body)
    port = int(os.environ.get("ALERT_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.starttls()
        if os.environ.get("ALERT_SMTP_USER"):
            s.login(os.environ["ALERT_SMTP_USER"], os.environ.get("ALERT_SMTP_PASS", ""))
        s.send_message(msg)
    return True


# --------------------------------------------------------------------------- modes

def backtest(th, districts):
    """Replay every archived raw run with no memory, to see how noisy the thresholds are."""
    files = sorted(glob.glob(os.path.join(ROOT, "docs", "archive", "raw", "2*.json")))
    print("Backtest: %d raw runs, thresholds %s\n" % (len(files), os.path.relpath(THRESHOLDS, ROOT)))
    print("%-12s %7s  %s" % ("run", "events", "by lead day (1..7)"))
    totals = {r["id"]: 0 for r in th["rules"]}
    n_runs = n_events = 0
    for f in files:
        run = load_run(f)
        evs = build_events(run, th, districts)
        by_lead = [sum(1 for e in evs if e["lead"] == d) for d in range(1, 8)]
        for e in evs:
            for r in e["rules"]:
                totals[r] += 1
        n_runs += 1
        n_events += len(evs)
        print("%-12s %7d  %s" % (os.path.basename(f)[:-5], len(evs), " ".join("%2d" % x for x in by_lead)))
    print("\nMean %.1f events/run if you were re-emailed about everything every night." % (n_events / max(1, n_runs)))
    print("Events by rule (an event can count under several):")
    for rid, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print("   %-18s %d" % (rid, n))
    print("\nThe real email only lists changes (new/escalated/shifted/weakened/cleared), so it will be")
    print("much quieter than this raw count. Use --dry-run on consecutive runs to see that.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the email; no state written, nothing sent")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--run", default=RAW, help="raw outlook file to scan")
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--max-age-hours", type=float, default=14,
                    help="warn instead of scanning when the raw run is older than this")
    args = ap.parse_args()

    th, districts = load_json(THRESHOLDS), Districts(DISTRICTS)
    if args.backtest:
        return backtest(th, districts)

    run = load_run(args.run)
    state = load_json(args.state) if os.path.exists(args.state) else {}
    if state.get("run_date") == run["run_date"] and not args.dry_run:
        print("Run %s already scanned; nothing to do." % run["run_date"])
        return
    age_h = (dt.datetime.now(dt.timezone.utc)
             - dt.datetime.fromisoformat(run["run_date"].replace("Z", "+00:00"))).total_seconds() / 3600
    if age_h > args.max_age_hours:
        # The model run failed or is late. Say so loudly: silence must never look like "all quiet".
        subject = "[Front Line] WARNING: no fresh model run (latest is %.0f h old), alerts NOT checked" % age_h
        body = ("The newest raw model run is %s UTC, %.1f hours old, so tonight's scan did not run.\n"
                "Check the 'Daily storm outlook' workflow. Nothing has been checked against your thresholds."
                % (run["run_date"], age_h))
        print(subject + "\n\n" + body)
        if not args.dry_run:
            try:
                send(subject, body)
            except Exception as e:
                print("[email FAILED: %s]" % e, file=sys.stderr)
        return
    published = load_json(PUBLISHED) if os.path.exists(PUBLISHED) else None
    report, new_state = scan(run, th, state, districts, published)
    live = not args.dry_run
    note = ("TEST (dry run, nothing sent or saved)" if args.dry_run else
            "LIVE scan. Shadow mode: this goes only to you, nothing is published to subscribers.")
    subject, body = compose_email(run, th, report, note)
    print(subject + "\n\n" + body)
    if live:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(args.state, "w") as f:
            json.dump(new_state, f, indent=1)
        with open(LAST_EMAIL, "w") as f:
            f.write(subject + "\n\n" + body + "\n")
        try:
            sent = send(subject, body)
            print("\n[email %s]" % ("sent" if sent else "not configured: saved to last_email.txt only"))
        except Exception as e:  # a mail failure must never fail the build
            print("\n[email FAILED: %s]" % e, file=sys.stderr)


if __name__ == "__main__":
    main()
