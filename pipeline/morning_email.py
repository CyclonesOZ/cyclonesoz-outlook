"""Morning severe-weather email: where a Front Line alert (SWPA) might be needed in the next 3 days.

Josh, 19 Nov 2026: "every morning to EMAIL me at 4am where severe weather for an SWPA could occur
within the next 3 days ... severe storms, heavy rain, winds etc. Brief headline (severe storms
possible in western NSW) before a few details about what the conditions are, a good prompt for the
Alert writer on the cyclonesoz post alert system."

Then, on the first one: "this is a joke, way too long. can we make a localhost map that shows me
where the severe weather is expected?" and "email gives me the details, plus a link to the local
host map which is zoomable". So each item is five lines, and the email opens with a link to the map
(severemap/, served on Josh's Mac) that carries the areas after the '#' (map_link).

Runs inside the alert scan (alert-scan.yml), straight after the 3:52 am AWST model run, so it lands
between about 4 and 4:45 am Perth time. It takes the scan's events for model days 1-3, merges the
same hazard in the same area across those days into one item, ranks the items, and writes for each
a headline, when / tier / where, the conditions, one line for the Post Alert writer (alert type,
area, valid times in AWST, which the page's date boxes take) and notes to paste into "Your notes
for Claude". The notes leave out index names (SCP, DCAPE...) because the writer copies the notes'
vocabulary into member text.

Sends through Resend when RESEND_API_KEY is set, with an Idempotency-Key on the AWST date, so a
second model run on the same day cannot send a second email. Stdlib only. It never raises into the
scan: alert_scan.py calls it inside a try.

    python3 pipeline/alert_scan.py --morning-only            # print today's email from the latest run
    python3 pipeline/alert_scan.py --morning-only --send     # ... and send it (test key, not the daily one)
"""
import base64
import datetime as dt
import html
import json
import math
import os
import re
import statistics
import urllib.error
import urllib.request
import zlib
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOWNS = os.path.join(ROOT, "data", "towns.json")
POSTCODES = os.path.join(ROOT, "data", "postcodes", "postcodes.json")
PERTH = ZoneInfo("Australia/Perth")
TO = os.environ.get("MORNING_TO") or "josh@cyclonesoz.com.au"
FROM = "CyclonesOZ Alert Scan <josh@cyclonesoz.com.au>"  # cyclonesoz.com.au is verified in Resend
# The map server Josh runs on his Mac (severemap/install.sh). The link only works on that Mac.
MAP_URL = os.environ.get("MORNING_MAP_URL") or "http://localhost:8419/"

DAYS = 3
MERGE_DEG = 1.5      # the same hazard within ~150 km, any of the 3 days, is the same item
MAX_SPAN_DEG = 6.0   # ...unless that would make the item wider than ~600 km
MAX_ITEMS = 10
PC_RADIUS_DEG = 0.45  # postcodes "near" an item: centre within ~50 km of a firing point (proposals.py)
TIER = {1: "Minor", 2: "Moderate", 3: "Significant", 4: "Major"}

# Rule id prefix -> hazard family. Families are what an SWPA is issued for.
FAMILY = {"storms": "storms", "hail": "storms", "wind": "storms", "tor": "storms",
          "rain": "rain", "rain3": "rain", "rainf": "rain", "gust": "wind", "fire": "fire", "heat": "heat",
          "frost": "frost", "snow": "snow", "tc": "tc"}
ORDER = ["tc", "storms", "rain", "wind", "snow", "heat", "fire", "frost"]
HEAD = {"storms": "Severe storms possible in", "rain": "Heavy rain possible in",
        "wind": "Damaging winds possible in", "fire": "Dangerous fire weather in",
        "heat": "Heatwave possible in", "frost": "Frost possible in", "snow": "Heavy snow possible in",
        "tc": "Tropical cyclone risk for"}
# The Post Alert page's hazard list (src/pages/PostAlert.jsx HAZARDS).
WRITER_HAZARD = {"rain": "Heavy rainfall", "wind": "Severe weather (fronts, damaging winds)",
                 "fire": "Bushfire", "heat": "Heatwave", "frost": "Frost",
                 "snow": "Blizzards / heavy snow", "tc": "Tropical cyclone"}
STATE_NAME = {"TAS": "Tasmania", "ACT": "the ACT"}
CAT = {0: "nothing", 1: "TSTM", 2: "MRGL", 3: "MDT", 4: "HIGH"}
# Headlines name areas by BoM forecast district (docs/districts.geo.json), which are the regions
# people already use. A compass on the state's map box called Cairns "north-west QLD", so the
# compass is only the fallback for points outside every district. Districts not listed here are
# used as they are, with "the" where it reads better.
AREA = {
    ("NSW", "Northern Rivers"): "the Northern Rivers", ("NSW", "Mid North Coast"): "the Mid North Coast",
    ("NSW", "Hunter"): "the Hunter", ("NSW", "Northern Tablelands"): "the Northern Tablelands",
    ("NSW", "Metropolitan"): "Sydney", ("NSW", "Illawarra"): "the Illawarra", ("NSW", "South Coast"): "the NSW South Coast",
    ("NSW", "Central Tablelands"): "the Central Tablelands", ("NSW", "Southern Tablelands"): "the Southern Tablelands",
    ("NSW", "Snowy Mountains"): "the Snowy Mountains", ("NSW", "North West Slopes & Plains"): "north-west NSW",
    ("NSW", "Central West Slopes & Plains"): "central west NSW", ("NSW", "South West Slopes"): "the NSW South West Slopes",
    ("NSW", "Riverina"): "the Riverina", ("NSW", "Lower Western"): "western NSW", ("NSW", "Upper Western"): "western NSW",
    ("NT", "Daly"): "the Top End", ("NT", "Tiwi"): "the Tiwi Islands", ("NT", "Arnhem"): "Arnhem Land",
    ("NT", "Carpentaria"): "the NT Gulf coast", ("NT", "Gregory"): "the Katherine region", ("NT", "Barkly"): "the Barkly",
    ("NT", "Simpson"): "Central Australia", ("NT", "Lasseter"): "south-west NT", ("NT", "Tanami"): "the Tanami",
    ("QLD", "Peninsula"): "Cape York", ("QLD", "Gulf Country"): "the Gulf Country",
    ("QLD", "Northern Goldfields and Upper Flinders"): "inland north QLD",
    ("QLD", "North Tropical Coast and Tablelands"): "Far North QLD", ("QLD", "Herbert and Lower Burdekin"): "the Townsville region",
    ("QLD", "Central Coast and Whitsundays"): "the Mackay and Whitsunday coast", ("QLD", "Capricornia"): "Capricornia",
    ("QLD", "Central Highlands and Coalfields"): "the Central Highlands", ("QLD", "Central West"): "central west QLD",
    ("QLD", "North West"): "north-west QLD", ("QLD", "Channel Country"): "the Channel Country",
    ("QLD", "Maranoa and Warrego"): "the Maranoa and Warrego", ("QLD", "Darling Downs and Granite Belt"): "the Darling Downs",
    ("QLD", "Wide Bay and Burnett"): "Wide Bay and Burnett", ("QLD", "Southeast Coast"): "SE QLD",
    ("SA", "Adelaide Metropolitan"): "Adelaide", ("SA", "Upper South East"): "the SA South East",
    ("SA", "Lower South East"): "the SA South East", ("SA", "Riverland"): "the Riverland", ("SA", "Murraylands"): "the Murraylands",
    ("SA", "Mid North"): "the SA Mid North", ("SA", "Flinders"): "the Flinders Ranges", ("SA", "West Coast"): "the SA West Coast",
    ("SA", "Eastern Eyre Peninsula"): "Eyre Peninsula", ("SA", "Lower Eyre Peninsula"): "Eyre Peninsula",
    ("SA", "North West Pastoral"): "north-west SA", ("SA", "North East Pastoral"): "north-east SA",
    ("SA", "Mount Lofty Ranges"): "the Adelaide Hills",
    ("TAS", "Furneaux Islands"): "the Furneaux Islands", ("TAS", "North East"): "north-east Tasmania",
    ("TAS", "East Coast"): "the Tasmanian east coast", ("TAS", "Central North"): "central north Tasmania",
    ("TAS", "Midlands"): "the Tasmanian Midlands", ("TAS", "South East"): "south-east Tasmania",
    ("TAS", "Upper Derwent Valley"): "the Upper Derwent Valley", ("TAS", "Central Plateau"): "the Central Plateau",
    ("TAS", "Western"): "western Tasmania", ("TAS", "North West Coast"): "the Tasmanian north-west coast",
    ("VIC", "Mallee"): "the Mallee", ("VIC", "Wimmera"): "the Wimmera", ("VIC", "Northern Country"): "northern VIC",
    ("VIC", "North East"): "north-east VIC", ("VIC", "Central"): "Melbourne and central VIC",
    ("VIC", "North Central"): "north-central VIC", ("VIC", "South West"): "south-west VIC",
    ("WA", "Kimberley"): "the Kimberley", ("WA", "Pilbara"): "the Pilbara", ("WA", "Gascoyne"): "the Gascoyne",
    ("WA", "Goldfields"): "the Goldfields", ("WA", "Eucla"): "the Eucla", ("WA", "North Interior"): "the WA interior",
    ("WA", "South Interior"): "the WA interior", ("WA", "Central West"): "the Mid West", ("WA", "Lower West"): "Perth and the Lower West",
    ("WA", "South West"): "the WA South West", ("WA", "South Coastal"): "the WA south coast",
    ("WA", "South East Coastal"): "the Esperance coast", ("WA", "Great Southern"): "the Great Southern",
    ("WA", "Central Wheat Belt"): "the Wheatbelt",
}


# --------------------------------------------------------------------------- geography

def km(a, b):
    dy = (a[0] - b[0]) * 111.2
    dx = (a[1] - b[1]) * 111.2 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def key_ll(k):
    lat, lon = k.split(",")
    return float(lat), float(lon)


class Places:
    """Towns, states and state bounds, for naming an area the way a forecaster would."""

    def __init__(self, districts, towns_path=TOWNS, postcodes_path=POSTCODES):
        self.districts = districts
        self.towns = json.load(open(towns_path))["towns"] if os.path.exists(towns_path) else []
        pcs = json.load(open(postcodes_path)) if os.path.exists(postcodes_path) else {}
        self.pc = [(v["lat"], v["lon"], v["state"]) for v in pcs.values()
                   if v.get("lat") is not None and v.get("state")]
        self.pc_area = [(v["lat"], v["lon"]) for v in pcs.values() if v.get("lat") is not None and v.get("area")]
        self.bounds = {}
        for lat, lon, st in self.pc:
            b = self.bounds.setdefault(st, [lat, lon, lat, lon])
            b[0], b[1], b[2], b[3] = min(b[0], lat), min(b[1], lon), max(b[2], lat), max(b[3], lon)
        self._state = {}

    def district(self, lat, lon):
        """(district, state) at a point; (None, nearest postcode's state) offshore."""
        k = (lat, lon)
        if k not in self._state:
            hit = (None, None)
            for f in self.districts.features:
                if _in_geometry(lon, lat, f["geometry"]):
                    hit = (f["properties"]["name"], f["properties"].get("state"))
                    break
            if not hit[1] and self.pc:
                hit = (None, min(self.pc, key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2)[2])
            self._state[k] = hit
        return self._state[k]

    def state(self, lat, lon):
        return self.district(lat, lon)[1]

    def area_name(self, pts):
        """'the Channel Country and north-east SA', 'western NSW', 'the Top End'."""
        counts = {}
        for p in pts:
            d = self.district(*p)
            if d[0]:
                counts[d] = counts.get(d, 0) + 1
        if counts and sum(counts.values()) >= 0.5 * len(pts):
            ranked = sorted(counts.items(), key=lambda kv: -kv[1])
            names = []
            for i, ((name, st), n) in enumerate(ranked):
                if i and n < 0.2 * len(pts):
                    break
                label = AREA.get((st, name), name)
                if label not in names:
                    names.append(label)
                if len(names) == 3:
                    break
            return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return self.compass_name(pts)

    def compass_name(self, pts):
        """Fallback for points outside every district: 'south-west QLD and north-east SA'."""
        by = {}
        for lat, lon in pts:
            by.setdefault(self.state(lat, lon) or "?", []).append((lat, lon))
        total = sum(len(v) for v in by.values())
        ranked = sorted(by.items(), key=lambda kv: -len(kv[1]))
        keep = [kv for i, kv in enumerate(ranked) if i == 0 or len(kv[1]) >= 0.25 * total][:3]
        names = [self._part(st, sp) for st, sp in keep]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    def _part(self, st, pts):
        b = self.bounds.get(st)
        name = STATE_NAME.get(st, st)
        if not b or st == "ACT":
            return name
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        y = (lat - b[0]) / max(0.01, b[2] - b[0])
        x = (lon - b[1]) / max(0.01, b[3] - b[1])
        ns = "north" if y > 0.67 else "south" if y < 0.33 else ""
        ew = "east" if x > 0.67 else "west" if x < 0.33 else ""
        if st == "NT" and ns == "north":
            return "the Top End"
        if ns and ew:
            return "%s-%s %s" % (ns, ew, name)
        word = {"north": "northern", "south": "southern", "east": "eastern", "west": "western"}.get(ns or ew, "central")
        return "%s %s" % (word, name)

    def towns_near(self, ranked_pts, n=4):
        """Towns beside the strongest points: a town (tier 1-3) within 80 km, else a locality
        within 60 km. Strongest point first."""
        out = []
        for p in ranked_pts:
            best = None
            for name, lat, lon, tier in self.towns:
                d = km(p, (lat, lon))
                if (tier <= 3 and d <= 80) or d <= 60:
                    rank = (0 if tier <= 3 else 1, d)
                    if best is None or rank < best[0]:
                        best = (rank, name)
            if best and best[1] not in out:
                out.append(best[1])
            if len(out) >= n:
                break
        return out

    def postcodes_near(self, pts):
        """How many postcode areas sit within ~50 km of the item: a rough count of who it touches."""
        r2 = PC_RADIUS_DEG ** 2
        return sum(1 for lat, lon in self.pc_area if any(
            (lat - a) ** 2 + ((lon - b) * math.cos(math.radians(lat))) ** 2 <= r2 for a, b in pts))

    def nearest(self, p):
        """'Yulara, 150 km' for a remote point: the nearest town of tier 1-3."""
        best = min(((km(p, (lat, lon)), name) for name, lat, lon, tier in self.towns if tier <= 3), default=None)
        return "%s, %d km" % (best[1], r5(best[0])) if best else None


def _in_geometry(x, y, geom):
    from alert_scan import in_geometry  # one point-in-polygon in the pipeline
    return in_geometry(x, y, geom)


# --------------------------------------------------------------------------- items

def family_level(ev, fam, levels):
    return max(levels[r] for r in ev["rules"] if FAMILY.get(r.split("_")[0]) == fam)


def build_items(events, th, places=None):
    """Merge the scan's day 1-3 events into one item per hazard family per area."""
    levels = {r["id"]: r["level"] for r in th["rules"]}
    parts = []
    for ev in events:
        if ev["lead"] > DAYS:
            continue
        for fam in sorted({FAMILY.get(r.split("_")[0]) for r in ev["rules"]} - {None}):
            parts.append({"fam": fam, "ev": ev, "level": family_level(ev, fam, levels),
                          "pts": [key_ll(k) for k in ev["points"]]})
    # Greedy, strongest first. A part joins a group of its family when it lies within MERGE_DEG
    # of it and the group would still fit in MAX_SPAN_DEG. Plain linkage chained the north's
    # storm clusters from the Top End to Birdsville into one "area" on 19 Nov.
    parts.sort(key=lambda p: (-p["level"], -len(p["pts"])))
    groups = []
    for p in parts:
        for g in groups:
            if g[0]["fam"] != p["fam"]:
                continue
            allp = [q for x in g for q in x["pts"]] + p["pts"]
            span = max(max(q[0] for q in allp) - min(q[0] for q in allp), max(q[1] for q in allp) - min(q[1] for q in allp))
            if span <= MAX_SPAN_DEG and any(abs(a[0] - b[0]) <= MERGE_DEG and abs(a[1] - b[1]) <= MERGE_DEG
                                            for x in g for a in x["pts"] for b in p["pts"]):
                g.append(p)
                break
        else:
            groups.append([p])
    items = []
    for ps in groups:
        evs = [p["ev"] for p in ps]
        pts = sorted({pt for p in ps for pt in p["pts"]})
        rules = sorted({r for e in evs for r in e["rules"] if FAMILY.get(r.split("_")[0]) == ps[0]["fam"]},
                       key=lambda r: -levels[r])
        items.append({
            "fam": ps[0]["fam"], "level": max(p["level"] for p in ps), "events": evs, "pts": pts,
            "dates": sorted({e["date"] for e in evs}), "rules": rules,
            "first_seen": min((e.get("first_seen") or "9") for e in evs),
            "pub_cat": max((e.get("published_cat_max", -1) for e in evs), default=-1),
            "n_pc": places.postcodes_near(pts) if places else 0,
        })
    # Tier first (Josh's scheme), then the item that touches the most postcodes.
    items.sort(key=lambda it: (-it["level"], -it["n_pc"], ORDER.index(it["fam"]), -len(it["pts"])))
    return items


def is_main(it):
    """Worth an alert on its own: Moderate or above, or Minor that is more than one spot on one day."""
    return it["level"] >= 2 or len(it["pts"]) >= 3 or len(it["dates"]) >= 2


# --------------------------------------------------------------------------- facts

def point_days(it, run):
    """(lat, lon, day_index, record) for every point-day in the item."""
    idx = {"%.2f,%.2f" % (p["lat"], p["lon"]): p for p in run["points"]}
    di_of = {d: i for i, d in enumerate(run["dates"])}
    out = []
    for ev in it["events"]:
        di = di_of[ev["date"]]
        for k in ev["points"]:
            p = idx.get(k)
            if p and di < len(p["d"]):
                out.append((p["lat"], p["lon"], di, p["d"][di]))
    return out


def rain72(run, lat, lon):
    for p in run["points"]:
        if p["lat"] == lat and p["lon"] == lon:
            return sum((p["d"][i].get("rain") or 0) for i in range(min(DAYS, len(p["d"]))))
    return 0


def strength(fam, r):
    if fam == "storms":
        return (r.get("cape") or 0) * (r.get("shear") or 0) * (1 + (r.get("wind") or 0) + (r.get("hail") or 0))
    return {"rain": r.get("rain"), "wind": r.get("gust"), "fire": r.get("ffdi"), "heat": r.get("heat"),
            "frost": -(r.get("tmin") or 0), "snow": r.get("snow72"), "tc": r.get("tc_cat")}.get(fam) or 0


def r5(x):
    return int(5 * round(float(x) / 5))


def r100(x):
    return int(100 * round(float(x) / 100))


def day_name(date):
    return dt.date.fromisoformat(date).strftime("%A")


def days_text(dates):
    ds = [dt.date.fromisoformat(d) for d in dates]
    if len(ds) == 1:
        return ds[0].strftime("%a %-d %b")
    if (ds[-1] - ds[0]).days == len(ds) - 1:
        return "%s to %s" % (ds[0].strftime("%a %-d"), ds[-1].strftime("%a %-d %b"))
    return " and ".join(d.strftime("%a %-d") for d in ds) + ds[-1].strftime(" %b")


def days_words(dates):
    names = [day_name(d) for d in dates]
    if len(names) == 1:
        return names[0]
    ds = [dt.date.fromisoformat(d) for d in dates]
    if len(ds) > 2 and (ds[-1] - ds[0]).days == len(ds) - 1:
        return "%s to %s" % (names[0], names[-1])
    return ", ".join(names[:-1]) + " and " + names[-1]


def describe(it, run, places, labels):
    """(headline, facts line, conditions line, writer line, notes to paste into the writer)."""
    fam, pds = it["fam"], point_days(it, run)
    ranked = sorted(pds, key=lambda x: -strength(fam, x[3]))
    pts = [(lat, lon) for lat, lon, _, _ in pds]
    area = places.area_name(pts)
    towns = places.towns_near([(lat, lon) for lat, lon, _, _ in ranked])
    districts = []
    for e in it["events"]:
        for d in e.get("districts", []):
            if d not in districts:
                districts.append(d)
    peak = lambda f: max(((r.get(f) or 0), di) for _, _, di, r in pds) if pds else (0, 0)
    hazards = [labels[r] for r in it["rules"]]

    head = "%s %s" % (HEAD[fam], area)
    if fam == "fire" and any(r.startswith("fire_") and r != "fire_minor" for r in it["rules"]):
        head = "Extreme fire weather in %s" % area
    if fam == "rain" and all(r.startswith("rain3") for r in it["rules"]):
        head = "Persistent heavy rain possible in %s" % area
    if fam == "frost" and any(r != "frost_minor" for r in it["rules"]):
        head = "Severe frost possible in %s" % area

    nearest = places.nearest(ranked[0][:2]) if ranked else None
    seen = it["first_seen"]
    facts = [days_text(it["dates"]), TIER[it["level"]],
             "near " + ", ".join(towns[:3]) if towns else "remote, nearest town " + nearest if nearest else "remote",
             "flagged since %s" % _run_local(seen).strftime("%a %-d") if seen and seen < run["run_date"] else "new"]
    when = days_words(it["dates"])
    place = ", ".join(districts[:3]) or area
    around = ", around " + ", ".join(towns) if towns else ""
    general = lambda hz: "Writer: General severe weather · %s · %s. Paint: %s%s." % (hz, TIER[it["level"]], place, around)
    # Notes are facts only. The writer treats them as authoritative and picks the "What to do" points
    # from Josh's own list, in his voice, which never gives bare orders ("never drive through
    # floodwater"): advice written here would be copied over it.
    notes = []

    if fam == "storms":
        cape, shear = peak("cape")[0], peak("shear")[0]
        wind, hail, stp = peak("wind")[0], peak("hail")[0], peak("stp")[0]
        rain = peak("rain")[0]
        threats = []
        if wind >= 3:
            threats.append("destructive winds 125+ km/h (isolated 160+)")
        elif wind >= 2:
            threats.append("destructive winds 125+ km/h")
        elif wind >= 1:
            threats.append("damaging winds 90+ km/h")
        if hail >= 3:
            threats.append("very large hail 6 cm+")
        elif hail >= 2:
            threats.append("large hail 3-6 cm")
        if stp >= 1:
            threats.append("tornado risk")
        said = ", ".join(threats) or "severe storms (outlook category MDT+)"
        cond = "%s. CAPE %s J/kg, shear %d kt.%s" % (
            said[:1].upper() + said[1:], format(r100(cape), ","), shear,
            "" if it["pub_cat"] < 0 else " Not in your outlook." if it["pub_cat"] == 0 else " Your outlook: %s." % CAT[it["pub_cat"]])
        inner = [(lat, lon) for lat, lon, _, r in ranked
                 if (r.get("cat") or 0) >= 4 or (r.get("wind") or 0) >= 2 or (r.get("hail") or 0) >= 3 or (r.get("stp") or 0) >= 2]
        inner_towns = places.towns_near(inner, n=3) if inner else []
        it["inner"] = set(inner)
        writer = "Writer: Severe thunderstorms · %s. Outer: %s%s." % (TIER[it["level"]], place, around)
        if inner:
            writer += " Inner: around %s." % (", ".join(inner_towns) or "the strongest points")
        said = []  # the same threats in member words: the writer copies the notes' vocabulary
        if wind >= 3:
            said.append("destructive wind gusts of 125 km/h or more, possibly up to 160 km/h")
        elif wind >= 2:
            said.append("destructive wind gusts of 125 km/h or more")
        elif wind >= 1:
            said.append("damaging wind gusts of 90 km/h or more")
        if hail >= 3:
            said.append("very large hail, 6 cm or more")
        elif hail >= 2:
            said.append("large hail, 3 to 6 cm")
        if stp >= 1:
            said.append("a brief tornado can't be ruled out")
        notes.append("Severe thunderstorms possible %s over %s." % (when, area))
        if said:
            notes.append("Main threat: %s with the strongest storms." % said[0])
        if len(said) > 1:
            notes.append("Also possible: %s." % " and ".join(said[1:]))
        notes.append("Very unstable air (CAPE around %s J/kg) with %s shear (about %d kt), so storms that form can organise%s."
                     % (format(r100(cape), ","), "strong" if shear >= 40 else "moderate" if shear >= 25 else "weak", r5(shear),
                        ", with some supercells" if shear >= 35 and cape >= 1500 else ""))
        notes.append("Rain is secondary: up to about %d mm under storms." % r5(rain) if rain >= 5 else "Little rain with them.")
    elif fam == "rain" and run.get("fine"):
        # patch_run's numbers: ECMWF rain over each point's box, the month's normal, and the moisture
        totals = sorted(rain72(run, lat, lon) for lat, lon in sorted(set(pts)))
        day_peak, day_i = peak("rain")
        pw = max((r.get("pw") or 0) for _, _, _, r in pds)
        thr = statistics.median([r.get("pw_thr") or 0 for _, _, _, r in pds])
        normals = [r.get("normal") for _, _, _, r in pds if r.get("normal")]
        month = dt.date.fromisoformat(it["dates"][0]).strftime("%B")
        pct = "" if not normals else ", about %d%% of the %s average" % (
            r5(100 * max(totals) / statistics.median(normals)), month)
        cond = "Up to %d mm over 3 days%s. Heaviest %s, up to %d mm. Precipitable water up to %d mm (heavy-rain level here %d)." % (
            r5(max(totals)), pct, day_name(run["dates"][day_i]), r5(day_peak), round(pw), round(thr))
        writer = general("Heavy rainfall")
        notes.append("Heavy rain possible %s over %s." % (when, area))
        notes.append("Up to about %d mm over three days in the heaviest spots%s. Heaviest on %s, up to %d mm that day."
                     % (r5(max(totals)), pct, day_name(run["dates"][day_i]), r5(day_peak)))
        notes.append("The air is very moist for this time of year, so falls can be heavy where rain sets in.")
    elif fam == "rain":
        totals = sorted(rain72(run, lat, lon) for lat, lon in sorted(set(pts)))
        lo, hi = (statistics.quantiles(totals, n=4)[0], statistics.quantiles(totals, n=4)[2]) if len(totals) >= 4 else (min(totals), max(totals))
        day_peak, day_i = peak("rain")
        cond = "Mostly %d-%d mm over 3 days, up to %d mm. Heaviest %s, up to %d mm." % (
            r5(lo), r5(hi), r5(max(totals)), day_name(run["dates"][day_i]), r5(day_peak))
        writer = general("Heavy rainfall")
        notes.append("Heavy rain possible %s over %s." % (when, area))
        notes.append("Three-day totals mostly %d to %d mm, up to about %d mm in the heaviest spots. Heaviest on %s, up to %d mm that day."
                     % (r5(lo), r5(hi), r5(max(totals)), day_name(run["dates"][day_i]), r5(day_peak)))
    elif fam == "wind":
        g, gi = peak("gust")
        n90 = len({(lat, lon) for lat, lon, _, r in pds if (r.get("gust") or 0) >= 90})
        cond = "Gusts up to %d km/h, strongest %s; 90+ km/h at %d model points." % (r5(g), day_name(run["dates"][gi]), n90)
        writer = general("Severe weather (fronts, damaging winds)")
        notes.append("Damaging wind gusts possible %s over %s, up to about %d km/h, strongest on %s." % (when, area, r5(g), day_name(run["dates"][gi])))
    elif fam == "fire":
        f, fi = peak("ffdi")
        cond = "FFDI up to %d on %s." % (f, day_name(run["dates"][fi]))
        writer = general("Bushfire")
        notes.append("Hot, dry and windy: dangerous fire weather %s over %s%s." % (
            when, area, ", worst on %s" % day_name(run["dates"][fi]) if len(it["dates"]) > 1 else ""))
    elif fam == "heat":
        cond = "%s." % "; ".join(hazards)
        writer = general("Heatwave")
        notes.append("Heatwave conditions %s over %s: days well above normal with warm nights (%s)." % (when, area, hazards[0]))
    elif fam == "frost":
        t = min((r.get("tmin") for _, _, _, r in pds if r.get("tmin") is not None), default=None)
        cond = "Minimums down to about %s C." % ("%.0f" % t if t is not None else "?")
        writer = general("Frost")
        notes.append("Frost likely %s over %s, minimums down to about %s C." % (when, area, "%.0f" % t if t is not None else "zero"))
    elif fam == "snow":
        s = peak("snow72")[0]
        cond = "Up to %d cm of snow over 3 days." % r5(s)
        writer = general("Blizzards / heavy snow")
        notes.append("Heavy snow possible %s over %s, up to about %d cm over three days." % (when, area, r5(s)))
    else:  # tc
        c, ci = peak("tc_cat")
        cond = "%s. Peak category %d in range." % ("; ".join(hazards), c)
        writer = general("Tropical cyclone (or Tropical low)")
        notes.append("A tropical system may affect %s %s. Check the Cyclone Centre track before writing." % (area, when))

    writer += " %s to %s AWST." % (_awst(it["dates"][0], 0), _awst(it["dates"][-1], 1))
    it["district"] = districts[0] if districts else ""
    it["notes"] = " ".join(notes)
    return head, " · ".join(facts), cond, writer, it["notes"]


def _clock(t):
    return t.strftime("%-I:%M %p" if t.minute else "%-I %p").lower()


def _run_local(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PERTH)


def _awst(date, end):
    """A model day is 00Z-24Z, 8 am to 8 am AWST. AWST is what the Post Alert date boxes take (Josh's browser)."""
    t = (dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=end)).astimezone(PERTH)
    return "%s %s" % (t.strftime("%a %-d %b"), _clock(t))


# --------------------------------------------------------------------------- links

# The scan's hazard families as the Post Alert page's hazards (src/pages/PostAlert.jsx HAZARDS).
PAGE_HAZARD = {"storms": "severe_thunderstorms", "rain": "heavy_rainfall", "wind": "severe_weather", "fire": "bushfire",
               "heat": "heatwave", "frost": "frost", "snow": "snow", "tc": "tropical_cyclone"}
POST_ALERT_URL = "https://cyclonesoz.com.au/post-alert"


def _pack(obj):
    """base64url(raw deflate(JSON)), what the map and the Post Alert page read after the '#'.

    A browser never sends what follows '#' to a server, and neither page stores it anywhere public,
    so no possible-alert area is published by a link. This repo and its Actions logs are public."""
    z = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.urlsafe_b64encode(z.compress(json.dumps(obj, separators=(",", ":")).encode()) + z.flush()).decode().rstrip("=")


def _grid_step(run):
    lats = sorted({p["lat"] for p in run["points"]})
    return round(min((b - a for a, b in zip(lats, lats[1:]) if b - a > 0.01), default=0.72), 3)


def _day_bits(run, it):
    """{"lat,lon": bit per model day 1-3 the point fired on}."""
    di_of = {d: i for i, d in enumerate(run["dates"][:DAYS])}
    bits = {}
    for ev in it["events"]:
        di = di_of.get(ev["date"])
        if di is not None:
            for k in ev["points"]:
                bits[k] = bits.get(k, 0) | 1 << di
    return bits


def alert_seed(run, it, head, key):
    """The item as a draft for the Post Alert page (cyclonesoz repo, src/lib/alertSeed.js, which
    checks every field). Josh, 19 Nov: "a button that takes the drafted alert over to the post alerts
    page". key, with the model run, lets the page reopen the draft instead of adding it twice.

    v2 (20 Nov) carries the drawn outlines (fine_areas.outline) instead of the outlook's boxes:
    outer and inner as packed rings, hundredths of a degree, lon/lat, first point then steps."""
    t0 = dt.datetime.fromisoformat(it["dates"][0]).replace(tzinfo=dt.timezone.utc)
    t1 = dt.datetime.fromisoformat(it["dates"][-1]).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
    seed = {"key": "scan:%s:%s" % (run["run_date"], key),
            "type": "thunderstorm" if it["fam"] == "storms" else "general", "hazard": PAGE_HAZARD[it["fam"]],
            "tier": TIER[it["level"]].lower(), "title": head, "notes": it.get("notes", ""),
            "from": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "until": t1.strftime("%Y-%m-%dT%H:%M:%SZ")}
    shape = it.get("shape")
    if shape:
        seed.update({"v": 2, "outer": shape["outer"], "inner": shape["inner"]})
        if it["fam"] != "storms" and shape["inner"] and shape.get("inner_tier"):
            seed["inner_tier"] = shape["inner_tier"]
        return seed
    inner = it.get("inner") or set()
    seed.update({"v": 1, "step": _grid_step(run),
                 "cells": [list(key_ll(k)) + [1 if key_ll(k) in inner else 0] for k in sorted(_day_bits(run, it))]})
    return seed


def post_alert_link(seed):
    return POST_ALERT_URL + "#seed=1." + _pack(seed)


def map_link(run, entries):
    """Link to the zoomable map (severemap/) on Josh's Mac, with the areas after the '#'.

    entries: (number in the email or 0, item, headline, Post Alert seed). Format, read by
    severemap/index.html: "#1." + _pack({v, run, days, step, i: [[n, family, level, headline, when,
    [[lat, lon, day bits, inner]...], seed without v/step/cells]...]}). The map rebuilds each seed for
    its "Add to Post Alert drafts" button from the cells.

    "#2." when the items carry drawn outlines: i: [[n, family, level, headline, when, day bits,
    outer, inner, seed without v/outer/inner]...], plus "model" (which ECMWF run the shapes and the
    overlays come from, e.g. "ifs-0p25/2026111912").
    """
    if all(sd.get("v") == 2 for _, _, _, sd in entries):
        out = []
        for n, it, head, seed in entries:
            bits = 0
            for d in it["dates"]:
                if d in run["dates"][:DAYS]:
                    bits |= 1 << run["dates"].index(d)
            out.append([n, it["fam"], it["level"], head, days_text(it["dates"]), bits, seed["outer"], seed["inner"],
                        {k: v for k, v in seed.items() if k not in ("v", "outer", "inner")}])
        run_local = _run_local(run["run_date"])
        return MAP_URL + "#2." + _pack({
            "v": 2, "run": "%s %s AWST" % (run_local.strftime("%a %-d %b"), _clock(run_local)),
            "days": [dt.date.fromisoformat(d).strftime("%a %-d") for d in run["dates"][:DAYS]],
            "dates": run["dates"][:DAYS], "model": run.get("fine_key") or "", "i": out})
    out = []
    for n, it, head, seed in entries:
        inner = {tuple(c[:2]) for c in seed["cells"] if c[2]}
        out.append([n, it["fam"], it["level"], head, days_text(it["dates"]),
                    [list(key_ll(k)) + [b, 1 if key_ll(k) in inner else 0] for k, b in sorted(_day_bits(run, it).items())],
                    {k: v for k, v in seed.items() if k not in ("v", "step", "cells")}])
    run_local = _run_local(run["run_date"])
    return MAP_URL + "#1." + _pack({
        "v": 1, "run": "%s %s AWST" % (run_local.strftime("%a %-d %b"), _clock(run_local)),
        "days": [dt.date.fromisoformat(d).strftime("%a %-d") for d in run["dates"][:DAYS]],
        "step": _grid_step(run), "i": out})


# --------------------------------------------------------------------------- email

def _near_metro(pts):
    import fine_areas
    return next((m for m in (fine_areas.near_metro(*p) for p in pts) if m), None)


def settle_tiny(items, fine, run):
    """Josh, 20 Nov: "those tiny little one or two pixel zones either need to be automatically merged
    into a neighbouring bigger severe weather zone or hidden from the map unless situated within a
    major metro centre". Tiny: one or two outlook points, unless the fine grid shows a real area
    (10,000 km2+) there. Returns (items kept, number hidden)."""
    import fine_areas as F
    step = _grid_step(run)
    for it in items:
        it["support"] = F.item_support(it, fine, step) if fine else None
        it["tiny"] = len(it["pts"]) <= 2 and not (it["support"] and it["support"]["km2"] >= 2 * F.TINY_ITEM_KM2)
    kept, hidden = [], 0
    big = [it for it in items if not it["tiny"]]
    for it in items:
        if not it["tiny"]:
            kept.append(it)
            continue
        host = min((b for b in big if b["fam"] == it["fam"]), default=None,
                   key=lambda b: min(math.hypot(a[0] - c[0], a[1] - c[1]) for a in b["pts"] for c in it["pts"]))
        if host and min(math.hypot(a[0] - c[0], a[1] - c[1]) for a in host["pts"] for c in it["pts"]) <= F.TINY_MERGE_DEG:
            host["events"] += it["events"]
            host["pts"] = sorted(set(host["pts"]) | set(it["pts"]))
            host["dates"] = sorted(set(host["dates"]) | set(it["dates"]))
            host["level"] = max(host["level"], it["level"])
            host["rules"] = host["rules"] + [r for r in it["rules"] if r not in host["rules"]]
            host["first_seen"] = min(host["first_seen"], it["first_seen"])
            if host["support"] and it["support"]:
                host["support"] = {"cells": host["support"]["cells"] + it["support"]["cells"],
                                   "inner": host["support"]["inner"] + it["support"]["inner"],
                                   "res": host["support"]["res"], "km2": host["support"]["km2"] + it["support"]["km2"]}
        elif _near_metro(it["pts"]):
            kept.append(it)
        else:
            hidden += 1
    return kept, hidden


def shape_item(it, run):
    """Drawn outlines for an item, packed for the links (fine_areas.outline)."""
    import fine_areas as F
    o = F.outline(it, it.get("support"), _grid_step(run))
    shape = {"outer": F.pack_rings(o["outer"]), "inner": F.pack_rings(o["inner"]), "fine": o["fine"]}
    if it["fam"] != "storms" and it["level"] > 1:
        shape["inner_tier"] = TIER[it["level"]].lower()
    return shape


def compose(run, th, events, districts, data_notes=(), fine=None):
    labels = {r["id"]: r["label"] for r in th["rules"]}
    places = Places(districts)
    items = build_items(events, th, places)
    hidden = 0
    try:  # outlines need numpy/scipy/contourpy; without them the email falls back to the boxes
        import fine_areas  # noqa: F401
        drawn = True
    except Exception as e:
        drawn = False
        data_notes = list(data_notes) + ["outlines SKIPPED (%s)" % e]
    if drawn:
        items, hidden = settle_tiny(items, fine, run)
        items.sort(key=lambda it: (-it["level"], -it["n_pc"], ORDER.index(it["fam"]), -len(it["pts"])))
    worth = [it for it in items if is_main(it)]
    main, rest = worth[:MAX_ITEMS], worth[MAX_ITEMS:] + [it for it in items if not is_main(it)]
    run_local = _run_local(run["run_date"])
    first, last = run["dates"][0], run["dates"][min(DAYS, len(run["dates"])) - 1]
    described = [(it,) + describe(it, run, places, labels) for it in main]
    seen = {}
    for d in described:
        seen[d[1]] = seen.get(d[1], 0) + 1
    heads = [head + (" (%s)" % it["district"] if seen[head] > 1 and it["district"] else "")  # two "north-west QLD": say which
             for it, head, *_ in described]
    others = [(0, it, describe(it, run, places, labels)[0]) for it in rest]
    if drawn:
        for it in main + rest:
            it["shape"] = shape_item(it, run)
    seeds = [alert_seed(run, d[0], h, n) for n, (d, h) in enumerate(zip(described, heads), 1)]
    others = [(0, it, h, alert_seed(run, it, h, "x%d" % k)) for k, (_, it, h) in enumerate(others, 1)]

    body = ["3-day severe watch, %s to %s. Model run %s %s AWST." % (
        days_text([first]), days_text([last]), run_local.strftime("%a %-d %b"), _clock(run_local))]
    if items:
        body.append("Map: " + map_link(run, [(n, d[0], h, sd) for n, (d, h, sd) in enumerate(zip(described, heads, seeds), 1)] + others))
    body.append("")
    for n, ((it, _, facts, cond, writer, notes), head, seed) in enumerate(zip(described, heads, seeds), 1):
        body += ["%d. %s" % (n, head), "   " + facts, "   " + cond, "   " + writer, "   Notes: " + notes,
                 "   Post Alert: " + post_alert_link(seed), ""]
    if not main:
        body += ["Nothing in the next 3 days reaches an SWPA threshold.", ""]
    if rest:
        body += ["Also on the map: %d smaller area%s, shown lighter with dashed edges." % (len(rest), "" if len(rest) == 1 else "s"), ""]
    if hidden:
        body += ["Hidden: %d speck%s of one or two model points, away from the capitals and big cities." % (hidden, "" if hidden == 1 else "s"), ""]
    body.append("Model days run 8 am to 8 am AWST. Automatic detection from the raw model run, not a forecast. Nothing has gone to members.")
    notes = [n for n in data_notes if n]
    if notes:
        body.append("Data: " + " · ".join(notes))
    if heads:
        more = len(heads) - 1 + len(rest)
        subject = "3-day severe watch: %s%s" % (heads[0], " (+%d more)" % more if more else "")
    else:
        subject = "3-day severe watch: nothing at SWPA level"
    return subject, "\n".join(body)


def to_html(text):
    """The same email for mail apps: the map and Post Alert links as links, headlines in bold, indents kept."""
    out = []
    for line in text.split("\n"):
        if line.startswith("Map: "):
            out.append('<div style="margin:6px 0">Map: <a href="%s">open the zoomable map</a> (on your Mac)</div>'
                       % html.escape(line[5:], quote=True))
        elif line.startswith("   Post Alert: "):
            out.append('<div style="margin-left:18px"><a href="%s">Add to Post Alert drafts</a></div>'
                       % html.escape(line[15:], quote=True))
        elif not line:
            out.append("<div>&nbsp;</div>")
        elif re.match(r"\d+\. ", line):
            out.append("<div><b>%s</b></div>" % html.escape(line))
        else:
            indent = len(line) - len(line.lstrip(" "))
            out.append('<div style="margin-left:%dpx">%s</div>' % (indent * 6, html.escape(line.strip())))
    return ('<div style="font:14px/1.45 -apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;color:#1d232b">'
            + "\n".join(out) + "</div>")


def send(subject, text, key):
    api = os.environ.get("RESEND_API_KEY")
    if not api:
        return "not sent: RESEND_API_KEY is not set"
    req = urllib.request.Request(
        "https://api.resend.com/emails", method="POST",
        data=json.dumps({"from": FROM, "to": [TO], "subject": subject, "text": text, "html": to_html(text)}).encode(),
        # Resend sits behind Cloudflare, which refuses urllib's default user agent.
        headers={"Authorization": "Bearer " + api, "Content-Type": "application/json",
                 "Idempotency-Key": key, "User-Agent": "cyclonesoz-outlook-alert-scan/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return "sent (%s): %s" % (key, r.read().decode()[:200])
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        if e.code == 409:  # same key already used today, by this payload or another
            return "already sent today (%s): %s" % (key, detail)
        raise RuntimeError("Resend %d: %s" % (e.code, detail))


def daily_key(now=None):
    return "morning-severe/%s" % (now or dt.datetime.now(PERTH)).astimezone(PERTH).date().isoformat()
