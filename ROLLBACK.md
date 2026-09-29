# Rolling back

Every change since 12 Sep 2026 is either behind a switch or tagged, so nothing needs a
hand-written revert. Work from the cheapest option down.

## 1. Turn a feature off without touching history

Edit one line in `pipeline/build_outlook.R`, commit, and the next run behaves as if the feature
had never shipped. The daily `docs/outlook.json` schema is unchanged by all of these.

| Feature | Line | Off value | Effect when off |
|---|---|---|---|
| Primary model | `MODEL <- "ecmwf_ifs025"` | `MODEL <- ""` | Back to Open-Meteo's best-match blend (ICON upper air, mixed surface) and its 37 levels. API use roughly triples. |
| Second opinion may change the category | `SECOND_OPINION_UNGATES <- MODEL != "ecmwf_ifs025"` | `TRUE` | Lets GFS rain un-gate ECMWF-dry days again. Tested: that recreates coastal-shower storm calls neither model makes. |
| Full 37-level soundings | presence of the `OPENMETEO_KEY` secret | remove the secret | Only applies when `MODEL` is the blend; ECMWF always uses its 12 levels. |
| 3-hourly frames (days 1-8) | `ENABLE_FRAMES <- TRUE` | `FALSE` | No frames computed, no `docs/archive/frames/` written. Run time and `outlook.json` identical to before 14 Sep. |
| Default map zoom | `var FIT_ZOOM_OUT=1;` in `docs/index.html` | `0` | Back to the strict cover fit that crops to the panel. |
| Default pane layout | `var planeMode='dual';` in `docs/index.html` | `'quad'` | Also set `class="mode-dual"` back to `mode-quad` on `#planes` and move the `active` class on the two `.modeBtn` buttons. |
| Default view | `var viewMode='hourly';` in `docs/index.html` | `'daily'` | Opens on the 8-day daily panels instead of the 3-hourly slider. Also move the `active` class on the two `.viewBtn` buttons. |
| Rain trigger | `LEAD_TRIG <- c(2,2,2,2,2,2,2,2)` | (current setting) | Flat 2 mm at every lead. Raising later entries damps storm area at range, but every ladder tried scored below flat on the 55-run record (section 9). |
| Day-2 severe deflation | `LEAD_SEV_K <- c(1.0,0.875,...)` | all `1.0` | Removes the only lead adjustment not backed by observations. |
| Severity ceiling by lead | `MAX_CAT_BY_LEAD <- c(4,4,4,3,3,2,2,2)` | `c(4,4,4,4,4,4,4,4)` | Lets Moderate and High be drawn out to day 8 again. |
| Temperature-scaled TSTM floor | `tstm_floor()` | `return(150)` as the first line | Back to a flat 150 J/kg floor everywhere. |
| Observational verification | the `if (is.null(HIST_DATE))` block at the end of `build_outlook.R` | delete it | Stops scoring runs against gauge data. Costs nothing and touches no forecast value; there is no reason to turn it off except to silence its log. |
| Grid resolution | `data/grid.json` | `git checkout pre-grid-072 -- data/grid.json` | Back to 1032 points at 0.82 deg. Nothing else needs touching: the viewer measures the spacing of whatever grid it loads and rescales its own smoothing constants. Cost falls from ~83% to ~63% of the monthly API budget. |

After switching frames off, the stale files under `docs/archive/frames/` can be deleted; nothing
reads them unless the viewer asks for them.

## 2. Roll the code back to a tagged point

Two tags mark known-good states:

| Tag | What it is |
|---|---|
| `pre-frames` | Everything through the dual-plane default and the wider zoom, before any frame work. |
| `pre-marginal-recal` | Before the Marginal floor was raised to CAPE 1000 / 25 kt and before the hail/wind upgrade. |
| `pre-grid-072` | 1032 points at 0.82 deg, before the grid went to 0.72 deg and the smoothing constants became spacing-relative. |

To put a file back to a tagged state without discarding anything else:

```bash
git checkout pre-frames -- pipeline/build_outlook.R docs/index.html
git commit -m "Roll pipeline and viewer back to pre-frames"
git push
```

To undo one specific commit and keep the history intact:

```bash
git revert <sha>
git push
```

Never force-push `main`. The workflow commits `docs/outlook.json` and `docs/archive` on every run,
so a force-push races the scheduled job and can drop a published outlook.

## 3. Calibration values worth knowing

These are the numbers most likely to need tuning rather than reverting. All live in
`categorise_vals()` and its neighbours in `pipeline/build_outlook.R`.

| Setting | Current | Meaning |
|---|---|---|
| Marginal floor | `cape >= 1000 & shr_kt >= 25` | Plus an SCP route at >3 (supercell baseline), plus large hail or damaging winds |
| Moderate | SCP 6, STP 2, CAPE 1500 + (SHIP 2 or SCP 6), CAPE 3000 in a severe env | Any one route. STP below 2 is ignored entirely |
| High | SCP 8 with SHIP 1.5 or STP 2 behind it; or STP 5; or SCP 6 + SHIP 3 | Any one route. SCP alone stays Moderate. Lead ceiling still limits High to days 1-3 |
| Hail bands | SHIP 0.5 / 1.5 / 3 | Small / Large / Very large, on SHIP alone. No cold-aloft promotion; the warm-aloft demotion above 4900 m is kept |
| Hazard within category | hail <= category, wind Damaging max below Moderate | Re-applied after the conditional cap and lead ceiling, daily and frames |
| Day rain gate | `LEAD_TRIG`, flat 2 mm at every lead | Trace bar is 0.1x it, tropical floor 1.5x it, frame gate 0.25x it. Calibrated against gauge observations, 18 Sep 2026 |
| TSTM floor | 200 J/kg at -20C aloft, 500 at -8C | `tstm_floor()`, linear between; 350 when the 500hPa level is missing |
| Severity ceiling | High to day 3, Moderate to day 5, Marginal to day 8 | `MAX_CAT_BY_LEAD` |
| Frame rain gate | `FRAME_TRIG <- 0.5` mm per 3 h | Frames only |
| Tropical coastal zone | Carnarvon to Rockhampton line, 200 km inland | Needs 3 mm for Marginal and above |
| ECMWF candidate cap | `MAX_ECMWF_CANDIDATES <- 3000` | Second-opinion checks per run |
| Grid spacing | 0.72 deg, 1342 points | ~80 km. Costs ~27,500 API calls/run, ~83% of 1M a month at one run a day |
| Pressure levels | 37 with a key, 16 without | `LEVELS_FULL` / `LEVELS_BASE`; 19.5 vs 9 API calls per point |
| Workers | `NCORES <- 10` | Deliberately above the 4 vCPUs: the loop is network-bound, so workers overlap. Lower it if first-pass failures jump |

## 4. If a run publishes something wrong

The last 14 days of runs are kept in `docs/archive/` and are selectable in the viewer's run
picker. To republish an earlier day as the live outlook:

```bash
cp docs/archive/YYYY-MM-DD.json docs/outlook.json
git commit -am "Republish YYYY-MM-DD outlook"
git push
```


## 5. Verification (added 18 Sep 2026)

`pipeline/verify.py` scores the run archive against NOAA's CPC rain-gauge analysis. It is the
only part of this project that measures the forecast against something other than itself.

```bash
python3 pipeline/verify.py              # score every date that is now observable
python3 pipeline/verify.py --backfill   # refetch everything and re-score from scratch
```

It runs automatically at the end of every live build, writes `docs/archive/skill.json`, and
caches each day's observations under `docs/archive/obs/`. Pure standard library, no API key,
no Open-Meteo credits. It cannot fail the build.

**What it can check:** storm-day occurrence, which is exactly what the TSTM category and the
rain trigger assert. **What it cannot check:** hail, wind or tornado. A rain gauge cannot tell
Marginal from Moderate, so no number it produces is a severe-weather skill score.

The observation cache grows by about 68 KB a day and is never pruned, deliberately: the run
archive rolls over at 14 days, but the observations are the long-term record and are what any
future recalibration will be measured against. Prune only if the repo becomes unwieldy.

### First scores (12 days, September 2026)

| Day | POD | FAR | CSI | Frequency bias |
|---|---|---|---|---|
| 1 | 0.47 | 0.60 | 0.28 | 1.17 |
| 2 | 0.42 | 0.63 | 0.24 | 1.15 |
| 3 | 0.35 | 0.68 | 0.20 | 1.07 |
| 4 | 0.37 | 0.69 | 0.20 | 1.19 |
| 5 | 0.29 | 0.75 | 0.16 | 1.15 |
| 6 | 0.21 | 0.89 | 0.08 | 1.94 |
| 7 | 0.25 | 0.89 | 0.08 | 2.35 |
| 8 | 0.28 | 0.86 | 0.10 | 1.98 |

Measured before the lead-scaled trigger shipped, so days 6-8 are what that change targets.
Re-scored after it, over the same 12 days: mean CSI 0.177 -> 0.179 and mean |bias-1| 0.628 ->
0.114. Days 1-3 are untouched by construction, days 7-8 improve on POD, FAR and CSI together,
and day 6 is the one rung where no trigger buys both honesty and skill -- its CSI falls
monotonically as the trigger rises, so 5mm is a deliberate compromise at bias 1.22. Day 6 skill
is ~0.05 either way, which is a limit of the model at that range rather than of the calibration.
Twelve days in a dry September is a thin sample and the long-lead rows rest on ~110 observed
events each. Re-check once a wet season has run through.

**The finding that mattered.** Comparing the forecast against our own day-1 output said the
long-lead storm area was too SMALL, and the indicated fix was to lower the rain trigger at
range. The gauge data says the opposite: day 1 was itself over-forecasting by 1.17, so
"smaller than day 1" still meant considerably bigger than reality. The trigger ladder rises
with lead for that reason. Any future calibration should go through `verify.py` rather than
through a forecast-to-forecast comparison.

## 6. Checking a change without spending a run

A parse error costs a whole nightly outlook (it killed run 34800693670 after 1 minute) and a
manual re-run costs ~28,000 API calls, about 2.8% of the monthly quota. Both are avoidable: R
can be installed locally with no admin rights and no Xcode, and the two checks below catch
syntax errors and undefined variables without touching Open-Meteo.

```bash
curl -sL https://micro.mamba.pm/api/micromamba/osx-arm64/latest | tar -xj bin/micromamba
export MAMBA_ROOT_PREFIX=$PWD/mamba
./bin/micromamba create -y -p $PWD/renv -c conda-forge r-base r-codetools
```

**Does it parse:**

```bash
./renv/bin/Rscript -e 'parse("pipeline/build_outlook.R"); cat("OK\n")'
```

**Are all variables bound** -- load every function definition into an environment and run
`codetools::checkUsage` over it. Expect findings only for globals built by `if/else` or
`Sys.getenv` (`OM_HOST`, `OM_AUTH`, `LEVELS`, `START_DATE`, `END_DATE`, `OM_KEY`,
`ENABLE_FRAMES`, `GRID`, `NB_RADIUS`, `coast_xy`); anything else is a real bug.

The individual scoring functions can also be exercised directly this way -- `tstm_floor()`,
`lead_of()` and `categorise_vals()` are pure and need no sounding data, so a change to any of
them can be tested against known cases in seconds. `thunder` itself will NOT install without a
C toolchain, so `day_topN()` and anything downstream of a real sounding still needs CI.

The workflow now runs the same parse check as its own step ("Check pipeline parses"), ahead of
the package install, since 28 Sep 2026. It matters more than it looks: `Rscript` runs a file one
top-level expression at a time, so without it a syntax error anywhere after the main fetch would
only surface after ~10 minutes and ~28,000 API calls, with nothing published.


## 7. Hail and severity recalibration, 24 Sep 2026

Josh, on the 23 Sep run: *"great for TSTM, good for MRGNL, poor for MDT and the hail risks
were wildly overestimated."*

The hail over-forecast was not a threshold problem. That run published **34 points at Very
large (6cm+)** whose daily mean SHIP was 0.40-1.80 (median 0.80), against **zero** that raw
SHIP would have given. Two amplifiers compounded:

1. **The cold-aloft promotion fired nearly everywhere.** It needed only CAPE >= 300 with a
   500hPa temperature at or below -20C, which over southern Australia in September is the
   normal state of the atmosphere. It also allowed promotion all the way to tier 3, so 6cm+
   hail was being asserted off a SHIP near 1. Now CAPE >= 500 with a 3000m freezing level or
   -25C aloft, and it can only lift Small to Large.
2. **hail_tier() is fed the single peak-SHIP hour** while every other field is the top-6-hour
   mean, so the number behind a hail tier is always higher than the SHIP in the tooltip. That
   is deliberate and stays, but it means the bands must be read against peak-hour SHIP.

A worked case: peak SHIP 1.1 with CAPE 800 under -22C aloft published **Very large** before
this change and publishes **Small** after it.

On the top tiers being rare: SHIP 3 and STP 5 are above anything in the current archive
(peak-hour SHIP p99.9 is 1.0, max 2.3). That is intended. A tier meaning 6cm+ hail should be
close to dormant outside an extreme summer day -- 34 of them in a quiet September was the bug.
Do not lower these bars because they look unused; check them again after a wet season.

Watch on the first runs: **High now has reachable routes for the first time** (SCP 8 fired 130
times across the archive, against 0 for the old four-condition rule). The lead ceiling holds it
to days 1-3, but if High appears more than occasionally, SCP 8 is the dial to turn.


## 8. Cold-aloft promotion removed, 25 Sep 2026

Tightening it was not enough. On the 27 Sep panel it was **inverting the hail field**: the SW
corner of WA drew Large hail on SHIP 0.30 with SCP 1.3, while the Goldfields drew only Small on
SHIP 1.00 with SCP 9.5. A textbook supercell environment rated below a cold front.

The entire difference was the freezing level, checked against Open-Meteo directly:

| | Freezing level | CAPE | SCP | Promotion fired | Map showed |
|---|---|---|---|---|---|
| SW corner | 2740 m | 780 | 1.3 | yes | Large |
| Goldfields | 3320 m | 1100 | 9.5 | no | Small |

The physics behind it is sound -- Raupach et al. 2023 found melting-level height is exactly what
naive instability-shear proxies miss over Australia, and that they overestimate hail without it.
The error was applying it as a full tier promotion with no requirement that a hail-producing
updraft exist. A maritime cold front with 780 J/kg outranked a supercell.

A low freezing level means less melting of whatever hail forms. It is not itself a reason to
expect hail. **If this returns, it should modulate the SHIP bars rather than add a tier on top
of them** -- for instance requiring a lower SHIP for Large when the freezing level is low,
instead of promoting whatever tier SHIP already produced.

The warm-aloft demotion (freezing level above 4900 m) is kept. That one only ever reduces a
tier, and a 5 km freezing level genuinely does melt hail out before it reaches the ground.


## 9. The fuller record, 28 Sep 2026

**Verification now keeps every run it scores.** `docs/archive/skill_ledger.json` holds one
contingency record per (run, lead), forever; `skill.json` aggregates the whole ledger. Before
this, each night's scores covered only the 14 runs still in the rolling archive. Seeded with the
55 runs recovered from git history (4 Aug - 28 Sep) and 53 days of gauge observations.

**What the longer record overturned.** The steep rain-trigger ladder of 18 Sep,
`c(2,2,2,2,2.5,5,8,8)`, was fitted on 12 September days that were one unusually dry week. On
28 September days it drew a quarter to a half of the observed storm days at days 6-8 and cut
skill (day 7 CSI 0.126 -> 0.071), in both halves of the month. Replaced with Josh's gentler
`c(2,2,2,2,2.5,2.8,3.2,3.5)`. Flat 2 mm scored best of all on that record -- set all eight to 2
to use it. Lesson: do not calibrate on fewer than a month of verification days.

**Sounding accuracy 1 -> 2.** thundeR's `accuracy=1` is its fast setting. Against `accuracy=3`,
it put DCAPE ~21% low and changed the published wind tier on 3.3% of active point-days, every
one an under-call. `accuracy=2` removes most of that for ~1 ms per sounding. Revert by setting
`accuracy=1` in `day_topN()`.

**Later the same day: flat 2 mm.** Swept one trigger for every lead over the whole record. Skill
was flat from 1.5 to 2.0 mm (differences of 0.001-0.004 CSI on 28 September days), so 2.0 won on
keeping the 3 mm coastal-tropics floor (1.5x the trigger) and on being the long-standing value.
Kept as one number rather than eight, on purpose.


## 10. Primary model switched to ECMWF, 29 Sep 2026

Until now the pipeline named no model, so Open-Meteo served its "best match" blend: ICON upper
air (days 1-7) under a surface matching no single model. That stitching manufactured instability
-- northern NSW, Fri 2 Oct: blend CAPE ~800-900 J/kg where ICON, GFS and ECMWF each had 77-290,
drawn as Marginal. Josh's read: ICON has low skill over Australia; ECMWF is excellent, GFS good.

**Validation before switching.** All 55 archived runs (4 Aug - 28 Sep) were rebuilt from raw
archives on GFS (NOAA AWS) and on ECMWF (ECMWF open data, AWS), through the pipeline's own
day_topN(), at each run's own points, and scored against gauges and satellite cloud tops:

| Skill (CSI), current rules, days 1-4 / 5-8 | Gauge >=1 mm | Gauge >=5 mm | Aug gauge + sat <=230K |
|---|---|---|---|
| Blend | 0.246 / 0.166 | 0.102 / 0.075 | 0.139 / 0.120 |
| GFS | 0.164 / 0.120 | 0.121 / 0.079 | 0.181 / 0.134 |
| ECMWF | 0.199 / 0.128 | 0.099 / 0.074 | 0.172 / 0.133 |

The blend wins only the loose rain yardstick (by painting more area) and is weakest on both
storm-like ones, over-drawing 3.6-5.4x. GFS and ECMWF are close on storm occurrence, but GFS hit
CAPE >= 1000 on 0.13% of September point-days against ECMWF's 1.04% -- too rarely for the
Marginal floor ever to fire. ECMWF chosen for its realistic severe tail, Josh's experience of its
skill, a third of the API calls, and an archive to train on.

**What changed.** `models=ecmwf_ifs025`; only ECMWF's 12 levels requested (1000 925 850 700 600
500 400 300 250 200 150 100 -- losing the other 25 changed 0.8% of categories on a 240 point-day
test); ~6.9 API calls per point instead of 19.5, so ~10-11k per run, roughly a third of the
monthly quota at one run a day. Freezing level derived from the sounding (finish_hourly()); soil
moisture from ECMWF's 0-7 cm layer. The rain second opinion now comes from GFS, is recorded for
the tooltip, and does not change the category (see the switch above for why). Each outlook file
records `model` and `second_opinion`, and the viewer labels the tooltip from it, so archived runs
keep their ECMWF label.

**Expect smaller storm areas than the blend drew.** On a 62-point test over SE QLD / northern NSW:
general storm 20 -> 17 point-days, Marginal 13 -> 6. On the storm-like yardsticks that is a
correction. The thresholds were tuned on the blend's inflated CAPE; re-check them against the
verification ledger after a few weeks of ECMWF runs.

**Local testing note.** Running the pipeline on macOS can segfault inside mclapply workers when
they open network connections (fork safety). Set `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`. The
GitHub Linux runners are unaffected.
