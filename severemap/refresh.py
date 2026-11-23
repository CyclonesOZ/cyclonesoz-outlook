"""Runs the morning severe scan on Josh's Mac and keeps the map's newest areas, so
http://localhost:8419/ opens on today's watch by itself and refreshes every day, the way the
Edit room keeps itself current (Josh, 23 Nov 2026: "build it so that it runs like the outlook
editor site, so it refreshes every day").

Nothing new is published. The 4 am email (GitHub Actions) still carries the areas in its link,
because this repo is public. Here the Mac runs the same scan in print-only mode
(`alert_scan.py --morning-only`, no --send: no email, no state written), takes the map link out
of what it prints, and keeps it under ~/.cyclonesoz/severe-map/data, which only server.py on
127.0.0.1 reads.

    python3 severemap/refresh.py            run once (the launch agent runs it at 4:25, 5:15
                                            and 6:45 am, and at login)

It works in its own clone of this repo (~/.cyclonesoz/severe-map/repo), reset to origin/main
before each run, and its own Python (~/.cyclonesoz/severe-map/venv). The model viewer's gate key
comes from ~/.cyclonesoz/models.env and is only passed to the scan's environment.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".cyclonesoz", "severe-map")
REPO = os.path.join(BASE, "repo")
PY = os.path.join(BASE, "venv", "bin", "python")
DATA = os.path.join(BASE, "data")
LOG = os.path.join(BASE, "refresh.log")
ENV_FILE = os.path.join(HOME, ".cyclonesoz", "models.env")
# Upload to the admin-only severe watch on cyclonesoz.com.au (/hq/severe-watch, SevereWatch entity).
# Off until ~/.cyclonesoz/severe-watch.env holds SEVERE_WATCH_SECRET (the same value is a secret on the
# CyclonesOZ Base44 app). The page there decodes the same fragment the email link carries.
UPLOAD_URL = "https://cyclonesoz.com.au/api/apps/69fbea2b450e42a3a7c55ce9/functions/severeWatchUpload"
UPLOAD_ENV = os.path.join(HOME, ".cyclonesoz", "severe-watch.env")
LINK = re.compile(r"http://localhost:8419/(#[12]\.[A-Za-z0-9_-]+)")
KEEP_DAYS = 30
PERTH = dt.timezone(dt.timedelta(hours=8))


def log(msg):
    line = "%s %s" % (dt.datetime.now(PERTH).strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def gate_env():
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR")}
    try:
        for line in open(ENV_FILE):
            k, _, v = line.strip().partition("=")
            if k == "MODEL_GATE_KEY" and v:
                env[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    env["MODEL_CACHE_DIR"] = os.path.join(BASE, "cache")
    os.makedirs(env["MODEL_CACHE_DIR"], exist_ok=True)
    return env


def upload(rec):
    """Send the run to /hq/severe-watch. A failure only logs: the map on this Mac is unaffected."""
    secret = ""
    try:
        for line in open(UPLOAD_ENV):
            k, _, v = line.strip().partition("=")
            if k == "SEVERE_WATCH_SECRET":
                secret = v.strip().strip('"').strip("'")
    except OSError:
        return "upload off (no %s)" % UPLOAD_ENV
    if len(secret) < 32:
        return "upload off (no secret)"
    body = json.dumps({k: rec.get(k) or "" for k in ("generated_at", "subject", "fragment", "note", "repo")}).encode()
    req = urllib.request.Request(UPLOAD_URL, data=body, method="POST", headers={
        "content-type": "application/json", "x-severe-watch": secret, "user-agent": "cyclonesoz-severe-map/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return "uploaded (%s)" % json.loads(r.read() or b"{}").get("id", "ok")
    except urllib.error.HTTPError as e:
        return "upload failed: HTTP %s" % e.code
    except Exception as e:  # network down: try again on the next run
        return "upload failed: %s" % e


def main():
    os.makedirs(DATA, exist_ok=True)
    try:
        subprocess.run(["git", "-C", REPO, "fetch", "-q", "--depth=1", "origin", "main"], check=True, timeout=120)
        subprocess.run(["git", "-C", REPO, "reset", "-q", "--hard", "FETCH_HEAD"], check=True, timeout=60)
    except Exception as e:  # keep going on the copy we have; the scan reads the live model data anyway
        log("git update failed (%s); using the copy on disk" % e)
    head = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()

    env = gate_env()
    if "MODEL_GATE_KEY" not in env:
        log("no MODEL_GATE_KEY in %s: outlines fall back to rings" % ENV_FILE)
    try:
        r = subprocess.run([PY, "-I", "pipeline/alert_scan.py", "--morning-only"], cwd=REPO, env=env,
                           capture_output=True, text=True, timeout=40 * 60)
    except subprocess.TimeoutExpired:
        log("scan timed out after 40 min; keeping the last map")
        return 1
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    if r.returncode != 0:
        log("scan failed (exit %s): %s" % (r.returncode, out.strip().splitlines()[-1:] or ""))
        return 1

    m = LINK.search(out)
    # The printed email starts after a row of '=' with its subject line.
    lines = out.splitlines()
    sep = next((i for i, l in enumerate(lines) if l.startswith("=" * 20)), -1)
    subject = next((l.strip() for l in lines[sep + 1:] if l.strip()), "") if sep >= 0 else ""
    now = dt.datetime.now(PERTH)
    rec = {
        "fragment": m.group(1) if m else None,
        "subject": subject,
        "generated_at": now.isoformat(timespec="seconds"),
        "repo": head,
    }
    if not m:
        rec["note"] = "No severe areas in this run" if "NO fresh model run" not in out else "No fresh model run"
    write_json(os.path.join(DATA, "latest.json"), rec)
    write_json(os.path.join(DATA, now.strftime("%Y-%m-%d") + ".json"), rec)
    for name in sorted(os.listdir(DATA)):
        if re.match(r"^\d{4}-\d{2}-\d{2}\.json$", name) and name[:10] < (now - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d"):
            os.remove(os.path.join(DATA, name))
    log("ok: %s%s; %s" % (subject or "(no subject)", "" if m else " [no map link]", upload(rec)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
