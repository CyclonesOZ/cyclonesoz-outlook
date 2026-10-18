# Draft thresholds v2 (for Josh to edit)

Tier scheme (Josh, 19 Oct 2026): **Minor = MDT-equivalent. Moderate = widespread MDT or isolated HIGH. Significant = HIGH.**
Major is unused. Each hazard uses its own scale, so not every hazard reaches every tier.
**Widespread = 6+ neighbouring grid points** (grid is ~80 km, so roughly 38,000 km2). Change `W` in the rules' `min_points`.
Edit `alerts/thresholds.json`, then `python3 pipeline/alert_scan.py --backtest`.
The model caps HIGH at days 1-3 and MDT at days 4-5, so higher tiers cannot appear later in the week.

| # | Hazard | Minor | Moderate | Significant | Events in 10 runs (M / Mod / Sig) |
|---|---|---|---|---|---|
| 1 | Severe storms (overall category) | MDT, days 1-5 | 6+ pts MDT, or isolated HIGH | 6+ pts HIGH | 109 / 13 / 0 |
| 2 | Large hail | hail tier 2 (3-6 cm) | 6+ pts tier 2, or any tier 3 (6 cm+) | 6+ pts tier 3 | 17 / 2 / 0 |
| 3 | Damaging wind | tier 1 (90+ km/h) with MDT | tier 2 (125+ km/h) | tier 3 (160+ km/h) | 10 / 33 / 11 |
| 4 | Tornado environment | STP 1+ with MDT | STP 2+ with MDT | STP 4+ with HIGH | 0 / 0 / 0 |
| 5 | Heavy rain / flash flooding | 75+ mm/day (2 pts) | 6+ pts 75 mm, or isolated 150+ mm | 6+ pts 150+ mm | 1 / 0 / 0 |
| 6 | Fire danger | 6+ pts FFDI 50+ | FFDI 100+ | 6+ pts FFDI 100+ | 57 / 0 / 0 |
| 7 | Tropical cyclone | not in the model run: needs a separate feed | | | |
| 8 | Heatwave | not in the model run: needs a new source | | | |
| 9 | Snow / cold outbreak | not in the model run: needs a new source | | | |

Things to check:
- **Wind looks hot.** 71 point-days at tier 3 (160+ km/h) in 10 runs is a lot for "very destructive". Worth checking how the model's wind tier is calibrated before trusting it.
- **Fire Minor is noisy** (57 events). Probably wants a higher bar or a longer minimum run of days.
- **Tornado and heavy rain barely fire.** Their numbers are untested.
- This only emails Josh. Nothing here reaches members.
