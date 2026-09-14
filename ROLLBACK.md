# Rolling back

Every change since 12 Sep 2026 is either behind a switch or tagged, so nothing needs a
hand-written revert. Work from the cheapest option down.

## 1. Turn a feature off without touching history

Edit one line in `pipeline/build_outlook.R`, commit, and the next run behaves as if the feature
had never shipped. The daily `docs/outlook.json` schema is unchanged by all of these.

| Feature | Line | Off value | Effect when off |
|---|---|---|---|
| Full 37-level soundings | presence of the `OPENMETEO_KEY` secret | remove the secret | Falls back to the free endpoint and the original 16 levels automatically. |
| 3-hourly frames (days 1-8) | `ENABLE_FRAMES <- TRUE` | `FALSE` | No frames computed, no `docs/archive/frames/` written. Run time and `outlook.json` identical to before 14 Sep. |
| Default map zoom | `var FIT_ZOOM_OUT=1;` in `docs/index.html` | `0` | Back to the strict cover fit that crops to the panel. |
| Default pane layout | `var planeMode='dual';` in `docs/index.html` | `'quad'` | Also set `class="mode-dual"` back to `mode-quad` on `#planes` and move the `active` class on the two `.modeBtn` buttons. |
| Default view | `var viewMode='hourly';` in `docs/index.html` | `'daily'` | Opens on the 8-day daily panels instead of the 3-hourly slider. Also move the `active` class on the two `.viewBtn` buttons. |
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
| Marginal floor | `cape >= 1000 & shr_kt >= 25` | Plus an SCP route at 2.5, plus large hail or damaging winds |
| Moderate via SHIP | 2.0 / 1.5 / 1.2 | By shear band: under 35 kt, 35-50 kt, over 50 kt |
| High | CAPE 4000, SHIP 2.5, SCP 9, rain 10 mm | All four required |
| Day rain gate | `trig = 2` mm | Trace bar is 0.1x it, tropical floor 1.5x it |
| Frame rain gate | `FRAME_TRIG <- 0.5` mm per 3 h | Frames only |
| Tropical coastal zone | Carnarvon to Rockhampton line, 200 km inland | Needs 3 mm for Marginal and above |
| ECMWF candidate cap | `MAX_ECMWF_CANDIDATES <- 3000` | Second-opinion checks per run |
| Grid spacing | 0.72 deg, 1342 points | ~80 km. Costs ~27,500 API calls/run, ~83% of 1M a month at one run a day |
| Pressure levels | 37 with a key, 16 without | `LEVELS_FULL` / `LEVELS_BASE`; 19.5 vs 9 API calls per point |
| Workers | `NCORES <- max(1, min(4, ...))` | 4. Roughly half of each worker's time is spent waiting on HTTP, so raising this is the main remaining speed lever |

## 4. If a run publishes something wrong

The last 14 days of runs are kept in `docs/archive/` and are selectable in the viewer's run
picker. To republish an earlier day as the live outlook:

```bash
cp docs/archive/YYYY-MM-DD.json docs/outlook.json
git commit -am "Republish YYYY-MM-DD outlook"
git push
```
