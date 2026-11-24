"""Climate regions, so a threshold can mean different things in different parts of Australia.

Josh, 23 Oct 2026: "there's going to be different thresholds for different parts of Australia.
Like, rainfall is not a blanket one size fits all. Same with temperature... build those
thresholds, not for every postcode, but for every region around Australia."

Regions are declared in alerts/regions.json and matched in order, first hit wins, so a narrow
region (alpine) is listed before the broad one it sits inside (murray_darling). A declared
`default` region catches anything unmatched, so region_for() can never return None and a rule
can never silently lose its threshold.

A threshold value in thresholds.json may be a plain number (national, as before) or
  {"default": 75, "by_region": {"tropical_north": 100}}
which resolve_value() turns back into a number for the region being evaluated. Plain numbers
keep working untouched, so this is backwards compatible with every existing rule.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGIONS_PATH = os.path.join(ROOT, "alerts", "regions.json")


class Regions:
    def __init__(self, spec):
        self.default = spec["default"]
        self.regions = [r for r in spec["regions"]]
        self.by_id = {r["id"]: r for r in self.regions}
        if self.default not in self.by_id:
            raise ValueError("regions.json default %r is not a declared region" % self.default)
        self._cache = {}

    @classmethod
    def load(cls, path=REGIONS_PATH):
        with open(path) as f:
            return cls(json.load(f))

    def region_for(self, lat, lon):
        """The id of the first region whose box contains the point, else the default.

        Bounds are [min, max): min inclusive, max exclusive, so adjacent regions that share an
        edge cannot both claim a point. A point outside every box falls to the default rather
        than returning None — an unregioned point would silently drop its threshold.
        """
        if lat is None or lon is None:
            return self.default
        key = (round(lat, 2), round(lon, 2))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        found = self.default
        for r in self.regions:
            lo_lat, hi_lat = r["lat"]
            lo_lon, hi_lon = r["lon"]
            if lo_lat <= lat < hi_lat and lo_lon <= lon < hi_lon:
                found = r["id"]
                break
        self._cache[key] = found
        return found

    def label(self, region_id):
        r = self.by_id.get(region_id)
        return r["label"] if r else region_id


def resolve_value(raw, region, bars=None):
    """A threshold value for one region.

    Three forms:
      75                                  a plain national number, passes straight through
      {"default": 75, "by_region": {...}}  inline per-region values
      {"$ref_region_bar": "rain_heavy"}    a named bar from thresholds.json's region_bars,
                                           so one bar is tuned in one place

    Anything malformed raises rather than guessing. A rule silently falling back to the wrong
    number is worse than a run that stops: it would fire alerts on a threshold nobody set.
    """
    if not isinstance(raw, dict):
        return raw

    ref = raw.get("$ref_region_bar")
    if ref is not None:
        if not bars or ref not in bars:
            raise ValueError("rule refers to region bar %r but thresholds.json has no "
                             "region_bars entry for it" % ref)
        raw = bars[ref]
        if not isinstance(raw, dict):
            return raw

    by_region = raw.get("by_region") or {}
    if region is not None and region in by_region:
        return by_region[region]
    if "default" not in raw:
        raise ValueError("threshold value %r has no 'default' and no entry for region %r"
                         % (raw, region))
    return raw["default"]
