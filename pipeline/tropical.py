"""Tropical cyclone and tropical low hazard for the alert scan.

Adds two per-point, per-day fields to a raw run, so the ordinary rules in alerts/thresholds.json
can use them:

    tc_cat    0-5   Australian category of the strongest system whose ensemble cone covers this
                    point on this day. 0 means a tropical low or invest (below category 1) is
                    over the point, which is the rulebook's Minor tier. The field is ABSENT when
                    no system is near, and a missing field never fires a rule.
    tc_prob   0-1   ensemble member_fraction for that track point - the share of members that
                    still have the system there. The probability gate the rulebook asks for.

Source is the app's `tropicalSystems` function, which serves the TropicalSystem rows written 00Z
and 12Z by its own "Fetch TC Tracks" workflows. Each row carries `consensus_track`: per forecast
hour, the ensemble mean position, `member_fraction`, cone radii at the 50th/70th/90th percentiles,
and `vmax_mean`.

ACCESS, and a real limitation. This used to read the entity directly and unauthenticated, because
its RLS said `"read": {}`. That open rule was the leak that also handed the Front Line forecast
product to anyone with the URL, so it was closed on 23 Oct 2026 and reads now go through the
function, which gates by entitlement.

  - WITHOUT a token the function returns the FREE shape, whose consensus_track is clipped to
    lead_days <= 3. So the cyclone rules only see days 1-3, not the full 7 the rest of the scan
    covers. A cyclone inside three days is the actionable window, so this is useful rather than
    useless, but it IS a reduction and it is deliberate, not an oversight.
  - WITH BASE44_TOKEN set to an admin token the function returns the full set and the cyclone
    rules cover the whole 7 days like every other hazard.

Either way a failed read returns no systems, the tc_ fields are never set, no cyclone rule fires,
and the rest of the scan is untouched. The run never fails because of this.

The entity is GLOBAL: on 22 Oct 2026 the four active systems were in the Indian Ocean, the east
Pacific, the Atlantic and the west Pacific, none of them near Australia. AU_REGION filters to
the Australian area of responsibility so a hurricane off Mexico cannot raise an Australian alert.

Stdlib only, like the rest of the pipeline.
"""
import datetime as dt
import json
import math
import os
import urllib.request

# BoM's area of responsibility, widened a little to catch systems still approaching it.
AU_REGION = {"lat": (-45.0, 0.0), "lon": (90.0, 170.0)}

# Public: this id is in the app's committed .env.native, it is not a secret.
DEFAULT_APP_ID = "69fbea2b450e42a3a7c55ce9"

# Australian category is defined on the 10-minute mean wind; ATCF vmax is the 1-minute mean.
# 0.88 is the standard conversion. Verified against the entity's own peak_category for four real
# systems on 22 Oct 2026 (sh022027, ep222026, al102026, wp292026) - all four reproduce exactly.
ONE_MIN_TO_TEN_MIN = 0.88
CAT_BANDS = [(108, 5), (86, 4), (64, 3), (48, 2), (34, 1)]

# Which cone percentile counts as "over the point". r70 is the middle of the three the entity
# stores: r50 is too tight to warn on, r90 is wide enough to cover most of a state late in the run.
CONE_RADIUS_FIELD = "r70_km"
MIN_CONE_KM = 60.0  # early track points have a very small cone; do not let it shrink below this


def au_category(vmax_1min_kt):
    """Australian category 0-5 from an ATCF 1-minute vmax in knots. 0 is a low, below category 1."""
    if vmax_1min_kt is None:
        return None
    kt10 = vmax_1min_kt * ONE_MIN_TO_TEN_MIN
    for lo, cat in CAT_BANDS:
        if kt10 >= lo:
            return cat
    return 0


def in_au_region(lat, lon):
    return (AU_REGION["lat"][0] <= lat <= AU_REGION["lat"][1]
            and AU_REGION["lon"][0] <= lon <= AU_REGION["lon"][1])


def km_between(lat1, lon1, lat2, lon2):
    dlat = (lat2 - lat1) * 111.0
    dlon = (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def fetch_systems(app_id="", token="", url=None):
    """Active TropicalSystem rows that are in or heading for the Australian region.

    Returns [] on any failure - network error, bad payload, a tightened read rule. A cyclone
    hazard that cannot be evaluated must leave the scan alone, not break it.
    """
    app_id = app_id or os.environ.get("BASE44_APP_ID", "") or DEFAULT_APP_ID
    token = token or os.environ.get("BASE44_TOKEN", "")
    url = url or ("https://cyclonesoz.com.au/api/apps/%s/functions/tropicalSystems" % app_id)
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": "CyclonesOZ-alert-scan/1.0"}
    if token:  # an admin token lifts the free day-3 clip; see the module docstring
        headers["Authorization"] = "Bearer %s" % token
    try:
        req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
    except Exception:
        return []
    rows = body.get("systems") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return []
    return [s for s in rows if relevant(s)]


def relevant(system):
    """True if any part of the system's current position or consensus track is in the AU region."""
    cur = system.get("current") or {}
    if cur.get("lat") is not None and in_au_region(cur["lat"], cur["lon"]):
        return True
    return any(in_au_region(p["lat"], p["lon"])
               for p in (system.get("consensus_track") or [])
               if p.get("lat") is not None and p.get("lon") is not None)


def track_by_date(system, dates):
    """{date: [(lat, lon, cone_km, cat, prob), ...]} for the model days the run covers.

    A track point's valid time is the system's init_time plus `tau` hours; it is binned to the
    UTC date it lands on, because a model day in this pipeline is 00Z-24Z.
    """
    init = system.get("init_time")
    if not init:
        return {}
    try:
        t0 = dt.datetime.fromisoformat(init.replace("Z", "+00:00"))
    except ValueError:
        return {}
    want = set(dates)
    out = {}
    for p in system.get("consensus_track") or []:
        if p.get("lat") is None or p.get("lon") is None or p.get("tau") is None:
            continue
        date = (t0 + dt.timedelta(hours=float(p["tau"]))).date().isoformat()
        if date not in want:
            continue
        cone = max(float(p.get(CONE_RADIUS_FIELD) or 0), MIN_CONE_KM)
        out.setdefault(date, []).append((
            p["lat"], p["lon"], cone,
            au_category(p.get("vmax_mean")),
            float(p.get("member_fraction") or 0),
        ))
    return out


def augment_run(run, systems=None, app_id="", token=""):
    """Add 'tc_cat' and 'tc_prob' to every point-day a system's cone covers.

    Returns a one-line status for the email. Points no system reaches are left alone, so the
    fields are absent rather than zero and a rule cannot fire on "no cyclone".
    """
    if systems is None:
        systems = fetch_systems(app_id, token)
    if not systems:
        return "tropical: no systems in the Australian region (cyclone rules idle)"

    dates = run["dates"]
    per_system = [(s, track_by_date(s, dates)) for s in systems]
    hits = 0
    for p in run["points"]:
        for di, date in enumerate(dates):
            if di >= len(p["d"]):
                break
            best_cat, best_prob = None, 0.0
            for _s, byday in per_system:
                for lat, lon, cone, cat, prob in byday.get(date, []):
                    if cat is None:
                        continue
                    if km_between(p["lat"], p["lon"], lat, lon) <= cone:
                        if best_cat is None or cat > best_cat:
                            best_cat, best_prob = cat, prob
                        elif cat == best_cat:
                            best_prob = max(best_prob, prob)
            if best_cat is not None:
                p["d"][di]["tc_cat"] = best_cat
                p["d"][di]["tc_prob"] = round(best_prob, 3)
                hits += 1
    names = ", ".join(s.get("name") or s.get("storm_id", "?") for s, _ in per_system)
    return "tropical: %d system(s) in region (%s), %d point-days covered" % (
        len(systems), names, hits)
