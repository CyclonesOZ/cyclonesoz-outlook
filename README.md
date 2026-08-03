# CyclonesOZ — Automated Severe Storm Outlook

A free, daily severe-storm outlook for Australia. It uses the **thundeR** package (the same engine behind ASTORP) on GFS model data, and publishes an SPC-style map you can embed in cyclonesoz.

## How it works

Once a day, a free GitHub Action:

1. Pulls the GFS model's vertical profile for ~210 points across Australia (from Open-Meteo, which serves GFS as JSON — no messy GRIB files).
2. Runs each profile through **thundeR** to get the real storm parameters (MUCAPE, effective shear, SCP, STP, SHIP).
3. Turns those into SPC-style categories (TSTM → HIGH) with a hatch for significant severe.
4. Writes `docs/outlook.json`.

The web viewer (`docs/index.html`) reads that file and draws the smooth map. cyclonesoz shows it in an iframe.

**No server. No cost. Runs itself every day.**

## One-time setup (about 10 minutes)

1. **Create a new GitHub repo** (private is fine), e.g. `cyclonesoz-outlook`.
2. **Upload every file in this folder**, keeping the folder structure (`.github/`, `pipeline/`, `data/`, `docs/`).
3. **Turn on Pages:** repo **Settings → Pages → Source: Deploy from a branch → Branch: `main` / folder: `/docs`** → Save.
4. **Give the Action permission to save results:** **Settings → Actions → General → Workflow permissions → “Read and write permissions”** → Save.
5. **Run it once:** **Actions tab → “Daily storm outlook” → Run workflow.** Wait a few minutes.
6. When it finishes, open your Pages URL: `https://<your-username>.github.io/cyclonesoz-outlook/` — you should see the map.

After that it updates itself every day at 07:00 UTC (afternoon in Australia).

## Embed in cyclonesoz

Once the Pages URL works, add this to the subscriber dashboard (I can give you the Base44 prompt):

```html
<iframe src="https://<your-username>.github.io/cyclonesoz-outlook/?theme=dark"
        style="width:100%;height:620px;border:0;border-radius:10px;"></iframe>
```

GitHub Pages allows embedding, so no extra headers are needed.

## Tuning

The category thresholds live at the top of `pipeline/build_outlook.R` in the `categorise()` function. After the first real run we check the numbers (especially whether effective shear comes out in m/s vs knots) and tune so it fires right for Australian storms.

## Files

- `.github/workflows/daily-outlook.yml` — the daily schedule + steps
- `pipeline/build_outlook.R` — fetch profiles, run thundeR, categorise, write JSON
- `data/grid.json` — the 210 Australian grid points `[lat, lon]`
- `docs/index.html` — the map viewer (served by GitHub Pages)
- `docs/outlook.json` — created by the Action each day
