# Front Line Severe Outlook: Threshold Rulebook v1

Written 19 Oct 2026 in answer to "Threshold Rulebook Prompt v2". Draft for Josh to edit. Machine-readable twin: `rulebook.json`. Prospect record schema: `prospect.schema.json`. Nothing in here sends anything to anyone: the sweep only fills a review queue, and only Josh publishes.

## 0. Read this first

**How honest the numbers are.** I could not reach SILO, ERA5, BoM warnings or ensemble data from the build environment, so no threshold below was fitted to real climatology. What is solid:
- Josh's own definitions (the tier scheme, tropical cyclone tiers, snow depths, heatwave anomalies) are carried over exactly.
- Absolute benchmarks that are BoM or industry standards are used as stated: wind 90 / 125 / 160 km/h, Australian cyclone categories, FFDI 50 / 75 / 100, EHF. No BoM data feed is used for flooding (it is paywalled): flood is built from rainfall.
- Percentile and return-period rules are given as formulas the code computes from SILO or ERA5. The numeric floors and caps around them are my starting guesses and are flagged as tunable.

What I could test: the current scanner's output on 10 archived model runs (8 to 17 Oct 2026). That is the only measured frequency in this document.

**Historical calibration events** are from memory. Each carries a confidence tag: `ok` means I am confident of the event and rough magnitude, `verify` means check it against BoM records before using it to tune anything.

**Where this differs from what exists today.**

| | Today (scanner on `claude/alert-scan-thresholds`) | This rulebook |
|---|---|---|
| Hazards run | 6 (storms, hail, wind, tornado, rain, fire), plus heat and snow once the climatology builds | 11 |
| Climate-relative | No, fixed numbers on the model's own tiers | Yes: SILO / ERA5 percentiles per cell, with absolute floors |
| Probability | One deterministic model (ECMWF IFS) plus GFS as a second opinion | Ensemble probability gates by lead day |
| Persistence | Emails changes against a saved state | Full prospect lifecycle with hysteresis and an annual alert budget |
| Output | Email to Josh | Review queue records (schema in section 6), draft message per prospect |

**Timing problem to decide.** 03:00 AEDT is 16:00 UTC. At that time the newest complete data are the ECMWF 00Z run and the GFS/GEFS 06Z run. The ECMWF 12Z run is not out until about 19:40 UTC (06:40 AEDT). The nightly build runs at 19:52 UTC, so it does use 12Z. Recommendation: run the sweep at 03:00 on 00Z/06Z data as the early look, and run it again straight after the nightly build (about 07:30 AEDT) as the update, so the queue Josh sees over breakfast has the freshest guidance. Both runs use the same persistence logic, so the second run updates prospects, not duplicates them.

## 1. Summary table (headline thresholds)

Tiers follow Josh's definitions: Minor a few times a year, Moderate about twice a year, Significant about once a year, Major rare. Every cell needs the probability and area gates in section 2 as well.

| # | Hazard | Minor | Moderate | Significant | Major |
|---|---|---|---|---|---|
| 1 | Tropical cyclone | Tropical low, 30%+ chance of gales, within 300 km of coast | Category 1 | Category 2-3 | Category 4-5 |
| 2 | Severe thunderstorms | MDT category | Widespread MDT (6+ cells) or isolated HIGH | HIGH widespread (6+ cells), or hail 6 cm+, wind 160 km/h+, or STP 4+ | HIGH, 12+ cells, two top-tier hazards, 1M+ people |
| 3 | Flooding (rain-based) | Rain at Minor level on soil 70th pct+, or the zone's 3-hour flash depth (3.1) | Rain at Moderate level on soil 85th pct+ | Rain at Significant level on wet soil, or Moderate on saturated soil | Major rain on wet soil, or Significant on saturated soil over 12+ cells |
| 4 | Heavy rain | 10th-wettest day of a typical year | 2nd-wettest day | Median annual-maximum day | 10-year return level |
| 5 | Cold front | 2 of 4 signals (gust 90, 12 C drop, 10 hPa fall, snow level) | Gust 100+ and 14 C drop, 6+ cells | Wind tier 2 (125 km/h) at 3+ cells with the front | Wind tier 3, or 1,000 km wide gale band |
| 6 | Damaging wind (non-convective) | Gust 90+ and P99.5 | Gust 110+ and P99.8 | Gust 125+ and P99.95 | Gust 160+ coastal / 140+ inland and P99.99 |
| 7 | Heatwave | Max +12 C, 2+ days, warm nights | +14 C | +17 C | +21 C |
| 8 | Fire weather | AFDRS Extreme, 1+ district | Extreme widespread, or Catastrophic isolated | Catastrophic widespread | Catastrophic, 12+ cells, 2+ days or metro interface |
| 9 | Frost / cold | 0 C or below, 2 nights, crop zone, Aug-Oct | -2 C, 2 nights, Sep-Oct | -3 C, 2 nights, 6+ cells | -5 C or station records, 12+ cells |
| 10 | Snow / blizzard | Low-level snow 5 cm+ below 800 m | 50 cm+ in 72 h | 100 cm+ in 72 h | 150 cm+ or road-closing blizzard |
| 11 | Waves / storm surge | Hs P99 and 3.5 m (east) | P99.7 and 4.5 m | P99.9 and 6 m, or tide above HAT | P99.99 and 8 m, or HAT +0.5 m with 4 m waves |

Snow Minor and Major are my additions: Josh's earlier spec only had Moderate and Significant. Delete them if you want to keep that.

## 2. Rules that apply to every hazard

### 2.1 Grid, cells and aggregation
- **Evaluation unit:** the existing national model grid (1,342 points, about 80 km spacing, roughly 6,400 km2 a cell). Each cell is evaluated per day for days 1-7 against each hazard's tier thresholds.
- **Scale match:** climatology is computed on the same scale as the forecast. SILO (0.05 degrees) is averaged up to the model cells before any percentile is taken, so a model cell mean is compared with a cell-mean climate, not a point gauge. This is the single most important calibration decision, and it stops models looking "too wet" or "too weak" against point records.
- **Clustering:** firing cells within 1.1 degrees of each other form one cluster (already how the scanner works). A cluster becomes a candidate once it meets that hazard's minimum cell count.
- **Widespread** means 6 or more cells (about 38,000 km2). **Isolated** means 1 to 5 cells. **Outbreak scale** means 12 or more cells (about 77,000 km2). One constant, tunable.
- **To postcodes and sectors:** a postcode joins a cluster if its centre is within 50 km of a firing cell (already built in `proposals.py`). Sectors are the 13 national sectors, taken from the postcodes.
- **Exposure floor:** a cluster needs at least 1,000 residents, or at least 1 active subscriber postcode, or it is logged but not queued. Remote events with no one in them are not Front Line alerts.

### 2.2 Time
Model days are 00Z to 24Z. Windows are converted to each state's local time for the draft message (already built). Lead day 1 is the model day containing "now"; "D3" means lead day 3.

### 2.3 Probability gating by lead day
`P` is the fraction of ensemble members (ECMWF ENS plus GEFS, 82 members) at or above the tier threshold in a cell on that day, or inside that window for multi-day totals. A cluster's `P` is the median of its cells' P.

Until ensembles are ingested (they are not in the current pipeline), use **agreement** as a stand-in: IFS and GFS both at or above the threshold scores 1.0, one of them scores 0.5, neither scores 0. The gate table below applies to either measure.

| Lead | Minor | Moderate | Significant | Major | Highest tier allowed |
|---|---|---|---|---|---|
| D1-3 | P 50% | P 40% | P 30% | P 20% | Major |
| D4-5 | P 55% | P 45% | P 35% | P 25% | Major, shown as "watch" |
| D6-7 | P 60% | P 50% | P 40% | P 30% | Significant (Major capped, reported as Significant with a "potential Major" note) |

Reasoning: rarer events are less predictable, so the bar for them is lower but the review is stricter, and Josh sees the evidence. The cap at D6-7 is for credibility: promising a Major seven days out is how a feed loses trust.

Hazard-specific caps override this table: the model's own storm category cannot reach HIGH beyond D3 or MDT beyond D5 (`MAX_CAT_BY_LEAD`), so severe-storm Significant and Major are D1-3 only, and Moderate (widespread MDT) is D1-5.

### 2.4 Confidence score (0-100) on every prospect
`confidence = 50 * P_tier_gate_margin + 25 * model_agreement + 15 * track_record_hazard_lead + 10 * persistence`, where the margin is how far P clears the gate, agreement is IFS vs GFS vs ECMWF-AIFS spatial overlap, and persistence is the number of consecutive sweeps the cluster has appeared in (capped at 3). Weights are guesses; learn them from Josh's accept/reject history (section 8).

### 2.5 Compound and escalation (the only places a tier is lifted)
Tier lifts are explicit and capped at one tier per prospect. No generic "two hazards add up" rule.

| Condition | Effect |
|---|---|
| Rain at or above Moderate on soil above the 85th percentile (SILO/ERA5 soil moisture) | Heavy-rain hazard lifted one tier (max Significant). Flooding does not use this lift: it applies its own soil conditions (4.3) |
| Tropical cyclone/low landfall within 6 h of a high tide at or above HAT minus 0.2 m | Surge hazard lifted one tier; headline may take the cyclone tier |
| Tropical cyclone with rain at Significant or above in the same cluster | Cyclone tier lifted one tier (max Major) |
| Cyclone category 1-2 crossing within 50 km of a city with 100,000+ people | Cyclone lifted one tier |
| Fire: Extreme rating, Tmax anomaly +10 C or more, gusts 60 km/h+, wind change within the burn window | Fire lifted one tier |
| Heat at Moderate or above, with dry fuels and gusts 50 km/h+ | Spawns a fire prospect at Moderate minimum |
| Dry lightning (lightning, under 2.5 mm rain) on Extreme fire rating | Fire raised to Moderate minimum |
| Cold front gust at or above 90 km/h on days after 25 mm+ rain on saturated soil | Wind lifted one tier (tree-fall risk) |

### 2.6 De-duplication and the headline tier
Hazards overlapping in the same cluster and window merge into one prospect (`merged_hazards`). Rules:
1. Headline tier is the highest tier among the merged hazards after lifts.
2. Headline hazard is that tier's hazard; ties are broken by this impact order: cyclone, flood, fire, severe thunderstorm, damaging wind/cold front, storm surge/waves, heatwave, heavy rain, snow, frost.
3. The draft message names every merged hazard and gives the headline one first.
4. Windows merge when they overlap or are within 12 hours of each other.
5. Different clusters of the same hazard on the same day are different prospects unless within 300 km.

### 2.7 Persistence across sweeps
Prospect identity is `hazard family + cluster centroid (within 1.5 degrees) + window overlap of 50% or more`.

| Change in a new sweep | Action |
|---|---|
| Same tier, centroid moved under 1.5 degrees, window moved under 12 h | `updated` silently (no re-review) |
| Tier up | Upgrade after 2 consecutive sweeps, or 1 sweep when lead is 2 days or less |
| Tier down | Downgrade after 2 consecutive sweeps, or immediately when lead is 1 day or less |
| Area grows 1.5x and 3+ cells, or shrinks by half | Queue an update for Josh |
| Window shifts 12 h or more | Queue an update for Josh |
| Below Minor for 2 consecutive sweeps, or window has passed | `cancelled`; if already issued, queue a cancellation draft |

**Hysteresis:** a cluster that is already at a tier stays there until values fall 10% below the threshold, so thresholds do not flip-flop on daily model noise.

### 2.8 Suppression (keeping alerts rare)
Budgets are per postcode, all hazards combined, per rolling 12 months, based on Josh's own frequencies:

| Tier | Budget |
|---|---|
| Minor | 6 |
| Moderate | 3 |
| Significant | 1.5 |
| Major | no cap (alerts are rare by definition) |

- A postcode over budget does not auto-suppress Moderate and above. The prospect goes to the queue flagged `over_budget` so Josh sees it is noisy.
- **Minor over budget is auto-suppressed** and logged, not queued.
- **Quiet period:** after an alert is issued, the same hazard family in the same cluster stays quiet for 48 h unless it upgrades.
- Starting budget by hazard for Minor (tunable): severe thunderstorms 1.5, heavy rain/flood 1.0, fire 1.0, wind/cold front 0.8, heat 0.8, frost/cold 0.4, tropical 0.3, waves 0.2, snow 0.0 (non-alpine postcodes only).

### 2.9 Human sign-off
Nothing leaves the queue without Josh. `approved`, `edited`, `rejected` are recorded with a reason code (section 8). The sweep never sets the live-send flag, never calls the publish function, and never emails subscribers.

## 3. Rainfall: the SILO method (hazard 4, used by flood too)

Josh's framework, made computable:

1. **Data:** SILO daily rainfall, 0.05 degree grid, 1889 to present. Average to forecast cells first (section 2.1).
2. **Baseline:** 1991-2020 for everything that is a "typical year" statistic (Minor, Moderate, Significant). 1900-2025 for the 10-year return level (Major), because 30 years cannot estimate a 1-in-10 well. Run both baselines and report the difference, so you can see how much the answer depends on the period.
3. **Days:** all days, not just wet days (the k-th largest day per year does not care). Days at or above 1 mm are used only as a sanity check.
4. **Windows:** 1-day, 2-day, 3-day, 5-day and 7-day running totals, computed separately (5- and 7-day are used by flooding). A cell can fire on any window; the sweep uses whichever window is most extreme relative to its own threshold. Events are declustered (separated by 3 days; 5 days for the 5- and 7-day windows) before counting.
5. **Tier thresholds (per cell, per window):**
   - Minor: the median across baseline years of that year's 10th-largest total.
   - Moderate: the median across years of the 2nd-largest total.
   - Significant: the median across years of the largest total (the median annual maximum, about a 1.4-year return period).
   - Major: the 10-year return level of the annual maximum series (GEV fitted by L-moments, 1900-2025).
6. **Absolute floors** (24 h mm, by climate zone, see 3.1; scale 1.4x for 48 h, 1.7x for 72 h, 2.2x for 5 days, 2.7x for 7 days). Arid and semi-arid cells where the formula gives less than the floor use the floor and require a higher P gate (add 10 points) because model rain there is poorly constrained.
7. **Absolute caps** (24 h mm, by climate zone, see 3.1), so very wet places stay reachable.
8. **Seasonal stratification:** north of 19 S, the statistic uses November-April only (the monsoon season); elsewhere it uses the full year.
9. **Output file:** `data/rain_thresholds.json`, a table cell x window x tier, built offline once and rebuilt each decade. Floors and caps are applied when it is built, not when the sweep runs.
10. **Forecast side:** the model rainfall (cell mean) is compared with the table. Ensemble probability is the fraction of members at or above the threshold.

### 3.1 Climate zones (used wherever an absolute number appears)

A flat national mm figure is wrong almost everywhere: 60 mm in 3 hours is routine in the Wet Tropics and a flash-flood event in Melbourne. Every absolute rainfall number in this rulebook (rain floors and caps, and the flash-flood route) is therefore looked up by **climate zone**, assigned once per model cell from latitude, longitude and SILO mean annual rainfall (`R`, 1991-2020).

| Zone | Rule (first match wins) | Typical places |
|---|---|---|
| `tropical_wet` | north of 19.5 S and R at least 1,200 mm | Wet Tropics, Top End coast, Cape York east, Kimberley north |
| `tropical_dry` | north of 23.5 S and R 400-1,199 mm | Gulf, Pilbara, dry savanna, Townsville dry belt, Mackay-Rockhampton hinterland |
| `subtropical_east` | 23.5-37 S, east of 149 E, R at least 800 mm | South-east Queensland, Northern Rivers, Mid North Coast, Sydney, Illawarra |
| `temperate` | south of 23.5 S, R at least 400 mm (anywhere not matched above) | Melbourne, Tasmania, Adelaide, south-west WA, Canberra, tablelands |
| `semi_arid` | R 250-399 mm | Mallee, Riverina west, inland Queensland, WA wheatbelt east |
| `arid` | R under 250 mm | Central Australia, Nullarbor, Lake Eyre |

Overlays applied on top of the zone value:
- **Urban impervious:** capital cities and towns over 100,000 people: flash thresholds x 0.8 (drains and hard surfaces flood sooner).
- **Alpine and high ranges** (elevation at least 1,200 m): use the `temperate` flash row, then x 1.3 (orographic rain is more efficient and expected).

Numbers below are indicative and were tripled on 19 Oct 2026 (the pre-tripling values were one third of those shown). The proper source for the flash route is the **BoM Intensity-Frequency-Duration (IFD 2016) design rainfall data**, which is published for download as free design-rainfall depth grids (check the current terms before relying on it): for each cell take the **3-hour depth** at the target frequency, then multiply by an **areal reduction factor of 0.85** because the model gives a 0.25 degree (about 28 km) cell mean, not a point. The zone table is the fallback, and also the sanity check on the IFD values.

**Flash-flood 3-hour rain, point-equivalent mm, tripled at Josh's request on 19 Oct 2026** (before the 0.85 factor). The original frequency labels (about 1 per year, 1-in-5, 1-in-20, 1-in-100) no longer apply: tripled depths are far rarer than those design rainfalls, and the Major values in the wet zones (600 mm in 3 hours) exceed anything observed in Australia, so those cells will effectively never fire at Major. Re-check against BoM IFD before using:

| Zone | Minor | Moderate | Significant | Major |
|---|---|---|---|---|
| `tropical_wet` | 210 | 330 | 450 | 600 |
| `tropical_dry` | 165 | 270 | 375 | 510 |
| `subtropical_east` | 135 | 240 | 345 | 480 |
| `temperate` | 54 | 96 | 135 | 195 |
| `semi_arid` | 66 | 114 | 165 | 240 |
| `arid` | 60 | 105 | 150 | 225 |

The original values were impact-based (roughly drainage failing, flash flooding of low areas, serious flooding, very rare). After tripling they are well above that, so expect very few flash prospects. The alert-frequency targets in section 8 are met after gating, not by the raw depth.

**Daily-rain absolute floors and caps by zone** (24 h mm, tripled at Josh's request on 19 Oct 2026; scaled by the window factors in section 3; these replace the single national numbers):

| Zone | Floors: Minor / Moderate / Significant / Major | Caps: Minor / Moderate / Significant / Major |
|---|---|---|
| `tropical_wet` | 120 / 210 / 330 / 600 | 750 / 1200 / 1650 / 2400 |
| `tropical_dry` | 90 / 165 / 270 / 480 | 600 / 960 / 1350 / 1950 |
| `subtropical_east` | 90 / 150 / 240 / 420 | 450 / 750 / 1050 / 1650 |
| `temperate` | 60 / 90 / 135 / 240 | 240 / 390 / 600 / 960 |
| `semi_arid` | 60 / 90 / 135 / 240 | 240 / 390 / 600 / 960 |
| `arid` | 45 / 75 / 120 / 210 | 210 / 360 / 540 / 900 |

Other absolute numbers that are already regional: heat floors (temperate south vs tropical north), wave heights (east vs south/west), frost (crop zones by region), and fire (percentile first, region-specific fuels). Wind gust floors stay national (90 / 110 / 125 / 160 km/h) because they are damage thresholds for buildings, not climate-relative, and each is also gated by the cell's own gust percentile.

## 4. Per-hazard specification

For each hazard: variables, tier thresholds, floors/caps, duration and extent, lead and confidence, regional and seasonal rules, compounds, public descriptors, and calibration events. The shared rules in section 2 apply on top; where a hazard needs something different it says so.

### 4.1 Tropical cyclones and tropical lows

- **Variables:** forecast category at the closest approach or landfall (Australian scale), ensemble genesis probability of reaching 34 kt, track distance to the nearest populated place, rain and surge footprint. Data already in the app: `TropicalSystem` (GEFS member tracks, ensemble genesis area, consensus track and cone).
- **Australian category scale** (maximum sustained wind / maximum gust): Cat 1 under 89 km/h / under 125; Cat 2 89-117 / 125-164; Cat 3 118-159 / 165-224; Cat 4 160-199 / 225-279; Cat 5 200+ / 280+.
- **Josh's tiers, unchanged:** Minor = tropical low; Moderate = Cat 1; Significant = Cat 2-3; Major = Cat 4-5.
- **Triggers in numbers:**
  - Minor: a tropical low or invest with at least 30% of members reaching 34 kt within 7 days **and** the consensus track within 300 km of the Australian coast or a populated island within 5 days.
  - Moderate: at least 40% of members at Cat 1 or higher with the centre within 300 km of a populated cell.
  - Significant: at least 35% of members at Cat 2 or higher within 300 km, or Cat 3 within 400 km (large cyclones).
  - Major: at least 25% of members at Cat 4 or higher within 400 km, and D5 or sooner.
- **Floors/caps:** a system staying more than 500 km offshore with no forecast approach does not fire. A weak system more than 300 km away fires at Minor at most.
- **Duration/extent:** the window runs from gale-force onset at the first populated cell to the last. Minimum footprint is the forecast gale radius (R34) reaching a cell with residents.
- **Lead/confidence:** gates in 2.3 plus a track gate: the 90th-percentile cone must reach the populated cell. D6-7 intensity is capped at Moderate; genesis-only Minor can appear at D7.
- **Region/season:** the cyclone season is November-April (peak January-March). Minor is allowed outside the season only with 50% genesis probability. Southern tropical-low and ex-cyclone rain events are handled by hazards 3 and 4.
- **Compounds:** section 2.5 (tide, rain, city).
- **Descriptors:**
  - Minor: "A tropical low is worth watching this week; it may develop, and I'll update you as the track firms up."
  - Moderate: "A Category 1 cyclone is on track to affect your area; now is the time to check your preparations."
  - Significant: "A Category 2-3 cyclone is expected near you; this is the type of system that damages homes, so please prepare properly."
  - Major: "A Category 4-5 cyclone is forecast to impact your area; this is dangerous, and I'd want you well prepared before it arrives."
- **Calibration events:** Tracy, Darwin, Dec 1974, Cat 4, Major (`ok`). Yasi, Mission Beach, Feb 2011, Cat 5, Major (`ok`). Debbie, Airlie Beach/Bowen, Mar 2017, Cat 4, Major (`ok`). Seroja, Kalbarri, Apr 2021, Cat 3, Significant (`ok`). Kirrily, near Townsville, Jan 2024, Cat 2, Significant (`verify` landfall category). Jasper, Far North Queensland, Dec 2023, Cat 2 with more than 1 m of rain, Major through the rain compound (`ok`). Alfred, Brisbane area, Mar 2025, weakening to Cat 1 / low at crossing near a 3.5-million-person metro, Moderate lifted to Significant by the city rule (`verify`).

### 4.2 Severe thunderstorms

- **Variables (already in the model output):** categorical outlook `cat` (0 none, 1 TSTM, 2 MRGL, 3 MDT, 4 HIGH), hail tier (1 small, 2 3-6 cm, 3 6 cm+), wind tier (1 90+, 2 125+, 3 160+ km/h), SCP, STP, SHIP, CAPE, shear, DCAPE, rain rate. Only MDT and HIGH trigger.
- **Tiers on Josh's scheme:**
  - Minor: MDT (cat 3) in 1 or more cells, with CAPE at least 1,000 J/kg and 0-6 km shear at least 25 kt.
  - Moderate: widespread MDT (6+ cells), or isolated HIGH (1-5 cells).
  - Significant: HIGH widespread (6+ cells), or any cell with hail tier 3, wind tier 3 or STP at least 4.
  - Major: HIGH over 12+ cells, with two top-tier hazards present (for example hail 6 cm+ **and** wind 125 km/h+, or a tornado environment plus large hail), and at least 1,000,000 people in the footprint at P of 50% or more; or 3 consecutive days of Significant in the same sector.
- **Floors/caps:** none beyond the model's own category gate. The CAPE and shear floors only guard against a model category produced on thin ingredients.
- **Duration/extent:** a day is a unit. A multi-day outbreak is evaluated per day and merged when 2 or more consecutive days hit.
- **Lead/confidence:** HIGH only D1-3; MDT up to D5. Significant and Major are D1-3 only. Gate by P per 2.3, but at D4-5 convection is not ensemble-resolved, so use agreement and cap at Moderate.
- **Region/season:** the thresholds are the same everywhere. Season is handled by the sector budgets (2.8), not by changing the numbers.
- **Compounds:** flash-flood rate over 50 mm/h in a MDT cell and soil above 85th percentile creates a flood prospect (hazard 3).
- **Descriptors:**
  - Minor: "A day of organised severe storms is possible near you; keep an eye on the sky and tidy up loose items."
  - Moderate: "A more significant storm day is on the cards, with a good chance of damaging hail or winds in your area."
  - Significant: "This is the kind of storm day we see only once or twice a year; please protect your car, your roof and yourselves."
  - Major: "This is an exceptional storm outbreak that could hit a lot of people with giant hail and destructive winds; I'd want you prepared well in advance."
- **Calibration events:** Sydney hailstorm, 14 Apr 1999, Major (`ok`). Perth hailstorm, 22 Mar 2010, Significant to Major (`ok`). Brisbane hail and wind, 27 Nov 2014, Significant (`ok`). Canberra hailstorm, 20 Jan 2020, Significant (`ok`). Minor and Moderate examples: any routine MDT day in south-east Queensland (use the archived outlooks to pick them).

### 4.3 Flooding (riverine and flash), built from rainfall only

**Design rule (Josh, 19 Oct 2026): no BoM flood data.** BoM gauge forecasts and flood-class warnings are paywalled, so nothing in this hazard depends on them. A flood prospect is derived from rainfall, antecedent wetness and catchment shape, all from free sources: the model forecast, SILO and ERA5, and the Geoscience Australia hydrological geofabric (free, CC-BY) for basin outlines. The sweep never reads a river height. Where BoM issues its own warning, that is outside this system and is not a trigger or an input.

- **Variables:**
  - `rain_72h`, `rain_120h`, `rain_168h` (cell-mean model totals) as ratios to the section 3 table for the same window.
  - `rain_peak_3h` (mm, the wettest 3 hours in the cell-mean forecast) against the zone's 3-hour table in 3.1, for flash flooding.
  - `soil_moisture_percentile` (SILO or ERA5 top-layer soil moisture against that cell and calendar month, 1991-2020).
  - `api30_percentile`, the antecedent rainfall index: 30-day exponentially weighted rainfall (decay 0.85 a day) as a percentile for the cell and month, from SILO observed rain up to yesterday.
  - `basin_mean_rain_168h_ratio`, the area-weighted forecast rain over the whole upstream catchment, which is what drives slow inland rivers.
  - `catchment_response` flag per cell: steep and coastal (fast), urban impervious (fast), flat inland (slow), taken from the geofabric once and stored.
- **Three routes, any one can fire a tier:**
  - **Flash route (D1-3):** short, intense rain on a fast-responding catchment, **using the zone and urban-overlay 3-hour table in 3.1, not one national number**.
  - **Event route (D1-7):** 72 h or 120 h rain against the section 3 thresholds, made worse by wet soil.
  - **Basin route (D2-7):** whole-catchment rain over 5-7 days for slow rivers (Murray-Darling, Fitzroy, Burdekin and similar), where the peak arrives days after the rain.
- **Tiers:**
  - **Minor:** flash route: `rain_peak_3h` at or above the zone's Minor 3-hour value (3.1, after the 0.85 factor and urban overlay) over 3 or more cells on fast catchments; or event route: `rain_72h` at or above the Minor threshold with `soil_moisture_percentile` at least 70; or basin route: basin mean 7-day rain at or above the Moderate threshold with `api30_percentile` at least 70.
  - **Moderate:** event route: `rain_72h` at or above the Moderate threshold with soil at least 85; or `rain_120h` at or above Moderate with `api30_percentile` at least 85; or flash route: `rain_peak_3h` at or above the zone's Moderate 3-hour value on fast catchments. Significant and Major also have flash routes: at or above the zone's Significant and Major values respectively.
  - **Significant:** event route: `rain_72h` at or above the Significant threshold with soil at least 85; or Moderate with soil at least 95 (saturated); or basin route: basin mean 7-day rain at or above Significant with `api30_percentile` at least 85.
  - **Major:** event route: `rain_72h` at or above the Major threshold with soil at least 85, over 2 or more cells; or Significant with soil at least 95 over 12 or more cells (outbreak scale); or 3 consecutive days each at Significant level on the same basin.
- **Floors/caps:** inherits the rain floors and caps in section 3, so a dry-country cell cannot fire on a nuisance total. The flash route uses the zone table in 3.1 (IFD-based where available). A cell with no residents downstream (basin outlet with no subscriber postcodes or 1,000 residents) is not queued.
- **Duration/extent:** the window runs from the first rain at the tier level to 36 hours after the last (slow catchments 5 days after). Minimum 3 cells for Minor and Moderate, 2 for Significant and Major.
- **Lead/confidence:** the flash route is only D1-3 (hourly rain is not trusted beyond) and, because the model smooths bursts, it is a guide: it can underestimate, so it should fire rarely and be read with the event route. Event route to D7 with the section 2.3 gates; basin route is more reliable at D3-7 than D1 because the basin average smooths model error. At D6-7 cap at Significant unless soil is at least 95.
- **Region/season:** tropical north, wet season (Nov-Apr) uses the monsoon-trough statistics from section 3. Southern cool-season soils run wetter, so the soil percentile is taken per calendar month, never annual. Urban areas on fast catchments use the lower flash floors.
- **Compounds:** soil lift (2.5) already uses the same soil test, so do not count it twice: the soil conditions above replace the lift for flood, and the lift still applies to rain hazard. Cyclone rain (2.5) and surge tide (2.5) apply.
- **Merge:** flood is the headline when it fires; heavy rain in the same cluster becomes evidence.
- **Limits, stated plainly:** with no river gauges the system cannot know where water actually goes, how levees or dams behave, or timing at a specific town. It warns that the **rain and ground conditions are flood-producing**, not that a named river will reach a named height. The draft message must say so in words like "rainfall like this on ground this wet often leads to flooding", never "the river will peak at". Josh can add the local detail by hand.
- **Descriptors:**
  - Minor: "Heavy rain on already damp ground could bring minor flooding in low-lying areas and some roads; it's a good time to check your drains and move valuables."
  - Moderate: "Rain on very wet ground raises the risk of moderate flooding, with closed roads and water over low areas."
  - Significant: "This is a serious flood setup: heavy rain on saturated ground, the kind of combination that has flooded homes, so please prepare early."
  - Major: "Rainfall and ground conditions like these are linked to major, widespread flooding; I'd want you ready to move your family and belongings if you need to."
- **Calibration events** (check with SILO rainfall, not gauges): Brisbane and Ipswich, Jan 2011, Major (`ok`). Lismore and the Northern Rivers, Feb-Mar 2022, Major (`ok`). Townsville, Jan-Feb 2019, monsoon, Major (`ok`). Central West NSW, Oct-Nov 2022, Major on saturated soil (`ok`). Northern Victoria, Oct 2022, Significant (`verify`). Test target: each of these should reach its tier from 72 h and 120 h SILO rainfall plus a pre-event soil percentile at 95 or above, with no gauge data.

### 4.4 Significant rainfall

- **Variables and method:** section 3, windows 24, 48 and 72 h, thresholds Minor 10th-wettest day, Moderate 2nd-wettest, Significant median annual maximum, Major 10-year level.
- **Extent:** at least 5,000 km2 (one cell) for Minor and Moderate; at least 2 cells for Significant; Major can be 1 cell if the 10-year level is exceeded by 25% or more, otherwise 2 cells.
- **Lead/confidence:** gates in 2.3; at D6-7 cap at Significant.
- **Region/season:** north of 19 S use wet-season statistics; arid and semi-arid cells use the floors and the P+10 gate; the Pilbara and Kimberley cyclone rain is handled together with hazard 1.
- **Merge:** rain alone is a prospect, but if a flood prospect exists in the same cluster the rain becomes evidence and the flood hazard takes the headline.
- **Descriptors:**
  - Minor: "A good soaking is on the way: one of the wetter days you'll see this year."
  - Moderate: "A very wet day or two is forecast, in the top couple of falls you'd see in a typical year."
  - Significant: "This looks like your wettest day of the year, with a real chance of flash flooding."
  - Major: "A rainfall total like this comes around about once in ten years; please prepare for flooding."
- **Calibration events:** Pasha Bulker east coast low, Newcastle, Jun 2007, Significant (`verify` totals). Mid North Coast and Hunter, Mar 2021, Significant to Major (`ok`). Jasper rain, Cairns region, Dec 2023, Major (`ok`). South-east Queensland and Northern Rivers, Feb-Mar 2022, Major (`ok`).

### 4.5 Cold fronts (high-profile only)

- **Variables:** 24 h Tmax change (C), 10 m gust (km/h), 6 h MSLP fall (hPa), 850 hPa wind (kt), snow level (m), coverage across SWWA, SA, VIC, TAS, NSW. A "high-profile front" is defined as 2 or more of these 4 signals at once:
  1. gust at least 90 km/h at 6+ cells
  2. Tmax falls at least 12 C in 24 h at 3+ cells
  3. MSLP falls at least 10 hPa in 6 h at 3+ cells
  4. snow level at or below 800 m
- **Tiers:**
  - Minor: 2 of the 4 signals.
  - Moderate: gust at least 100 km/h and Tmax drop at least 14 C over 6+ cells, plus snow level at or below 600 m in the south-east or Tasmania.
  - Significant: wind tier 2 (125 km/h+) at 3+ cells with the front, or 850 hPa wind at least 60 kt over a populated region.
  - Major: wind tier 3 (160 km/h+), or a gale band at least 1,000 km wide with gusts 100 km/h+ over a capital city.
- **Floors/caps:** gusts must also reach P99 of the cell's cool-season gust climatology (ERA5, 1991-2020), so windy places such as the Bass Strait coast and west coast do not fire on ordinary frontal days.
- **Duration:** at least 3 hours at gust at 80% of the tier value.
- **Lead/confidence:** cold fronts are well resolved, so D1-5 uses the 2.3 gates and D6-7 is capped at Moderate.
- **Region/season:** cool season (Apr-Oct) in the south; SWWA winter fronts use the 850 hPa wind route; early-season fronts after heat can be flagged as compound with fire weather (2.5).
- **Compounds:** wet and windy (tree-fall) lift in 2.5; heat then front: fire wind change lifts fire.
- **Descriptors:**
  - Minor: "A strong cold front crosses tomorrow, with a sharp drop in temperature and gusty winds."
  - Moderate: "A powerful front brings damaging gusts and a big temperature drop, with snow to low levels in the ranges."
  - Significant: "This is a very strong front that can bring down trees and power lines, so please secure what you can."
  - Major: "A destructive front is forecast across a wide area; this is an event to prepare for well ahead."
- **Calibration events:** South Australian statewide storm and blackout, 28 Sep 2016, Significant (`ok`). Victorian wind storm in the Dandenongs and Gippsland, 9-10 Jun 2021, Significant (`ok`). Major example: none I can name with confidence; treat Major as a rare rule to test in backtest (`verify`).

### 4.6 Damaging wind (non-convective)

- **Variables:** hourly 10 m wind gust (km/h) and sustained wind from IFS (not in the current pipeline: needs `wind_gusts_10m`), cell gust climatology from ERA5 1991-2020 (hourly), duration, exposure.
- **Tiers (need both an absolute and a percentile):**
  - Minor: gust at least 90 km/h and at or above the cell's P99.5 hourly gust.
  - Moderate: gust at least 110 km/h and at or above P99.8.
  - Significant: gust at least 125 km/h and at or above P99.95 (about 4 hours a year).
  - Major: gust at least 160 km/h coastal (140 km/h inland) and at or above P99.99.
- **Duration/extent:** at least 3 consecutive hours at 80% of the tier value, at 3+ cells (Minor and Moderate) or 2+ (Significant and Major); the exposure floor in 2.1 applies.
- **Lead/confidence:** gates in 2.3; gust is a post-processed model field, so require both IFS and GFS to reach at least the tier below before issuing the tier.
- **Region/season:** coastal tropics use the cyclone hazard instead (no double alert); alpine tops excluded (no residents). East coast lows (Apr-Sep) are prime; SWWA and SA winter fronts overlap hazard 5 and merge.
- **Compounds:** wet and windy lift (2.5); wind after bushfire damage is a separate escalation Josh can add by hand.
- **Descriptors:**
  - Minor: "Gusty winds to around 90 km/h are forecast; a good day to secure the trampoline and park away from trees."
  - Moderate: "Damaging winds are forecast, in the strongest few events you'd see each year."
  - Significant: "Destructive winds of 125 km/h or more are possible; this is serious, and I'd want you sheltered and prepared."
  - Major: "A once-in-a-decade wind event is forecast for your area; please treat this as dangerous."
- **Calibration events:** Pasha Bulker east coast low, Jun 2007, Significant (`verify` gusts). Dungog and Hunter east coast low, Apr 2015, Significant (`ok`). 1998 Sydney-Hobart storm, Bass Strait, offshore Major (`verify`, marine). Victoria, Jun 2021, Significant (`ok`).

### 4.7 Heatwaves

- **Josh's rule (carried over exactly):** daily maximum above the 1991-2020 average for that calendar day by **+12 / +14 / +17 / +21 C** for Minor / Moderate / Significant / Major, on at least **2 consecutive days**, with the overnight minimum also above its average on those days.
- **Extra gates I added for rarity:**
  - Absolute Tmax floors (C), temperate south: 35 / 38 / 41 / 44. Tropical north (anomaly unreliable because variability is small): 38 / 40 / 42 / 45.
  - Major needs 3 or more consecutive days.
  - Early-season weighting: in Sep-Nov, subtract 2 C from the anomaly and the floor for every tier ("early heat weighted higher").
- **EHF cross-check:** compute the Excess Heat Factor (Nairn and Fawcett). If EHF is at or above 3 times the cell's EHF85 (BoM's "extreme"), the day is at least Significant; if also 4 or more days and a capital city is in the footprint, it is Major. EHF can only lift a tier, never lower the anomaly tier.
- **Extent:** 3+ cells for Minor and Moderate, 2+ for Significant, 1+ for Major (a capital city cell alone qualifies).
- **Lead/confidence:** heat is well forecast: D1-7 uses 2.3, no cap other than the D6-7 Major cap.
- **Compounds:** heat at Moderate or above with dry fuels and gusts 50 km/h+ spawns a fire prospect (2.5); heat with very warm nights raises health impact, noted in the message but not a tier lift.
- **Descriptors:**
  - Minor: "A few hot days well above average are on the way; plan to keep cool and check on neighbours."
  - Moderate: "A proper heatwave is forecast, with no real overnight relief; please prepare for several hot days."
  - Significant: "This is a dangerous heatwave, of the kind we see about once a year; look after yourself and the vulnerable."
  - Major: "An extreme, record-threatening heatwave is forecast; this carries serious risk to life, so please prepare early."
- **Calibration events:** south-east Australia heatwave, Jan-Feb 2009, Melbourne and Adelaide, Major (`ok`). Adelaide 46.6 C, 24 Jan 2019, Major (`ok`). Australia-wide record mean maximum, 17-18 Dec 2019, Significant to Major (`ok`). Western Sydney 47.3 C, 7 Jan 2018, Significant (`ok`).

### 4.8 Fire weather

- **Variables:** AFDRS fire behaviour index and rating (Moderate / High / Extreme / Catastrophic), FFDI (forest, 50 severe, 75 extreme, 100 catastrophic in the old scale), GFDI (grass, with curing), Drought Factor, KBDI, soil moisture, Haines or C-Haines, wind change timing, dry lightning. The current pipeline already carries `fire` tier and `ffdi`.
- **Local percentile first:** each cell's FFDI is compared with its own fire-season distribution (ERA5 or BARRA reanalysis, 1991-2020), so a day normal in the NT interior does not alert.
- **Tiers on Josh's scheme (isolated vs widespread):**
  - Minor: AFDRS **Extreme** in 1 or more fire weather district for at least 1 day, with FFDI at least 50 and at or above P97 of the cell's fire-season climate.
  - Moderate: Extreme **widespread** (6+ cells, or 3+ districts), or **Catastrophic** isolated (1-5 cells), with FFDI at least 75 and at or above P99.
  - Significant: **Catastrophic** widespread (6+ cells), or Catastrophic in any district with C-Haines at least 10 and a forecast wind change in the afternoon, FFDI at least 100 and at or above P99.5.
  - Major: Catastrophic over 12+ cells, on 2 or more consecutive days, or touching a metropolitan bushland interface (Melbourne, Sydney, Perth, Adelaide Hills) with Tmax at least 40 C, Drought Factor 10, KBDI at least 150 and a gusty wind change.
- **Floors/caps:** fire weather only counts in a fire district with at least 5,000 residents, or 1 or more subscriber postcode, or a major fuel-and-asset interface, so remote spinifex fires do not queue.
- **Duration:** the burn window is Tmax-hour to the wind change plus 3 h.
- **Lead/confidence:** fire weather is predictable to D5; D6-7 caps at Moderate. Wind-change timing uncertainty is reported as a note.
- **Region/season:** forest fire season by region (SE Oct-Mar, WA southwest Nov-Apr, Tropical north grass fires Jun-Nov); grassland uses GFDI with curing at least 80%; bushfire-prone forest uses FFDI.
- **Compounds:** section 2.5 (heat and wind, dry lightning, wind change).
- **Descriptors:**
  - Minor: "Fire danger will be Extreme near you; please make sure your fire plan is ready."
  - Moderate: "Extreme fire danger across a wide area, with Catastrophic risk in places; this deserves real attention."
  - Significant: "Catastrophic fire danger is forecast, the type of day that puts homes and lives at risk; please leave early if your plan says so."
  - Major: "This is a rare and truly dangerous fire weather event across multiple regions; act early and follow your fire plan."
- **Calibration events:** Black Saturday, 7 Feb 2009, Victoria, Major (`ok`). Black Summer peak days, 8 Nov, 21 Dec and 31 Dec 2019, and 4 Jan 2020, Major (`ok`). Wye River and Separation Creek, 25 Dec 2015, Significant (`verify`). Ash Wednesday, 16 Feb 1983, Major (`ok`, pre-AFDRS).

### 4.9 Cold impacts and frost

- **Variables:** daily minimum temperature at 2 m (SILO, ERA5 for forecast-side climatology), grass-minimum is not available so use screen temperature at or below 2 C as the ground-frost proxy, duration (nights), crop stage by region and month, wind chill for livestock.
- **Where it counts:** two masks, both built from ABARES-style cropping zones (SWWA wheatbelt, SA mid-north/Eyre/Mallee, Victorian Mallee/Wimmera, NSW central and western plains, Darling Downs and Central Queensland) for crops, and the southern tablelands and Tasmania for livestock, plus urban cold-outbreak for non-farm postcodes.
- **Crop frost tiers (August to October only; flowering and grain fill):**
  - Minor: Tmin at or below 0 C for 2 or more nights and at or below the cell's P2 for the date.
  - Moderate: Tmin at or below -2 C for 2 or more nights in Sep-Oct.
  - Significant: Tmin at or below -3 C for 2 or more nights in Sep-Oct over 6+ cells.
  - Major: Tmin at or below -5 C, or below the station record by 1 C or more, over 12+ cells in any month from August to November.
- **Livestock cold snap (Tas, Vic, NSW tablelands, within 2 weeks of shearing season):** Tmax at or below 10 C with rain and sustained wind at least 40 km/h for 24+ hours: Moderate; add snow below 600 m: Significant.
- **Urban cold outbreak (outside crop mask):** Tmax anomaly at or below -10 C for 2 or more days with Tmin at or below P1: Minor; Tmin at or below station record: Moderate. No Significant or Major (use snow and wind hazards).
- **Lead/confidence:** frost is hard to resolve in 80 km cells; use IFS minimum temperature minus 2 C as the expected ground frost, require IFS and GFS agreement, and cap tier at Moderate beyond D4.
- **Descriptors:**
  - Minor: "A couple of cold, clear nights are coming, and a frost is likely in low-lying paddocks."
  - Moderate: "Hard frosts to around -2 C are forecast during a sensitive crop window; I'd be checking your frost plans."
  - Significant: "Damaging frosts are forecast across a wide area at a critical time for crops; please prepare and protect where you can."
  - Major: "Record-threatening cold is forecast over a large region; this could be very damaging."
- **Calibration events:** all marked `verify`, because I am not confident enough on dates: widespread spring frosts across eastern cropping regions in September, in more than one recent year. Use BoM monthly climate summaries and GRDC frost-event notes to pick 2-3 and set the Moderate and Significant lines.

### 4.10 Snow and blizzards

- **Josh's rules (carried over exactly):** Moderate 50 cm or more of snowfall, Significant 100 cm or more, both within any 72-hour window. Alpine and non-alpine are separated.
- **Variables:** model snowfall (cm, summed over 72 h, rolling), snow level (m), wind and visibility. The model-run pipeline does not carry snowfall; the extra-hazards module already fetches it.
- **Tiers:**
  - Minor (my addition): snow level at or below 800 m **and** at least 5 cm forecast at a place with residents above 600 m elevation (Oberon, Armidale, Ballarat hills, Tasmanian Midlands) in non-alpine postcodes only.
  - Moderate (Josh): at least 50 cm in 72 h at one or more alpine cell (elevation at least 1,200 m).
  - Significant (Josh): at least 100 cm in 72 h.
  - Major (my addition): at least 150 cm in 72 h, or blizzard conditions over a main alpine road for 6+ hours with snow level at or below 300 m.
- **Blizzard conditions:** sustained wind at least 56 km/h or gusts at least 90 km/h, with at least 10 cm in 24 h, and visibility under 100 m (a proxy: wind-blown snow depth); confirm the BoM definition before coding.
- **Duration/extent:** one alpine cell is enough for Moderate and above; Minor needs a populated place.
- **Lead/confidence:** snow depth is poorly predicted. Cap at Moderate beyond D4; require snowfall at or above the tier in IFS and GFS.
- **Region/season:** alpine NSW and Victoria, Tasmania highlands; May to October.
- **Descriptors:**
  - Minor: "Snow is forecast to unusually low levels, and it could settle in some hill towns."
  - Moderate: "A major alpine snowfall is coming, around half a metre or more; roads will be affected."
  - Significant: "A metre or more of snow could fall; conditions will be dangerous and travel will be cut off in places."
  - Major: "An extreme blizzard is forecast; please avoid the alps and prepare for isolation."
- **Calibration events:** all `verify`: early-season and winter snow dumps in the Snowy Mountains and Victorian Alps, and Tasmania low-level snow outbreaks. Pull the biggest 72 h resort snowfall records from Perisher, Thredbo, Falls Creek and Mt Hotham snow depth logs before setting the real depth lines.

### 4.11 Significant waves and storm surge

- **Variables:** significant wave height Hs (m) and peak period Tp (s) from ECMWF WAM (via Open-Meteo Marine) and its hindcast climatology (ERA5/CAWCR 1979-2020), wave direction, storm surge residual (m), predicted tide relative to Highest Astronomical Tide (HAT).
- **Direction gate:** onshore within 45 degrees of the coast normal, and Tp at least 10 s for erosion risk.
- **Tiers (need both percentile and absolute, east coast shown first, south/west coasts in brackets):**
  - Minor: Hs at or above the cell's P99 and at least 3.5 m (5.0 m).
  - Moderate: P99.7 and at least 4.5 m (6.5 m).
  - Significant: P99.9 and at least 6.0 m (8.5 m); or storm tide (surge plus tide) above HAT.
  - Major: P99.99 and at least 8.0 m (12.0 m); or storm tide at least 0.5 m above HAT with onshore waves at least 4 m.
- **Duration/extent:** at least 6 hours at 80% of the tier value; coastal communities within 20 km with at least 5,000 people, or a named erosion hot spot (Collaroy-Narrabeen, Gold Coast, Byron, Phillip Island).
- **Lead/confidence:** waves are well predicted to D5; cap at Significant at D6-7. Surge needs a deterministic storm-surge model (not currently in the pipeline).
- **Compounds:** king tide plus Hs at or above P99 and predicted tide within 0.2 m of HAT lifts one tier; cyclone landfall near high tide (2.5).
- **Descriptors:**
  - Minor: "Large, powerful surf is forecast; stay off rock platforms and keep well clear of beaches in the afternoon."
  - Moderate: "Very large waves are coming, with beach erosion and dangerous surf; please avoid the water."
  - Significant: "Destructive waves and a high tide could cause coastal flooding and erosion; please keep away from the shoreline."
  - Major: "Extreme waves and a storm tide are forecast; this could damage coastal property badly."
- **Calibration events:** East coast low and Collaroy-Narrabeen erosion, 4-6 Jun 2016, Significant (`ok`). Yasi storm surge, Tully Heads, Feb 2011, Major (`ok`). Alfred, Gold Coast and Moreton Bay swell, Mar 2025, Significant (`verify`).

## 5. Machine-readable files

- `rulebook.json`: every threshold as rows with the keys you asked for: `hazard, tier, variable, operator, value, unit, percentile, return_period_years, duration_hours, min_area_km2, lead_day_max, min_ensemble_prob, region, season, escalation_rules`. I added `rule_id` and `group` (conditions with the same `group` are ANDed; different groups under one tier are ORed) and `source` (where the variable comes from), plus a `global` block holding the shared rules in section 2.
- `prospect.schema.json`: JSON Schema (draft 2020-12) for the review-queue record.

## 6. Prospect record (summary; schema in `prospect.schema.json`)

`id`, `sweep_run_time`, `hazard`, `tier`, `confidence`, `valid_from`, `valid_to`, `postcodes`, `sectors`, `geometry`, `evidence`, `draft_message`, `status` (pending / approved / edited / rejected / issued / updated / cancelled), `reviewer_notes`, `supersedes_id`, plus `merged_hazards`, `lead_day`, `ensemble_p`, `over_budget`, `escalations_applied`, `rejection_code`, `edit_diff` so every decision can be logged and learned from.

## 7. Data sources and ingestion before the 03:00 AEDT run

At 16:00 UTC (03:00 AEDT) these are available; everything else is slower.

| Need | Source | Ingest | Status today |
|---|---|---|---|
| Deterministic forecast, days 1-10 | ECMWF IFS 00Z via Open-Meteo (keyed) | already used | In pipeline (12Z); add 00Z for the 03:00 run |
| Second opinion | GFS 06Z via Open-Meteo | already used | In pipeline |
| Ensembles (probability gates) | ECMWF ENS 00Z and GEFS 06Z via Open-Meteo ensemble API or ECMWF open data | cell-level exceedance fractions | Not yet: biggest data gap |
| Wind gusts | IFS `wind_gusts_10m` | add to cell fetch | Not in pipeline |
| Snowfall, snow level | Open-Meteo `snowfall`, freezing level | already in `extra_hazards.py` | Built, not running |
| Heat anomalies | forecast Tmax/Tmin plus SILO/ERA5 climatology table | `build_climatology.py` | Built, not run (needs the workflow) |
| Rain climatology and zones | SILO gridded daily rainfall (public S3 or Long Paddock API), averaged to cells; mean annual rain to assign climate zones | one-off build of `data/rain_thresholds.json` and `data/climate_zones.json` | Not built |
| Design rainfall (flash-flood depths) | BoM IFD 2016 3-hour depths, free download (confirm current terms); fallback is the zone table in 3.1 | one-off per-cell table | Not built |
| Gust and wave climatology | ERA5 hourly (Copernicus CDS), CAWCR wave hindcast | one-off build | Not built |
| Soil moisture | SILO / ERA5 `swvl1` percentile | daily from the model run or ERA5T | Partly in pipeline |
| Tropical cyclones | App `TropicalSystem` (GEFS tracks, genesis), JTWC/ATCF | scanner reads from the app (private route) | App only |
| Soil wetness and antecedent rain | SILO daily rain (up to yesterday) and ERA5 soil moisture, percentile per cell and month | 30-day antecedent index and soil percentile tables, refreshed daily | Partly in pipeline |
| Catchment outlines and response | Geoscience Australia hydrological geofabric (free) | one-off per-cell basin map and fast/slow flag | Not built |
| Fire danger | Model FFDI (pipeline) plus fire-season percentiles from ERA5; AFDRS ratings only if a free feed is confirmed | percentile table | Pipeline FFDI only |
| Tide and HAT | Free tide predictions or a published HAT table per coastal zone (confirm licence; BoM tide data may be restricted) | table lookup | Not built |
| Postcodes and sectors | `data/postcodes/`, `postcodeSectors` | already built | Built |

**Ordering:** fetch, build per-cell features, evaluate rules, cluster, apply lifts, de-duplicate and merge, match against yesterday's prospects, write the queue, draft messages (Claude drafts, Josh edits). Allow about 20 minutes. If the ensemble fetch fails, fall back to agreement-based gating and mark every prospect `degraded: no ensemble`.

## 8. Validation plan

1. **Climate frequency (offline, no forecast needed).** Using SILO 1900-2025 and ERA5, count at each cell how often each tier would have fired if the forecast were perfect (for rain, heat, wind, frost, waves). Report events per location per year per tier. Targets: Minor 3-6, Moderate about 2, Significant about 1, Major 0.1 or less, all hazards combined. This is an upper bound: probability and area gates will cut it. If a tier is above target, raise its percentile first, then its floor.
2. **Forecast replay (needs archived forecasts).** The scanner has 10 archived raw runs (8-17 Oct 2026). On the current 6-hazard version, the backtest gave about 17 raw events a night before persistence filtering and roughly 2 a night at Moderate and above. This is the only measured queue size; build a longer archive of ensemble forecasts to replace it. Backfill with ECMWF reforecasts or ERA5-as-forecast if archived forecasts are not available.
3. **Event hit rate.** Take the calibration events in section 4. Flood events are tested against SILO rainfall and soil percentiles, never gauges. The rulebook passes if each would have produced a prospect at its listed tier (or one below) at D3 or earlier wherever the hazard is predictable at that lead, and none above. Record the miss list.
4. **Review queue targets.** Design target: at most 3 prospects on an average day nationally, and no more than 8 on a peak day, split about 60% Minor, 25% Moderate, 12% Significant, 3% Major. Josh rejects fewer than 30% of what he sees by the end of the first season.
5. **Tuning knobs (in `rulebook.json` `global`):** `widespread_cells`, `outbreak_cells`, per-tier P gates by lead, tier caps by lead, rain `k` ranks and floors/caps, heat floors and early-season relaxation, fire percentiles, wind percentiles, quiet period, per-location budgets, hysteresis percentage.
6. **Learning from rejections.** Every reviewed prospect stores a `rejection_code` (not-severe-enough, too-wide, wrong-timing, duplicate, low-confidence, already-covered, other) and an `edit_diff` (tier, area, window changes). Weekly report: rejection rate per hazard x tier x lead, plus the median edit to tier and area. Rules for adjusting:
   - If a hazard x tier is rejected more than 50% as not-severe-enough, raise that tier's percentile by 0.05 points (or the floor by 10%).
   - If Josh upgrades a tier in more than 30% of reviews, lower that tier's threshold by the same step.
   - Never auto-change thresholds: the report proposes, Josh accepts.

## 9. Assumptions to challenge

1. Widespread is 6 cells, outbreak is 12.
2. Gate probabilities by lead day (2.3) and the D6-7 Major cap.
3. Rain floors and caps (by climate zone, 3.1) and the zone boundaries.
4. Heat floors and the 2-degree early-season relaxation; the rule that Major needs 3 days.
5. Fire percentiles (P97 / P99 / P99.5) and the exposure floor of 5,000 residents.
6. Wind percentiles (P99.5 to P99.99) and the inland versus coastal split for Major.
7. Snow Minor and Major (my additions), and the 800 m snow-level line for Minor.
8. Annual budgets per location (6 / 3 / 1.5) and the choice to auto-suppress only Minor.
9. Running a 03:00 sweep on 00Z data plus an update after the nightly build.
10. Flood thresholds: soil percentiles 70 / 85 / 95, the zone-by-zone 3-hour flash depths and the 0.85 areal factor, the climate-zone boundaries, and the basin-route windows. No flood-class or gauge data is used anywhere.
