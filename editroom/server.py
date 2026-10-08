#!/usr/bin/env python3
"""CyclonesOZ outlook edit room: the local server behind http://localhost:8417/

Runs on Josh's Mac (LaunchAgent au.com.cyclonesoz.edit-room, so the bookmark always works). It
serves the editor (editor.html), hands it the fresh raw model run and the last published edit, and
builds the draft preview and the published files with pipeline/manual_compose.py -- the same code
the nightly pipeline uses, so what the editor previews is what gets published.

It works from its own clone of the outlook repo (~/.cyclonesoz/edit-room/repo), refreshed from
GitHub whenever the editor loads, so it never touches a working copy anyone is editing.

LIVE (from 8 Oct 2026): "Publish" checks the outlook, commits it to main from the managed clone
(docs/outlook.json, the frames, the edit itself in docs/archive/manual/edits.json and today's archive
copy) and pushes, using the Mac's existing GitHub login; the editor then watches the public site
until it serves the new file. That one file feeds the website, Front Line, Broadcast, the app map
and member summaries. Creating ~/.cyclonesoz/edit-room/SANDBOX switches back to sandbox mode, where
Publish only writes to ~/.cyclonesoz/edit-room/sandbox/ (viewable at /sandbox/).

Only listens on 127.0.0.1, checks the Host header (so another website can't reach it through DNS
tricks) and needs a custom header on every POST (so another website can't post to it from a
browser). Standard library only: macOS's bundled python3 (3.9) runs it.
"""
import datetime as dt
import http.server
import importlib
import json
import os
import posixpath
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse

PORT = 8417
HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".cyclonesoz", "edit-room")
MANAGED_CLONE = os.path.join(BASE, "repo")
SANDBOX = os.path.join(BASE, "sandbox")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                     # the repo this server file lives in
DOCS = os.path.join(REPO, "docs")
LOG = os.path.join(BASE, "server.log")
SYNC_EVERY_S = 60
LIVE_URL = "https://cyclonesoz.github.io/cyclonesoz-outlook/"
AUTHOR = ("Josh Toohey", "josh@cyclonesoz.com.au")


def mode():
    return "sandbox" if os.path.exists(os.path.join(BASE, "SANDBOX")) else "live"
ALLOWED_HOSTS = {"localhost:%d" % PORT, "127.0.0.1:%d" % PORT}
STARTED_MTIME = os.path.getmtime(os.path.abspath(__file__))

_lock = threading.Lock()
_last_sync = 0.0
_preview = {}          # published path -> bytes, for the draft preview
_patch_notes = []


def log(msg):
    os.makedirs(BASE, exist_ok=True)
    line = "%s %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(LOG, "a") as f:
        f.write(line)


def mc():
    """pipeline/manual_compose.py from this repo, reloaded so a fresh pull is picked up."""
    p = os.path.join(REPO, "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)
    import manual_compose
    return importlib.reload(manual_compose)


def awst_today():
    return (dt.datetime.utcnow() + dt.timedelta(hours=8)).date().isoformat()


# ---------------------------------------------------------------- repo ----------------------
def sync_repo(force=False):
    """Brings the managed clone up to date with GitHub. Never touches any other checkout."""
    global _last_sync
    if os.path.realpath(REPO) != os.path.realpath(MANAGED_CLONE):
        return "development checkout (not synced)"
    if not force and time.time() - _last_sync < SYNC_EVERY_S:
        return "synced %ds ago" % int(time.time() - _last_sync)
    try:
        subprocess.run(["git", "-C", REPO, "fetch", "-q", "--depth=1", "origin", "main"],
                       check=True, timeout=90, capture_output=True)
        subprocess.run(["git", "-C", REPO, "reset", "-q", "--hard", "FETCH_HEAD"],
                       check=True, timeout=60, capture_output=True)
        _last_sync = time.time()
        return "synced"
    except Exception as e:
        log("sync failed: %s" % e)
        return "could not reach GitHub (%s) -- showing the last copy" % e.__class__.__name__


def raw_sources():
    """The raw model run and its frames (docs/archive/raw, written by every run since 8 Oct 2026).
    docs/outlook.json is the PUBLISHED outlook now, so it is never used as a stand-in."""
    rd = os.path.join(DOCS, "archive", "raw")
    if not os.path.exists(os.path.join(rd, "outlook.json")):
        raise RuntimeError("the raw model run (docs/archive/raw/outlook.json) is missing")
    return os.path.join(rd, "outlook.json"), os.path.join(rd, "frames"), "raw"


def current_edits():
    """The last published edit: the live one, or the sandbox one in sandbox mode."""
    p = os.path.join(SANDBOX, "edits.json") if mode() == "sandbox" else \
        os.path.join(REPO, "docs", "archive", "manual", "edits.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def git(*args, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "-C", REPO] + list(args), check=True, timeout=timeout,
                          capture_output=True, text=True, env=env)


def live_published_at():
    """published_at of the outlook the public site is serving right now (curl, so macOS's own
    certificates are used), or None."""
    try:
        r = subprocess.run(["curl", "-sf", "--max-time", "20", LIVE_URL + "outlook.json?_=%d" % time.time()],
                           capture_output=True, timeout=30)
        return (json.loads(r.stdout).get("manual") or {}).get("published_at") if r.returncode == 0 else None
    except Exception:
        return None


# ---------------------------------------------------------------- review flags --------------
def _sharp(a, b, coarse, m):
    """Two categories far enough apart to matter: either side of the severe line (MRGL), or two
    or more categories apart; on days 5-8, a different coarse class."""
    if coarse:
        return m.coarse_class(a) != m.coarse_class(b)
    return ((a >= 2) != (b >= 2)) or abs(a - b) >= 2


def previous_raw(m, edits, raw):
    """{date: [category per current point]} from the model run the last edit was made on, read from
    the raw archive (docs/archive/raw/<day-1 date>.json), or None if it isn't there."""
    base = (edits or {}).get("base_run")
    if not base or base == raw.get("run_date"):
        return None
    try:
        d0 = (dt.datetime.strptime(base, "%Y-%m-%dT%H:%M:%SZ") + dt.timedelta(hours=8)).date()
    except ValueError:
        return None
    for d in (d0, d0 - dt.timedelta(days=1)):
        pth = os.path.join(DOCS, "archive", "raw", d.isoformat() + ".json")
        if not os.path.exists(pth):
            continue
        old = m.read_json(pth)
        if old.get("run_date") != base:
            continue
        idx = {m.pkey(p["lat"], p["lon"]): p for p in old["points"]}
        out = {}
        for j, dd in enumerate(m.run_dates(old)):
            out[dd] = [(idx[m.pkey(p["lat"], p["lon"])]["d"][j]["cat"] if m.pkey(p["lat"], p["lon"]) in idx else None)
                       for p in raw["points"]]
        return out
    return None


def review_flags(m, raw, edits, cats, raw_cats, info):
    """Per day, the points to look at this morning: where the NEW model run disagrees sharply with
    the carried edit AND has changed its mind since the run that edit was made on. A square where
    the forecaster deliberately overrode the model, and the model still says the same, isn't
    flagged again. With no newer run than the edit, nothing is flagged."""
    if not edits or edits.get("base_run") == raw.get("run_date"):
        return [[] for _ in info]
    prev = previous_raw(m, edits, raw)
    dates = m.run_dates(raw)
    flags = []
    for j, inf in enumerate(info):
        f = []
        if inf["source"] == "edit":
            coarse = j >= m.FINE_DAYS
            old = (prev or {}).get(dates[j])
            for i in range(len(raw["points"])):
                a, b = cats[j][i], raw_cats[j][i]
                if not _sharp(a, b, coarse, m):
                    continue
                if old is not None and old[i] is not None and not _sharp(old[i], b, coarse, m):
                    continue
                f.append(i)
        flags.append(f)
    return flags


# ---------------------------------------------------------------- checks --------------------
SUMMARY_CAT = [None, "TSTM", "MRGL", "MDT", "HIGH"]


def summary_for(rec):
    """The app's member summary for one day (base44/functions/localBrief dayRisk): category from
    cat, the storm line from thunderstorm chance."""
    tp = rec.get("tprob") or 0
    storm = "likely" if tp >= 80 else ("chance" if tp >= 20 else None)
    return SUMMARY_CAT[min(4, int(rec.get("cat") or 0))], storm


def run_checks(out, raw, frames):
    """Blocking problems and warnings for a composed outlook. Publishing live with a stale day 1 is
    blocked: the app reads the first day as "today", so members would get yesterday's risk."""
    problems, warnings = [], []
    if len(out["points"]) != len(raw["points"]):
        problems.append("%d points in the outlook, %d in the model run" % (len(out["points"]), len(raw["points"])))
    nd = len(raw["days"])
    short = sum(1 for p in out["points"] if len(p["d"]) != nd)
    if short:
        problems.append("%d points are missing days" % short)
    dates = out.get("dates") or []
    if dates and dates[0] != awst_today():
        msg = "Day 1 is %s, not today (%s). The overnight model run hasn't landed yet." % (dates[0], awst_today())
        (problems if mode() == "live" else warnings).append(msg)
    # every member's summary (days 1-2, what localBrief reads) must agree with the map
    bad = []
    for p in out["points"]:
        for j in range(min(2, len(p["d"]))):
            r = p["d"][j]
            cat, storm = summary_for(r)
            c = int(r.get("cat") or 0)
            if (c >= 1 and storm is None) or (c == 0 and storm is not None) or \
               (r.get("hail") or 0) > c or (c < 2 and (r.get("wind") or 0) > 0):
                bad.append("%.2f,%.2f day %d" % (p["lat"], p["lon"], j + 1))
    if bad:
        problems.append("%d member summaries would disagree with the map: %s" % (len(bad), ", ".join(bad[:8]) + (" ..." if len(bad) > 8 else "")))
    return problems, warnings


# ---------------------------------------------------------------- compose -------------------
def build(edit_dates, published_at=None):
    """Composes the raw run with the editor's grids. Returns (out, frames, report, raw)."""
    m = mc()
    raw_path, frames_dir, _ = raw_sources()
    raw = m.read_json(raw_path)
    frames = m.load_frames(frames_dir)
    edits = {"version": 1, "points": [[p["lat"], p["lon"]] for p in raw["points"]],
             "base_run": raw.get("run_date"), "published_at": published_at, "dates": edit_dates}
    out, fo, rep = m.compose(raw, frames, edits, published_at=published_at)
    return out, fo, rep, raw, edits


def sandbox_carry_forward():
    """What the nightly pipeline will do once manual mode is live, done for the sandbox: when a
    newer model run has landed than the sandbox outlook was built on, lay the last sandbox publish
    over it (passed day dropped, new day 8 from the model)."""
    edits = current_edits()
    sp = os.path.join(SANDBOX, "outlook.json")
    if not edits or not os.path.exists(sp):
        return
    m = mc()
    raw_path, frames_dir, _ = raw_sources()
    raw = m.read_json(raw_path)
    with open(sp) as f:
        if json.load(f).get("run_date") == raw.get("run_date"):
            return
    with _lock:
        out, fo, rep = m.compose(raw, m.load_frames(frames_dir), edits)
        m.write_json_atomic(sp, out)
        if fo:
            m.write_frames(os.path.join(SANDBOX, "frames"), fo)
    log("sandbox carried forward onto %s: %s" % (raw.get("run_date"), m.summary_line(rep)))


def dumps(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False).encode()


# ---------------------------------------------------------------- viewer patches ------------
PATCHES = [
    # open on the Daily view and on the editor's day, not the 3-hourly default
    ("var viewMode='hourly'", "var viewMode=(window.__ER_VIEW||'hourly')", "start view"),
    ("selDay=0,LAND=null", "selDay=(window.__ER_DAY||0),LAND=null", "start day"),
    # hand the drawn shapes to the editor, tagged with their tier, for the smoothed-away check
    ("out.push({geo:clipped,style:", "out.push({t:sp.t,geo:clipped,style:", "tier tags"),
    ("featureCache[key]=out;", "featureCache[key]=out; try{(window.__ERF=window.__ERF||{})[key]=out;}catch(e){}", "shape hand-off"),
]
TAIL_ANCHOR = "\n})();\n</script>"
TAIL_HOOK = ("\n  window.__ERshowDay=function(d){ selDay=d; if(viewMode!=='daily') setView('daily'); "
             "buildDayStrip(); showDay(d); };"
             "\n  window.__ERsetView=function(v){ setView(v); };")
# the preview pane is narrow: drop the public page's sidebar (its controls are the editor's job
# there) so the two map panels get the width. Layout only -- the drawing code is untouched.
HEAD_CSS = "<style>:root{--sbw:0px!important}#topbar{display:none!important}</style>\n</head>"
HEAD_HOOK = ("<head>\n<script>(function(){var q=new URLSearchParams(location.search);"
             "window.__ER_VIEW=q.get('view')||'daily';window.__ER_DAY=+(q.get('day')||0);})();</script>")


def patched_viewer():
    """The public page, with small hooks for the preview. Its drawing code is untouched."""
    global _patch_notes
    with open(os.path.join(DOCS, "index.html")) as f:
        html = f.read()
    notes = []
    for old, new, name in PATCHES:
        if html.count(old) == 1:
            html = html.replace(old, new)
        else:
            notes.append(name)
    if html.count(TAIL_ANCHOR) >= 1:
        i = html.rindex(TAIL_ANCHOR)
        html = html[:i] + TAIL_HOOK + html[i:]
    else:
        notes.append("day switching")
    html = html.replace("<head>", HEAD_HOOK, 1)
    html = html.replace("</head>", HEAD_CSS, 1)
    _patch_notes = notes
    return html.encode()


# ---------------------------------------------------------------- HTTP ----------------------
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "EditRoom/1"

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        return (self.headers.get("Host") or "") in ALLOWED_HOSTS

    def _send(self, code, body, ctype="application/json", cache=False):
        if isinstance(body, (dict, list)):
            body = dumps(body)
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype=None):
        if not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        if ctype is None:
            ctype = {".html": "text/html; charset=utf-8", ".json": "application/json",
                     ".js": "text/javascript", ".css": "text/css", ".png": "image/png",
                     ".svg": "image/svg+xml"}.get(os.path.splitext(path)[1], "application/octet-stream")
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _docs_file(self, rel):
        rel = posixpath.normpath(rel).lstrip("/")
        if rel.startswith("..") or "/../" in rel:
            return self._send(400, {"error": "bad path"})
        return self._file(os.path.join(DOCS, rel))

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        url = urllib.parse.urlparse(self.path)
        p = url.path
        q = urllib.parse.parse_qs(url.query)
        try:
            if p in ("/", "/index.html"):
                return self._file(os.path.join(HERE, "editor.html"))
            if p == "/api/state":
                return self._state(force="refresh" in q)
            if p == "/api/live":
                want = (q.get("published_at") or [""])[0]
                got = live_published_at()
                return self._send(200, {"live": bool(want) and got == want, "serving": got})
            if p == "/data/raw.json":
                return self._file(raw_sources()[0])
            if p in ("/data/coastline.geo.json", "/data/states.geo.json"):
                return self._file(os.path.join(DOCS, p.split("/")[-1]))
            if p.startswith("/preview/") or p.startswith("/sandbox/"):
                return self._viewer_file(p)
            return self._send(404, {"error": "not found"})
        except Exception as e:
            log("GET %s failed: %s" % (p, traceback.format_exc()))
            return self._send(500, {"error": str(e)})

    def _viewer_file(self, p):
        kind, rel = p.split("/", 2)[1], (p.split("/", 2)[2] if p.count("/") >= 2 else "")
        if rel in ("", "index.html"):
            if kind == "preview":
                return self._send(200, patched_viewer(), "text/html; charset=utf-8")
            return self._file(os.path.join(DOCS, "index.html"))
        if rel == "archive/index.json":                  # no past-run picker in the preview
            return self._send(200, b"[]")
        raw_path, frames_dir, _ = raw_sources()
        if rel == "archive/frames/index.json":
            return self._file(os.path.join(frames_dir, "index.json"))
        if rel == "outlook.json" or rel.startswith("archive/frames/d"):
            key = "outlook.json" if rel == "outlook.json" else rel.split("/")[-1]
            if kind == "preview":
                if key in _preview:
                    return self._send(200, _preview[key])
            else:
                sandbox_carry_forward()
                sp = os.path.join(SANDBOX, key if key == "outlook.json" else os.path.join("frames", key))
                if os.path.exists(sp):
                    return self._file(sp)
            return self._file(raw_path if key == "outlook.json" else os.path.join(frames_dir, key))
        return self._docs_file(rel)

    def _state(self, force=False):
        with _lock:
            sync_note = sync_repo(force=force)
        m = mc()
        raw_path, _, raw_kind = raw_sources()
        raw = m.read_json(raw_path)
        edits = current_edits()
        cats, raw_cats, info = m.edited_cats(raw, edits)
        review = review_flags(m, raw, edits, cats, raw_cats, info)
        restart = os.path.getmtime(os.path.abspath(__file__)) != STARTED_MTIME
        self._send(200, {
            "mode": mode(),
            "live_url": LIVE_URL,
            "base_run": raw.get("run_date"),
            "model": raw.get("model"),
            "dates": m.run_dates(raw),
            "days": raw.get("days"),
            "today": awst_today(),
            "start": cats,
            "info": info,
            "review": review,
            "edits_published_at": (edits or {}).get("published_at"),
            "edits_base_run": (edits or {}).get("base_run"),
            "raw_source": raw_kind,
            "sync": sync_note,
            "preview_notes": _patch_notes,
        })
        if restart:     # a pull brought a new server.py: restart into it once this reply is out
            log("server.py changed on disk -- restarting")
            threading.Thread(target=lambda: (time.sleep(0.5), os.execv(sys.executable, [sys.executable] + sys.argv)),
                             daemon=True).start()

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Edit-Room") != "1":
            return self._send(403, {"error": "forbidden"})
        p = urllib.parse.urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if p == "/api/preview":
                return self._preview_post(body)
            if p == "/api/publish":
                return self._publish(body)
            return self._send(404, {"error": "not found"})
        except Exception as e:
            log("POST %s failed: %s" % (p, traceback.format_exc()))
            return self._send(500, {"error": str(e)})

    def _check_base(self, body):
        raw_path = raw_sources()[0]
        with open(raw_path) as f:
            run = json.load(f).get("run_date")
        if body.get("base_run") != run:
            self._send(409, {"error": "A newer model run has landed since you opened the editor. "
                                      "Reload to bring your edits onto it.", "run_date": run})
            return False
        return True

    def _preview_post(self, body):
        global _preview
        if not self._check_base(body):
            return
        out, fo, rep, raw, _ = build(body.get("dates") or {})
        pv = {"outlook.json": dumps(out)}
        for n, fd in (fo or {}).items():
            pv["d%d.json" % int(n)] = dumps(fd)
        _preview = pv
        problems, warnings = run_checks(out, raw, fo)
        return self._send(200, {"ok": True, "report": rep, "problems": problems, "warnings": warnings})

    def _publish(self, body):
        if mode() == "live":
            return self._publish_live(body)
        if not self._check_base(body):
            return
        now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        out, fo, rep, raw, edits = build(body.get("dates") or {}, published_at=now)
        problems, warnings = run_checks(out, raw, fo)
        if problems:
            return self._send(422, {"ok": False, "problems": problems, "warnings": warnings})
        m = mc()
        os.makedirs(os.path.join(SANDBOX, "frames"), exist_ok=True)
        m.write_json_atomic(os.path.join(SANDBOX, "outlook.json"), out)
        if fo:
            m.write_frames(os.path.join(SANDBOX, "frames"), fo)
        m.write_json_atomic(os.path.join(SANDBOX, "edits.json"), edits)
        changed = [x["changed"] for x in rep["days"]]
        log("sandbox publish: base %s, changed per day %s" % (raw.get("run_date"), changed))
        return self._send(200, {"ok": True, "mode": "sandbox", "published_at": now, "report": rep,
                                "warnings": warnings, "url": "/sandbox/"})


    def _publish_live(self, body):
        """Commit the outlook to main and push. Starts from a fresh copy of main each attempt, so a
        push that loses a race (e.g. with the nightly run) is rebuilt on top of whatever landed."""
        if os.path.realpath(REPO) != os.path.realpath(MANAGED_CLONE):
            return self._send(400, {"ok": False, "problems": ["Live publishing only runs from the edit room's own copy (%s)." % MANAGED_CLONE]})
        m = mc()
        with _lock:
            last = ""
            for attempt in range(3):
                note = sync_repo(force=True)
                if note != "synced":
                    return self._send(503, {"ok": False, "problems": ["Couldn't reach GitHub to publish: " + note]})
                m = mc()
                with open(raw_sources()[0]) as f:
                    run = json.load(f).get("run_date")
                if body.get("base_run") != run:
                    return self._send(409, {"error": "A newer model run has landed since you opened the editor. "
                                                     "Reload to bring your edits onto it.", "run_date": run})
                now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                out, fo, rep, raw, edits = build(body.get("dates") or {}, published_at=now)
                problems, warnings = run_checks(out, raw, fo)
                if problems:
                    return self._send(422, {"ok": False, "problems": problems, "warnings": warnings})
                day1 = out["dates"][0]
                m.write_json_atomic(os.path.join(DOCS, "outlook.json"), out)
                if fo:
                    m.write_frames(os.path.join(DOCS, "archive", "frames"), fo)
                m.write_json_atomic(os.path.join(DOCS, "archive", "manual", "edits.json"), edits)
                m.write_json_atomic(os.path.join(DOCS, "archive", day1 + ".json"), out)
                changed = [x["changed"] for x in rep["days"]]
                local = (dt.datetime.utcnow() + dt.timedelta(hours=8)).strftime("%-d %b %H:%M AWST")
                try:
                    git("add", "docs/outlook.json", "docs/archive/frames", "docs/archive/manual", "docs/archive/%s.json" % day1)
                    git("-c", "user.name=%s" % AUTHOR[0], "-c", "user.email=%s" % AUTHOR[1], "commit", "-q", "-m",
                        "Publish edited outlook (%s)\n\nPublished from the edit room on model run %s. Squares changed "
                        "from the model by day: %s." % (local, run, ", ".join(str(c) for c in changed)))
                    git("push", "-q", "origin", "HEAD:main", timeout=180)
                except subprocess.CalledProcessError as e:
                    last = (e.stderr or e.stdout or str(e)).strip()
                    log("live publish attempt %d failed: %s" % (attempt + 1, last))
                    continue
                except subprocess.TimeoutExpired:
                    last = "GitHub didn't respond in time"
                    log("live publish attempt %d timed out" % (attempt + 1))
                    continue
                sha = git("rev-parse", "--short", "HEAD").stdout.strip()
                log("LIVE publish %s: base %s, changed per day %s" % (sha, run, changed))
                return self._send(200, {"ok": True, "mode": "live", "published_at": now, "report": rep,
                                        "warnings": warnings, "commit": sha, "url": LIVE_URL})
            return self._send(502, {"ok": False, "problems": ["Couldn't push to GitHub after 3 tries: " + last[-300:]]})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    os.makedirs(SANDBOX, exist_ok=True)
    log("edit room starting on 127.0.0.1:%d from %s" % (PORT, REPO))
    Server(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
