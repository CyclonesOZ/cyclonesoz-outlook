"""Morning severe-weather email: where a Front Line alert (SWPA) might be needed in the next 3 days.

Josh, 19 Nov 2026: "every morning to EMAIL me at 4am where severe weather for an SWPA could occur
within the next 3 days ... severe storms, heavy rain, winds etc. Brief headline (severe storms
possible in western NSW) before a few details about what the conditions are, a good prompt for the
Alert writer on the cyclonesoz post alert system."

Runs inside the alert scan (alert-scan.yml), straight after the 3:52 am AWST model run, so it lands
between about 4 and 4:45 am Perth time. It takes the scan's events for model days 1-3, merges the
same hazard in the same area across those days into one item, ranks the items, and writes for each
a headline, a few lines on the conditions, and a block for the Post Alert writer: alert type, valid
times in AWST (what the page's date boxes take), the area, and notes to paste into "Your notes for
Claude". The notes leave out index names (SCP, DCAPE...) because the writer copies the notes'
vocabulary into member text.

Sends through Resend when RESEND_API_KEY is set, with an Idempotency-Key on the AWST date, so a
second model run on the same day cannot send a second email. Stdlib only. It never raises into the
scan: alert_scan.py calls it inside a try.

    python3 pipeline/alert_scan.py --morning-only            # print today's email from the latest run
    python3 pipeline/alert_scan.py --morning-only --send     # ... and send it (test key, not the daily one)
"""
import datetime as dt
import json
import math
import os
import statistics
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOWNS = os.path.join(ROOT, "data", "towns.json")
POSTCODES = os.path.join(ROOT, "data", "postcodes", "postcodes.json")
PERTH = ZoneInfo("Australia/Perth")
TO = os.environ.get("MORNING_TO") or "josh@cyclonesoz.com.au"
FROM = "CyclonesOZ Alert Scan <josh@cyclonesoz.com.au>"  # cyclonesoz.com.au is verified in Resend

DAYS = 3
MERGE_DEG = 1.5      # the same hazard within ~150 km, any of the 3 days, is the same item
MAX_SPAN_DEG = 6.0   # ...unless that would make the item wider than ~600 km
MAX_ITEMS = 10
PC_RADIUS_DEG = 0.45  # postcodes "near" an item: centre within ~50 km of a firing point (proposals.py)
TIER = {1: "Minor", 2: "Moderate", 3: "Significant", 4: "Major"}

# Rule id prefix -> hazard family. Families are what an SWPA is issued for.
FAMILY = {"storms": "storms", "hail": "storms", "wind": "storms", "tor": "storms",
          "rain": "rain", "rain3": "rain", "gust": "wind", "fire": "fire", "heat": "heat",
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
STATE_TZ = {"WA": "Australia/Perth", "SA": "Australia/Adelaide", "NT": "Australia/Darwin",
            "QLD": "Australia/Brisbane", "NSW": "Australia/Sydney", "ACT": "Australia/Sydney",
            "VIC": "Australia/Melbourne", "TAS": "Australia/Hobart"}
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
    """(headline, detail lines, writer block lines)."""
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
    states = sorted({places.state(*p) for p in pts} - {None})
    peak = lambda f: max(((r.get(f) or 0), di) for _, _, di, r in pds) if pds else (0, 0)
    peak_day = lambda f: day_name(run["dates"][peak(f)[1]])
    hazards = [labels[r] for r in it["rules"]]
    where = ", ".join(districts[:5]) + (" (%s)" % "/".join(states) if states else "") + \
        ("; %d postcode%s within 50 km" % (it["n_pc"], "" if it["n_pc"] == 1 else "s"))

    head = "%s %s" % (HEAD[fam], area)
    if fam == "fire" and any(r.startswith("fire_") and r != "fire_minor" for r in it["rules"]):
        head = "Extreme fire weather in %s" % area
    if fam == "rain" and all(r.startswith("rain3") for r in it["rules"]):
        head = "Persistent heavy rain possible in %s" % area
    if fam == "frost" and any(r != "frost_minor" for r in it["rules"]):
        head = "Severe frost possible in %s" % area

    near = " Near %s." % ", ".join(towns) if towns else (
        " Remote: nearest town %s." % places.nearest(ranked[0][:2]) if ranked and places.nearest(ranked[0][:2]) else "")
    lines = ["Where: %s." % where + near]
    notes, writer = [], []
    when = days_words(it["dates"])
    place = (", ".join(districts[:3]) or area)

    if fam == "storms":
        cape, shear = peak("cape")[0], peak("shear")[0]
        wind, hail, stp = peak("wind")[0], peak("hail")[0], peak("stp")[0]
        rain = peak("rain")[0]
        threats = []
        if wind >= 3:
            threats.append("destructive winds (125+ km/h, isolated 160+)")
        elif wind >= 2:
            threats.append("destructive winds (125+ km/h)")
        elif wind >= 1:
            threats.append("damaging winds (90+ km/h)")
        if hail >= 3:
            threats.append("very large hail (6 cm+)")
        elif hail >= 2:
            threats.append("large hail (3-6 cm)")
        if stp >= 1:
            threats.append("tornado environment")
        lines.append("Threats: %s." % (", ".join(threats) or "severe storms (outlook category MDT+)"))
        lines.append("Ingredients: CAPE up to %s J/kg, shear up to %d kt, DCAPE up to %s, SCP up to %.1f%s."
                     % (format(r100(cape), ","), shear, format(r100(peak("dcape")[0]), ","), peak("scp")[0],
                        ", STP up to %.1f" % stp if stp >= 0.5 else ""))
        lines.append("Rain: up to %d mm in a day under storms." % r5(rain) if rain >= 5 else "Rain: little, under 5 mm.")
        if it["pub_cat"] >= 0:
            lines.append("Your published outlook: up to %s here." % CAT[it["pub_cat"]])
        inner = [(lat, lon) for lat, lon, _, r in ranked
                 if (r.get("cat") or 0) >= 4 or (r.get("wind") or 0) >= 2 or (r.get("hail") or 0) >= 3 or (r.get("stp") or 0) >= 2]
        inner_towns = places.towns_near(inner, n=3) if inner else []
        writer.append("Type: Severe thunderstorms (two outlines). Impact potential: %s." % TIER[it["level"]])
        writer.append("Outer outline (severe thunderstorms possible): %s%s." % (place, ", around " + ", ".join(towns) if towns else ""))
        if inner:
            writer.append("Inner outline (very dangerous): around %s." % (", ".join(inner_towns) or "the strongest points, %s" % place))
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
    elif fam == "rain":
        totals = sorted(rain72(run, lat, lon) for lat, lon in sorted(set(pts)))
        lo, hi = (statistics.quantiles(totals, n=4)[0], statistics.quantiles(totals, n=4)[2]) if len(totals) >= 4 else (min(totals), max(totals))
        day_peak, day_i = peak("rain")
        lines.append("Next 3 days: mostly %d-%d mm, up to %d mm. Heaviest day: %s, up to %d mm."
                     % (r5(lo), r5(hi), r5(max(totals)), day_name(run["dates"][day_i]), r5(day_peak)))
        lines.append("Why: %s." % "; ".join(hazards))
        writer.append("Type: General severe weather. Hazard: Heavy rainfall. Suggested tier: %s." % TIER[it["level"]])
        writer.append("Paint: %s%s." % (place, ", around " + ", ".join(towns) if towns else ""))
        notes.append("Heavy rain possible %s over %s." % (when, area))
        notes.append("Three-day totals mostly %d to %d mm, up to about %d mm in the heaviest spots. Heaviest on %s, up to %d mm that day."
                     % (r5(lo), r5(hi), r5(max(totals)), day_name(run["dates"][day_i]), r5(day_peak)))
        notes.append("Stress: rises in creeks and rivers, water over roads, never drive through floodwater.")
    elif fam == "wind":
        g, gi = peak("gust")
        n90 = len({(lat, lon) for lat, lon, _, r in pds if (r.get("gust") or 0) >= 90})
        lines.append("Gusts up to %d km/h (%s); 90+ km/h at %d model points." % (r5(g), day_name(run["dates"][gi]), n90))
        writer.append("Type: General severe weather. Hazard: Severe weather (fronts, damaging winds). Suggested tier: %s." % TIER[it["level"]])
        writer.append("Paint: %s%s." % (place, ", around " + ", ".join(towns) if towns else ""))
        notes.append("Damaging wind gusts possible %s over %s, up to about %d km/h, strongest on %s." % (when, area, r5(g), day_name(run["dates"][gi])))
        notes.append("Stress: secure loose items, keep clear of trees and powerlines, take care driving high-sided vehicles.")
    elif fam == "fire":
        f, fi = peak("ffdi")
        lines.append("FFDI up to %d (%s). %s." % (f, day_name(run["dates"][fi]), "; ".join(hazards)))
        writer.append("Type: General severe weather. Hazard: Bushfire. Suggested tier: %s." % TIER[it["level"]])
        writer.append("Paint: %s%s." % (place, ", around " + ", ".join(towns) if towns else ""))
        notes.append("Hot, dry and windy: dangerous fire weather %s over %s%s." % (
            when, area, ", worst on %s" % day_name(run["dates"][fi]) if len(it["dates"]) > 1 else ""))
        notes.append("Stress: follow the official fire danger rating and local fire service advice, and have a bushfire plan ready.")
    elif fam == "heat":
        lines.append("Why: %s." % "; ".join(hazards))
        writer.append("Type: General severe weather. Hazard: Heatwave. Suggested tier: %s." % TIER[it["level"]])
        writer.append("Paint: %s%s." % (place, ", around " + ", ".join(towns) if towns else ""))
        notes.append("Heatwave conditions %s over %s: days well above normal with warm nights (%s)." % (when, area, hazards[0]))
        notes.append("Stress: check on elderly neighbours, drink water, avoid the heat of the day.")
    elif fam == "frost":
        t = min((r.get("tmin") for _, _, _, r in pds if r.get("tmin") is not None), default=None)
        lines.append("Coldest minimum about %s C. %s." % ("%.0f" % t if t is not None else "?", "; ".join(hazards)))
        writer.append("Type: General severe weather. Hazard: Frost. Suggested tier: %s." % TIER[it["level"]])
        writer.append("Paint: %s." % place)
        notes.append("Frost likely %s over %s, minimums down to about %s C." % (when, area, "%.0f" % t if t is not None else "zero"))
    elif fam == "snow":
        s = peak("snow72")[0]
        lines.append("Up to %d cm of snow over 72 hours." % r5(s))
        writer.append("Type: General severe weather. Hazard: Blizzards / heavy snow. Suggested tier: %s." % TIER[it["level"]])
        writer.append("Paint: %s." % place)
        notes.append("Heavy snow possible %s over %s, up to about %d cm over three days." % (when, area, r5(s)))
    elif fam == "tc":
        c, ci = peak("tc_cat")
        lines.append("%s. Peak category %d in range." % ("; ".join(hazards), c))
        writer.append("Type: General severe weather. Hazard: Tropical cyclone (or Tropical low). Suggested tier: %s." % TIER[it["level"]])
        writer.append("Paint: %s." % place)
        notes.append("A tropical system may affect %s %s. Check the Cyclone Centre track before writing." % (area, when))

    seen = it["first_seen"]
    if seen and seen < run["run_date"]:
        lines.append("Flagged since the %s AWST run." % _run_local(seen).strftime("%a %-d %b"))
    else:
        lines.append("New in this run.")
    writer.append("Valid from: %s" % _valid(it["dates"][0], 0, states))
    writer.append("Valid until: %s" % _valid(it["dates"][-1], 1, states))
    writer.append("Notes for Claude (paste): " + " ".join(notes))
    it["district"] = districts[0] if districts else ""
    return head, lines, writer


def _clock(t):
    return t.strftime("%-I:%M %p" if t.minute else "%-I %p").lower()


def _run_local(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PERTH)


def _valid(date, end, states):
    """A model day is 00Z-24Z. AWST is what the Post Alert date boxes take (Josh's browser);
    the area's own time is beside it."""
    t = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=end)
    s = "%s %s AWST" % (t.astimezone(PERTH).strftime("%a %-d %b"), _clock(t.astimezone(PERTH)))
    zones = []
    for st in states:
        z = t.astimezone(ZoneInfo(STATE_TZ.get(st, "Australia/Perth")))
        lbl = "%s %s" % (_clock(z), z.tzname())
        if z.utcoffset() != t.astimezone(PERTH).utcoffset() and lbl not in zones:
            zones.append(lbl)
    return s + (" (%s)" % ", ".join(zones) if zones else "")


# --------------------------------------------------------------------------- email

def compose(run, th, events, districts, data_notes=()):
    labels = {r["id"]: r["label"] for r in th["rules"]}
    places = Places(districts)
    items = build_items(events, th, places)
    worth = [it for it in items if is_main(it)]
    main, over, minor = worth[:MAX_ITEMS], worth[MAX_ITEMS:], [it for it in items if not is_main(it)]
    run_local = _run_local(run["run_date"])
    first, last = run["dates"][0], run["dates"][min(DAYS, len(run["dates"])) - 1]
    body = ["Severe weather that could need an SWPA, %s to %s (model days 1-3)." % (days_text([first]), days_text([last])),
            "From the %s run of %s AWST. Model days run 00Z-24Z: 8 am-8 am in WA, 10 am-10 am in QLD."
            % (run.get("model", "model"), "%s %s" % (run_local.strftime("%a %-d %b"), _clock(run_local))), ""]
    described = [(it,) + describe(it, run, places, labels) for it in main]
    seen = {}
    for it, head, _, _ in described:
        seen[head] = seen.get(head, 0) + 1
    heads = []
    for it, head, _, _ in described:
        if seen[head] > 1 and it["district"]:  # two "north-west QLD" items: say which
            head += " (%s)" % it["district"]
        heads.append(head)
    if heads:
        body.append("At a glance:")
        body += ["  %d. %s · %s · %s" % (n, h, days_text(it["dates"]), TIER[it["level"]])
                 for n, (h, (it, _, _, _)) in enumerate(zip(heads, described), 1)]
        body.append("")
    for n, (head, (it, _, lines, writer)) in enumerate(zip(heads, described), 1):
        body.append("%d. %s" % (n, head.upper() if it["level"] >= 3 else head))
        body.append("   %s · suggested tier %s" % (days_text(it["dates"]), TIER[it["level"]]))
        body += ["   • " + l for l in lines]
        body.append("")
        body.append("   Post Alert writer")
        body += ["   " + w for w in writer]
        body.append("")
    if not main:
        body += ["Nothing in the next 3 days reaches an SWPA threshold.", ""]

    def one_line(it):
        head, _, _ = describe(it, run, places, labels)
        return "   %s · %s · %s (%s)" % (days_text(it["dates"]), TIER[it["level"]], head, it["district"] or "-")

    if over:
        body.append("Also flagged (past the first %d):" % MAX_ITEMS)
        body += [one_line(it) for it in over]
        body.append("")
    if minor:
        body.append("Smaller signals (one spot, one day, Minor):")
        body += [one_line(it) for it in minor[:10]]
        if len(minor) > 10:
            body.append("   and %d more" % (len(minor) - 10))
        body.append("")
    body.append("Checked: storms, hail, convective wind, tornado, rain (day and 3-day), wind gusts, fire, heat, frost, snow, tropical.")
    body += ["Data: " + n for n in data_notes if n]
    body += ["", "Automatic detection from the raw model run. Not a forecast, and nothing has gone to members."]
    if heads:
        more = len(heads) - 1 + len(over)
        subject = "3-day severe watch: %s%s" % (heads[0], " (+%d more)" % more if more else "")
    else:
        subject = "3-day severe watch: nothing at SWPA level"
    return subject, "\n".join(body)


def send(subject, text, key):
    api = os.environ.get("RESEND_API_KEY")
    if not api:
        return "not sent: RESEND_API_KEY is not set"
    req = urllib.request.Request(
        "https://api.resend.com/emails", method="POST",
        data=json.dumps({"from": FROM, "to": [TO], "subject": subject, "text": text}).encode(),
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
