# Draft thresholds v1 (for Josh to edit)

Edit `alerts/thresholds.json`, then `python3 pipeline/alert_scan.py --backtest`. `level` = impact tier.
Counts are events over the 10 archived runs (8-17 Oct 2026), before change-only filtering.

| Tier | Rule | Fires when (per grid point, per day) | Days | Points needed | Events |
|---|---|---|---|---|---|
| Minor | minor_storm | cat >= MRGL and CAPE >= 1500 | 1-3 | 4 | 68 |
| Minor | minor_wind | wind tier >= 1 and CAPE >= 1000 | 1-3 | 4 | 87 |
| Moderate | mod_hail | hail tier >= 2 | 1-4 | 3 | 4 |
| Moderate | mod_wind | wind tier >= 2 | 1-4 | 3 | 13 |
| Moderate | mod_storm | cat >= MDT | 1-4 | 3 | 18 |
| Moderate | mod_rain | rain >= 50 mm/day | 1-5 | 4 | 2 |
| Significant | sig_supercell | SCP >= 8, CAPE >= 2000, shear >= 35 kt | 1-5 | 3 | 3 |
| Significant | sig_wind | wind tier 3 | 1-5 | 3 | 10 |
| Significant | sig_rain | rain >= 100 mm/day | 1-5 | 4 | 0 |
| Significant | sig_flood | flood tier >= 2 | 1-5 | 2 | 0 |
| Major | major_tornado | STP >= 2 and CAPE >= 2000 | 1-3 | 2 | 0 |
| Major | major_cat | cat HIGH | 1-3 | 2 | 0 |
| Major | major_rain | rain >= 180 mm/day | 1-5 | 5 | 0 |

Notes: the model grid is ~80 km, so a tier here is a district-scale signal; you set the postcodes by hand.
Rain is mm per UTC day. Nothing here reaches members: it only emails you.
