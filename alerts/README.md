# Alert scan (step 1 of postcode alerts, shadow mode)

Checks the **raw** model run (`docs/archive/raw/outlook.json`, not your edited map) against
`alerts/thresholds.json` for days 1-7 and emails the forecaster. It publishes nothing to subscribers.

```bash
python3 pipeline/alert_scan.py --dry-run     # show tonight's email, change nothing
python3 pipeline/alert_scan.py --backtest    # replay every archived raw run, count events per rule
python3 pipeline/test_alert_scan.py          # unit tests
```

## Thresholds are region-relative, and calibrated against Josh's own alerts

Rainfall and frost mean different things in different parts of Australia, so a rule's value can be a
**region bar** (`{"$ref_region_bar": "rain_3day"}`) resolved per point against the 13 climate regions
in `alerts/regions.json`. Storms, hail, wind, tornado and fire already ride dimensionless model
tiers, and heat is an anomaly against each point's own climatology, so those need no region gate.

`pipeline/calibrate_against_issued.py` checks the thresholds against **every Front Line alert Josh
has actually issued** (`alerts/issued-calibration.json`, the complete PostcodeAlert history). Run it
after any threshold change:

```bash
python3 pipeline/calibrate_against_issued.py   # currently: fired 13, missed 0, of 13
```

`pipeline/test_regions.py` fails if a change stops surfacing one of those alerts.

**Why rain accumulates.** Measured against that history, the model's single-day peak over the areas
Josh alerted was 25-46 mm while the bar was 75 mm, so single-day rules missed 8 of 10. His validity
windows run 4-7 days. The `accumulate_days` condition sums a field over N consecutive days;
`rain3_minor` / `rain3_mod` use it.

## Two hazards are built but idle, each waiting on one thing

| Hazard | Waiting on |
|---|---|
| **heatwave** | `data/climatology.json`, which has never been built, so heat has been silently skipped on every run to date. The rules and the anomaly maths are already there. Run the one-off: `gh workflow run "Build climatology"`. |
| **tropical cyclone / low** | A Base44 read token. Add repo secrets `BASE44_APP_ID` and `BASE44_TOKEN` and pass them through in `alert-scan.yml`. `pipeline/tropical.py` then reads the `TropicalSystem` entity (ensemble consensus track, cone radii, member fraction) and sets `tc_cat` / `tc_prob`. Without the token it returns no systems and the four `tc_*` rules stay idle. |

Both degrade silently and safely: a missing field never fires a rule, so neither can half-work.

**Remaining thresholds are placeholders** until the real ones are supplied. Edit `alerts/thresholds.json`:
a rule fires at a grid point/day when all its `when` conditions hold; neighbouring firing points form
an event once some rule has `min_points` inside the cluster. `level` only drives escalation detection;
it is not an impact rating.

**What gets emailed.** Only changes since you were last told: NEW, ESCALATED (higher level, or area
grew by 1.5x and 3+ points), SHIFTED (centre moved 1.5 deg), WEAKENED, CLEARED. What you were last
told is held in `docs/archive/alerts/state.json`, so slow creep still re-alerts eventually. A heartbeat
email goes out every run, and a stale or failed model run sends a WARNING instead of a scan.

**Email delivery.** Set repo secrets `ALERT_SMTP_HOST`, `ALERT_SMTP_PORT`, `ALERT_SMTP_USER`,
`ALERT_SMTP_PASS`, `ALERT_FROM`, `ALERT_TO`. Without them the email is only printed and saved to
`docs/archive/alerts/last_email.txt`.

**Known limits.** Model days are 00Z-24Z (windows are shown in local time, by a crude state-level
zone). The grid is ~80 km, so districts, not postcodes, are named. Postcodes come in a later step.

**Turn it off.** Delete `.github/workflows/alert-scan.yml`. Nothing else reads these files.
