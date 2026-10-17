# Alert scan (step 1 of postcode alerts, shadow mode)

Checks the **raw** model run (`docs/archive/raw/outlook.json`, not your edited map) against
`alerts/thresholds.json` for days 1-7 and emails the forecaster. It publishes nothing to subscribers.

```bash
python3 pipeline/alert_scan.py --dry-run     # show tonight's email, change nothing
python3 pipeline/alert_scan.py --backtest    # replay every archived raw run, count events per rule
python3 pipeline/test_alert_scan.py          # unit tests
```

**Thresholds are placeholders** until the real ones are supplied. Edit `alerts/thresholds.json`:
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
