#!/usr/bin/env python3
"""Build data/postcodes/postcodes.json: one record per Australian postcode, linking every postcode
to a drawable area on the map.

Inputs (see data/postcodes/README.md for how each is made):
  --topo    simplified ABS POA TopoJSON (data/postcodes/poa_2021.topo.json)
  --csv     Matthew Proctor's australian_postcodes.csv (names, state, coordinates, type)
  --joined  point-only postcodes spatially joined to the POA polygon that contains them (GeoJSON)

Output record: {"state", "localities": [..up to 8..], "n_localities", "lat", "lon", "types": [..],
                "area": true|false, "host": "<POA code that draws it>"}
A postcode with its own area has host == itself. A point-only postcode (PO Box, large-volume
receiver, a few small localities) is hosted by the area that physically contains its point, so
selecting that area on the map reaches its subscribers. No coordinates means no host (host null).
Standard library only.
"""
import argparse
import collections
import csv
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topo", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--joined", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    topo = json.load(open(a.topo))
    areas = {g["properties"]["POA_CODE21"] for g in next(iter(topo["objects"].values()))["geometries"]
             if g.get("arcs")}
    host_of = {f["properties"]["postcode"]: f["properties"].get("POA_CODE21")
               for f in json.load(open(a.joined))["features"]}

    meta = collections.OrderedDict()
    for r in csv.DictReader(open(a.csv, encoding="utf-8-sig")):
        p = r["postcode"].zfill(4)
        m = meta.setdefault(p, {"state": r["state"], "localities": [], "lat": None, "lon": None, "types": []})
        if r["locality"] and r["locality"] not in m["localities"]:
            m["localities"].append(r["locality"])
        t = r["type"] or "Unknown"
        if t not in m["types"]:
            m["types"].append(t)
        if m["lat"] is None:
            try:
                la, lo = float(r["Lat_precise"] or r["lat"]), float(r["Long_precise"] or r["long"])
            except ValueError:
                continue
            if la and lo:
                m["lat"], m["lon"] = round(la, 5), round(lo, 5)

    out = {}
    for p in sorted(set(meta) | areas):
        m = meta.get(p, {"state": None, "localities": [], "lat": None, "lon": None, "types": []})
        has = p in areas
        out[p] = {
            "state": m["state"], "localities": m["localities"][:8], "n_localities": len(m["localities"]),
            "lat": m["lat"], "lon": m["lon"], "types": m["types"], "area": has,
            "host": p if has else host_of.get(p),
        }
    json.dump(out, open(a.out, "w"), separators=(",", ":"), sort_keys=True)
    n_area = sum(v["area"] for v in out.values())
    print("%d postcodes: %d with an area, %d point-only (%d hosted, %d with no host)" % (
        len(out), n_area, len(out) - n_area,
        sum(1 for v in out.values() if not v["area"] and v["host"]),
        sum(1 for v in out.values() if not v["host"])))


if __name__ == "__main__":
    main()
