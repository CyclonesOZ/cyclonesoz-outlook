"""Turn scan events into proposed postcode alerts for the Postcode Alert tab.

A proposal is only a suggestion: it lists postcodes near the firing model points, coloured by the
event's impact tier, plus the facts a forecaster draft needs. Nothing here sends anything; Josh opens
a proposal in the tab, edits the map and text, tests, and publishes himself. Stdlib only."""
import json
import math

TIERS = {1: "minor", 2: "moderate", 3: "significant", 4: "major"}
RADIUS_DEG = 0.45  # a postcode joins an event if its centre is within ~50 km of a firing model point


def _near(lat, lon, pts):
    for plat, plon in pts:
        dy, dx = lat - plat, (lon - plon) * math.cos(math.radians(lat))
        if dy * dy + dx * dx <= RADIUS_DEG * RADIUS_DEG:
            return True
    return False


def postcodes_for(event, postcodes):
    pts = [tuple(float(x) for x in k.split(",")) for k in event["points"]]
    south, west, north, east = event["bbox"]
    pad = RADIUS_DEG + 0.1
    return sorted(pc for pc, v in postcodes.items()
                  if v.get("lat") is not None and south - pad <= v["lat"] <= north + pad
                  and west - pad <= v["lon"] <= east + pad and _near(v["lat"], v["lon"], pts))


def build_proposals(events, th, postcodes):
    """One proposal per event, tier = event level. Where proposals overlap, the tab shows the highest."""
    labels = {r["id"]: r["label"] for r in th["rules"]}
    out = []
    for ev in events:
        tier = TIERS.get(ev["level"], "minor")
        top = [labels[r] for r in ev["rules"] if r in labels][:3]
        pcs = postcodes_for(ev, postcodes)
        out.append({
            "id": ev.get("id"), "date": ev["date"], "lead_day": ev["lead"], "tier": tier,
            "hazards": top, "districts": ev.get("districts", []),
            "window_local": ev.get("window_local"), "centroid": ev["centroid"],
            "model_points": ev["n_points"], "peak": ev.get("peak", {}),
            "postcodes": {pc: tier for pc in pcs}, "n_postcodes": len(pcs),
            "suggested_title": "%s for %s" % (top[0] if top else "Severe weather", ", ".join(ev.get("districts", [])[:3]) or "the region"),
        })
    return sorted(out, key=lambda p: (-list(TIERS.values()).index(p["tier"]), p["date"]))


def build_episode_proposals(events, th, postcodes):
    """One proposal per episode (a week of rain is one alert, not seven): postcodes are the union across
    the days, each at the highest tier it reaches, valid from the first day to the last."""
    by_ep = {}
    for ev in events:
        by_ep.setdefault(ev.get("episode_id") or ("E-" + str(ev.get("id"))), []).append(ev)
    rank = {t: i for i, t in enumerate(TIERS.values())}
    out = []
    for eid, evs in by_ep.items():
        evs.sort(key=lambda e: e["date"])
        daily = build_proposals(evs, th, postcodes)
        merged = {}
        for d in daily:
            for pc, tier in d["postcodes"].items():
                if pc not in merged or rank[tier] > rank[merged[pc]]:
                    merged[pc] = tier
        peak = max(daily, key=lambda d: (rank[d["tier"]], d["model_points"]))
        out.append({
            "episode_id": eid, "kind": "one_off" if len({e["date"] for e in evs}) == 1 else "multi_day",
            "first_date": min(e.get("start_date", e["date"]) for e in evs), "last_date": evs[-1]["date"],
            "n_event_days": len({e["date"] for e in evs}),
            "tier": peak["tier"], "peak_date": peak["date"], "hazards": peak["hazards"],
            "districts": list(dict.fromkeys(x for d in daily for x in d["districts"]))[:6],
            "basis": evs[0].get("basis", "raw model run"),
            "days": [{"date": d["date"], "tier": d["tier"], "model_points": d["model_points"], "n_postcodes": d["n_postcodes"]} for d in daily],
            "postcodes": merged, "n_postcodes": len(merged),
            "suggested_title": peak["suggested_title"],
        })
    return sorted(out, key=lambda x: (-rank[x["tier"]], x["first_date"]))


def write_proposals(path, run_date, events, th, postcodes_path):
    with open(postcodes_path) as f:
        postcodes = json.load(f)
    data = {"run_date": run_date, "note": "Suggestions only. Nothing is sent from here. One proposal per episode.",
            "proposals": build_episode_proposals(events, th, postcodes)}
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    return data
