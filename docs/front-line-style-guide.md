# The Front Line: writing and image style guide (APPROVED by Josh, 17 Oct 2026)

Learned from all 40 `SectorForecast` posts (19 Jun to 12 Oct 2026), the 2 `ClimateNews`
posts, and six of the forecast images (SWPA042, 040, 029/028, 037, 016, 026).
Nothing here has been used to generate anything yet.

## 1. Product facts the data shows

- Posts are per sector. One event is often posted several times, once per sector, each
  with its own text (SWPA027 = five posts, SWPA031 = three) and its own severity.
- Title format: `SWPA0nn - Short headline`. Numbering is sequential across the season.
  Early posts have no number, some numbers are skipped (017-021, 024, 030, 033, 034,
  036, 041 are not in the data; confirm whether they were never sent or sent elsewhere).
- `severity` is minor / moderate / significant / major and matches the tier bar on the image.
- The image is a screenshot of the map, uploaded by hand (~18 MB PNG, 4192 x 3600).
- Email subject is `CyclonesOZ Alert: <title>`; body is the post text and image.

## 2. Voice

- First person, one forecaster: "I'm watching...", "I appreciate you being on The Front
  Line", signed `~Josh` (older posts: `Cheers, Josh`). Warm, plain, no hype.
- Opens with "G'day <region>," for sector posts; "G'day everyone," for updates or
  multi-sector. Older posts used "Hi" or "Howdy". Use **G'day** going forward.
- Honest about uncertainty and misses: "This remains a tricky forecast", "some areas may
  miss out", "confidence has shifted". Says when something is **not** a big deal
  ("a pretty standard set up and doesn't warrant much in the way of warning").
- Updates say what changed and why ("UPDATE and REDUCTION", "Confidence has increased").
- Australian spelling, metric (mm, km/h, cm), 12-hour times with day names.
- Names real places and towns, then says "surrounding communities". Always names the
  capital if it is or is not affected ("Sydney is excluded from the shaded outline").
- Closes with practical advice (tidy the yard, never drive through floodwater, check
  river levels on the Weather Map) and when the next update is due.

## 3. Structure (in order)

1. Greeting.
2. One-paragraph summary: the system and the headline impact.
3. Where and what: totals in mm, wind gusts, hail size, snow depth, key places.
4. Timing: days and local time window.
5. Capital cities / big populations, one line each, even when "minimal impacts".
6. Impact tier statement and what to do.
7. Next-update promise, sign-off.

Length: 100-200 words for a routine single-sector post, 300-450 for a significant
event, up to ~550 for a multi-state update. Bulleted "Expected Impacts" list by
state/region is used for big multi-sector events.

Severe-storm posts add the ingredients when the event is notable (MLCAPE, 0-6 km shear)
but keep a plain-English sentence beside them.

## 4. Tier language (as decided)

| Tier | Meaning | Colour on image |
|---|---|---|
| Minor | run-of-the-mill severe event | teal |
| Moderate | more significant, more people | green |
| Significant | once or twice a year | yellow/amber |
| Major | life-threatening, less than 1-in-10 years (rain: 1-in-20) | red |

The tier name always appears as text ("MODERATE Impact Potential"), because outlook-map
colours overlap.

## 5. Image conventions (what to draw)

Canvas about 1400 x 1200 (screenshot was 4192 x 3600 at the same ratio).

- Satellite-style dark basemap (brown land, dark blue sea, soft vignette), zoomed to the
  event, not all of Australia. State borders as thin black lines.
- Top: horizontal **tier bar** MINOR | MODERATE | SIGNIFICANT | MAJOR, gradient
  teal > green > yellow > red, each with "IMPACT POTENTIAL" underneath. The tier for this
  event is boxed with a thick black outline and a pointer down to the headline box.
- Below it: **headline box** in the tier colour with a white warning triangle, bold
  condensed title `<TIER> Impact Potential — <Region>` and one-line subtitle.
- **Shaded polygons** with a thick dark outline, semi-transparent, filled in the tier
  colour. Higher tiers are drawn nested inside lower ones (Moderate inside Minor, as in
  SWPA037 and SWPA029).
- **Place labels**: white italic condensed caps on dark translucent chips, spaced out.
  Abbreviates long names (ROCK., GLAD., LIGHTNING R.).
- Optional **callout boxes** per tier area (SWPA037): tier-coloured box with tier name,
  "IMPACT POTENTIAL", 3-5 lines of text, a tail pointing at the area.
- Bottom-left: CyclonesOZ logo with the issue date (d/m/yyyy). Bottom-right: `SWPAnnn`.

New for postcode alerts: the polygons become **postcode areas** coloured by tier. Keep
the rest of the layout. Place labels come from the largest localities among the selected
postcodes plus the capital.

## 6. Decisions (all approved by Josh)

1. "G'day <region>," opener and `~Josh` sign-off, for hand-written and Claude-drafted posts.
2. Keep SWPAnnn numbering and the title format; continue automatically from the highest number (SWPA043, so next is SWPA044).
3. One master text per event, plus an optional editable regional paragraph.
4. Claude drafts, Josh proofreads. Never auto-publish.
5. Image drawn deterministically from data in the layout above. Basemap: NASA Blue Marble.

## 7. Hard rule

No test alert, email or notification goes to a real subscriber. Test sends go only to
josh@cyclonesoz.com.au (hard-coded in the send function), test mode is the default, and live
sending stays off until Josh says go.
