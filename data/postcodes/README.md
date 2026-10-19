# Postcode areas

Drawable borders and metadata for every Australian postcode, for the postcode-targeted alert map.

| File | What it is |
|---|---|
| `poa_2021.topo.json` | 2,641 postal-area polygons (TopoJSON, ~2 MB). Property `POA_CODE21` is the postcode. |
| `postcodes.json` | 3,175 postcodes: state, localities, coordinates, Australia Post type, and the area that draws it. |

**Point-only postcodes.** 534 postcodes have no area of their own in the ABS data (PO Boxes, large-volume
receivers, university/hospital codes, a few small localities such as 4219 West Burleigh and 4803 Hamilton
Island). Each has a `host`: the area that physically contains its point, found by spatial join. Selecting
a host area on the map reaches subscribers whose postcode is hosted by it. Postcode `6958` has no
coordinates in the source list and no host; it needs a manual assignment.

**Not drawn.** ABS special codes `ZZZZ`, `9494`, `9797` (no usual address, migratory and similar) have no
geometry and are removed.

## Rebuild

```bash
# 1. ABS Postal Areas 2021, GDA2020 shapefile (ABS, CC BY 4.0 -- keep the attribution)
curl -LO https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files/POA_2021_AUST_GDA2020_SHP.zip
# 2. Simplify to 6% (topology-preserving, no postcode dropped) and drop the three special codes
npx mapshaper POA_2021_AUST_GDA2020.shp -filter-fields POA_CODE21 -simplify 6% keep-shapes \
  -filter 'POA_CODE21 !== "ZZZZ" && POA_CODE21 !== "9494" && POA_CODE21 !== "9797"' \
  -o data/postcodes/poa_2021.topo.json format=topojson quantization=100000
# 3. Point-only postcodes -> the full-resolution area that contains them (points.geojson is built from
#    australian_postcodes.csv rows whose postcode has no polygon)
npx mapshaper points.geojson -join POA_2021_AUST_GDA2020.shp fields=POA_CODE21 -o points_joined.json format=geojson
# 4. Assemble
python3 pipeline/build_postcode_data.py --topo data/postcodes/poa_2021.topo.json \
  --csv australian_postcodes.csv --joined points_joined.json --out data/postcodes/postcodes.json
```

`australian_postcodes.csv` is Matthew Proctor's list (github.com/matthewproctor/australianpostcodes), used only
for names, state, coordinates and type. Check its licence before redistributing it.

**Simplification.** 6% was chosen by comparing 3%, 6% and 12% against the full-resolution file around
Brisbane and Toowoomba. 3% roughens coastlines; 6% and 12% are visually close to the original.
The 2021 areas can differ slightly from today's postcodes.
