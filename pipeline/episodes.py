"""Group daily scan events into episodes.

The scan makes one event per (date, cluster). A week-long rain event or a run of fire-danger days
would therefore be 5-7 separate detections. An episode joins them: events on the same or neighbouring
days, of the same hazard group, in the same place. An episode seen on one day only is a one-off.

    link_events(events)   -> episodes (list of dicts)
    annotate(events)      -> sets episode_id / episode_kind / episode_day on each event, returns episodes

Stdlib only."""
import datetime as dt
import math

# first word of a rule id -> hazard group. Unknown prefixes are their own group.
GROUP = {"storms": "thunderstorm", "hail": "thunderstorm", "wind": "thunderstorm", "tor": "thunderstorm",
         "supercell": "thunderstorm", "severe": "thunderstorm", "rain": "rain", "rain3": "rain", "rain5": "rain",
         "flood": "rain", "fire": "fire", "heat": "heat", "snow": "snow"}
GROUP_LABEL = {"thunderstorm": "Thunderstorms", "rain": "Heavy rain", "fire": "Fire weather", "heat": "Heatwave",
               "snow": "Snow"}
MAX_GAP_DAYS = 1      # a dry day inside a wet week does not split the episode
LINK_DEG = 2.0        # centres within this distance count as the same place even with no shared model point


def hazard_group(rule_id):
    p = str(rule_id).split("_")[0]
    return GROUP.get(p, p)


def groups_of(ev):
    return {hazard_group(r) for r in ev["rules"]}


def _dist(a, b):
    dy = a[0] - b[0]
    dx = (a[1] - b[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def _same_place(a, b, link_deg):
    return bool(set(a["points"]) & set(b["points"])) or _dist(a["centroid"], b["centroid"]) <= link_deg


def link_events(events, max_gap_days=MAX_GAP_DAYS, link_deg=LINK_DEG):
    n = len(events)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    d = [dt.date.fromisoformat(e["date"]) for e in events]
    for i in range(n):
        for j in range(i + 1, n):
            gap = abs((d[j] - d[i]).days)
            if gap < 1 or gap > 1 + max_gap_days:
                continue
            if groups_of(events[i]) & groups_of(events[j]) and _same_place(events[i], events[j], link_deg):
                parent[find(i)] = find(j)
    members = {}
    for i in range(n):
        members.setdefault(find(i), []).append(i)

    eps = []
    for idxs in members.values():
        evs = sorted((events[i] for i in idxs), key=lambda e: (e["date"], e.get("id", "")))
        dates = sorted({e["date"] for e in evs})
        peak = max(evs, key=lambda e: (e["level"], e["n_points"]))
        gcount = {}
        for e in evs:
            for g in groups_of(e):
                gcount[g] = gcount.get(g, 0) + 1
        main_group = max(gcount, key=lambda g: (gcount[g], g))
        seed = min(evs, key=lambda e: (e.get("first_seen", ""), e["date"], e.get("id", "")))
        start = min(e.get("start_date", e["date"]) for e in evs)   # accumulation rules start before they fire
        span = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(start)).days + 1
        inherited = sorted({e["episode_id"] for e in evs if e.get("episode_id")})   # carried from last scan
        eps.append({
            "episode_id": inherited[0] if inherited else "E-" + str(seed.get("id") or seed["date"]),
            "group": main_group, "groups": sorted(gcount), "kind": "one_off" if len(dates) == 1 else "multi_day",
            "first_date": start, "first_detected": dates[0], "last_date": dates[-1], "n_event_days": len(dates), "span_days": span,
            "peak_level": peak["level"], "peak_date": peak["date"],
            "event_ids": [e.get("id") for e in evs], "events": evs,
            "points": sorted({p for e in evs for p in e["points"]}),
            "districts": list(dict.fromkeys(x for e in evs for x in e.get("districts", [])))[:6],
        })
    eps.sort(key=lambda e: (e["first_date"], -e["peak_level"], e["episode_id"]))
    return eps


def annotate(events, **kw):
    eps = link_events(events, **kw)
    for ep in eps:
        for k, e in enumerate(sorted(ep["events"], key=lambda e: e["date"]), start=1):
            e["episode_id"], e["episode_kind"], e["episode_day"] = ep["episode_id"], ep["kind"], k
            e["episode_days"] = ep["n_event_days"]
    return eps
