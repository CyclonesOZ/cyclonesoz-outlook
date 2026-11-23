"""Local server for the morning severe-watch map: http://localhost:8419/

Josh, 19 Nov 2026, on the first morning email: "way too long. can we make a localhost map that
shows me where the severe weather is expected?", then "email gives me the details, plus a link to
the local host map which is zoomable".

The page holds no data. The email's link carries the areas after the '#', which a browser never
sends to any server, so nothing about a possible alert is stored or published anywhere new (this
repo is public; see pipeline/morning_email.py map_link). This server only hands out the page and
passes two kinds of request through, so the page can be same-origin:

  /tiles/...   -> https://tiles.cyclonesoz.com/...   our own basemap (Range requests for PMTiles).
                  The tile bucket's CORS only lets the website and two dev ports read it from a
                  browser; a server-side read needs no CORS.
  /vendor/...  -> cdn.jsdelivr.net/npm/...           MapLibre and PMTiles, pinned. MapLibre's
                  worker must be same-origin. Kept on disk after the first fetch.
  /models/...  -> https://models.cyclonesoz.com/...  the model viewer's ECMWF frames, for the map's
                  overlays (PWAT, rain, temperature, wind, shear, CAPE, SCP, STP, SHIP; Josh, 20 Nov:
                  "like what you do on the outlook editor"). Paid fields need our gate key, read
                  from ~/.cyclonesoz/models.env and sent from here only; the page never sees it.
                  Same path allowlist as the Edit room's proxy (editroom/server.py).

  /latest.json -> the newest morning scan this Mac ran itself (severemap/refresh.py, 23 Nov 2026),
                  so the bare address opens on today's areas. /day/<YYYY-MM-DD>.json for earlier days,
                  /days.json lists them. Read from ~/.cyclonesoz/severe-map/data; nothing leaves the Mac.

Listens on 127.0.0.1 only. Installed to run at login by severemap/install.sh.

    python3 severemap/server.py            # port 8419, or SEVERE_MAP_PORT

basemap-style.json is the website's light style, made from the cyclonesoz repo with the tile host
left as a placeholder that the page fills in:

    node --input-type=module -e "import {getBasemapStyle} from './src/lib/basemapStyle.js';
      process.stdout.write(JSON.stringify(getBasemapStyle('light', {origin: '__ORIGIN__'})))"
"""
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request

PORT = int(os.environ.get("SEVERE_MAP_PORT", "8419"))
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "vendor-cache")
TILES = "https://tiles.cyclonesoz.com"
CDN = "https://cdn.jsdelivr.net/npm"
UA = "cyclonesoz-severe-map/1.0"
DATA = os.environ.get("SEVERE_MAP_DATA", os.path.join(os.path.expanduser("~"), ".cyclonesoz", "severe-map", "data"))
DAY_PATH = re.compile(r"^/day/(\d{4}-\d{2}-\d{2})\.json$")
PAGES = {"/": "index.html", "/index.html": "index.html", "/basemap-style.json": "basemap-style.json"}
TYPES = {".html": "text/html; charset=utf-8", ".json": "application/json", ".js": "text/javascript",
         ".mjs": "text/javascript", ".css": "text/css"}
# Only these, so the server is not an open proxy. Versions match the website's package.json.
VENDOR = re.compile(r"^/(maplibre-gl@6\.14\.0/dist/maplibre-gl(-worker)?\.mjs|maplibre-gl@6\.14\.0/dist/maplibre-gl\.css"
                    r"|pmtiles@4\.5\.0/dist/pmtiles\.js)$")
MODELS = "https://models.cyclonesoz.com"
MODEL_PATH = re.compile(r"^/(index\.json|ifs-0p(?:1|25)/\d{10}/(?:manifest\.json|[a-z0-9]+/\d{3}\.png))$")
_frames, _frames_lock, FRAMES_KEEP = {}, threading.Lock(), 600   # path -> (fetched at, bytes)
TILE_PATH = re.compile(r"^/[A-Za-z0-9_\-]+\.pmtiles$|^/fonts/[A-Za-z0-9 ,%_\-]+/\d+-\d+\.pbf$")
PASS = ("Content-Type", "Content-Length", "Content-Range", "Content-Encoding", "Accept-Ranges", "ETag",
        "Last-Modified", "Cache-Control")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path in PAGES:
            return self.file(os.path.join(HERE, PAGES[path]), cache="no-cache")
        if path == "/latest.json":
            return self.file(os.path.join(DATA, "latest.json"), cache="no-store")
        if path == "/days.json":
            try:
                days = sorted((n[:10] for n in os.listdir(DATA) if re.match(r"^\d{4}-\d{2}-\d{2}\.json$", n)), reverse=True)
            except OSError:
                days = []
            return self.json_reply({"days": days})
        m = DAY_PATH.match(path)
        if m:
            return self.file(os.path.join(DATA, m.group(1) + ".json"), cache="no-store")
        if path.startswith("/tiles/") and TILE_PATH.match(path[6:]):
            return self.tiles(path[6:])
        if path.startswith("/vendor/") and VENDOR.match(path[7:]):
            return self.vendor(path[7:])
        if path.startswith("/models/") and MODEL_PATH.match(path[7:]):
            return self.models(path[7:])
        self.send_error(404)

    def models(self, rest):
        is_json = rest.endswith(".json")
        with _frames_lock:
            hit = _frames.get(rest)
        if not hit or (is_json and time.time() - hit[0] > 60):   # frames never change; run lists do
            req = urllib.request.Request(MODELS + rest, headers={"User-Agent": UA})
            key = gate_key()
            if key:
                req.add_header("X-Gate-Key", key)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
            except urllib.error.HTTPError as e:
                return self.send_error(e.code if e.code in (403, 404) else 502, "models: %s" % e.code)
            except (OSError, urllib.error.URLError) as e:
                return self.send_error(502, "models: %s" % e)
            hit = (time.time(), body)
            with _frames_lock:
                if len(_frames) >= FRAMES_KEEP:
                    for k in sorted(_frames, key=lambda k: _frames[k][0])[:FRAMES_KEEP // 4]:
                        del _frames[k]
                _frames[rest] = hit
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if is_json else "image/png")
        self.send_header("Content-Length", str(len(hit[1])))
        self.send_header("Cache-Control", "no-cache" if is_json else "max-age=604800, immutable")
        self.end_headers()
        try:
            self.wfile.write(hit[1])
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json_reply(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def file(self, fn, cache):
        try:
            with open(fn, "rb") as f:
                body = f.read()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(os.path.splitext(fn)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def vendor(self, rest):
        fn = os.path.join(CACHE, rest.lstrip("/").replace("/", "__"))
        if not os.path.exists(fn):
            try:
                req = urllib.request.Request(CDN + rest, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r, open(fn + ".part", "wb") as out:
                    shutil.copyfileobj(r, out)
                os.replace(fn + ".part", fn)
            except (OSError, urllib.error.URLError) as e:
                return self.send_error(502, "CDN: %s" % e)
        self.file(fn, cache="max-age=86400")

    def tiles(self, rest):
        headers = {"User-Agent": UA}
        for h in ("Range", "If-None-Match", "If-Modified-Since"):
            if self.headers.get(h):
                headers[h] = self.headers[h]
        try:
            r = urllib.request.urlopen(urllib.request.Request(TILES + rest, headers=headers), timeout=30)
        except urllib.error.HTTPError as e:
            r = e  # 206/304/404 come back through here too, with their headers
        except urllib.error.URLError as e:
            return self.send_error(502, "tiles: %s" % e.reason)
        with r:
            self.send_response(r.status if hasattr(r, "status") else r.code)
            for h in PASS:
                if r.headers.get(h):
                    self.send_header(h, r.headers[h])
            self.end_headers()
            try:
                shutil.copyfileobj(r, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass  # MapLibre cancels tile requests when the map moves on

    def log_message(self, fmt, *args):
        if args and str(args[1])[:1] in "45":
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def gate_key():
    """Our server key for the model data gate (MODEL_GATE_KEY in ~/.cyclonesoz/models.env)."""
    try:
        with open(os.path.expanduser("~/.cyclonesoz/models.env")) as f:
            for line in f:
                if line.startswith("MODEL_GATE_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    os.makedirs(CACHE, exist_ok=True)
    print("Severe map: http://localhost:%d/" % PORT, flush=True)
    Server(("127.0.0.1", PORT), Handler).serve_forever()
