# The Front Line: writing and image style guide (approved 17 Oct 2026; voice and structure revised 21 Oct 2026)

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

## 2. Voice (revised 21 Oct 2026)

- A **forecaster's outlook**, not a warning bulletin or a to-do list. Say what the weather is doing and will do, how
  confident the forecast is, and what that means for people. Signed `~Josh`. Warm, plain, no hype.
- Opens with "G'day <region>," ("G'day everyone," for updates or several unrelated regions), then goes **straight
  into a thesis**: one confident statement of what the event is, where, when and how significant. Never opens with
  "I am tracking", "I'm watching", "I'm monitoring" or similar.
- **Advice is guidance, not orders.** "It would be wise to...", "it's worth...", "now is a good time to...",
  "residents in low-lying areas may want to...". Not "Prepare now", "Do not...", "Make sure you...", "Stay inside".
  Where life safety matters, state the risk and let it carry the advice ("driving through floodwater is how most flood
  deaths happen, so it is best avoided entirely").
- First person is allowed sparingly ("I'm expecting", "my confidence is higher for...", "I'll update this as guidance
  firms up"), never as the opener and never to give commands.
- Honest about uncertainty and misses ("some areas may miss out", "confidence has shifted"); says when something is
  **not** a big deal. Updates say what changed and why.
- Australian spelling, metric (mm, km/h, cm), 12-hour times with day names.
- Names real places and towns, and always names the capital or largest town if it is or is not affected.

## 3. Structure and length by tier (revised 21 Oct 2026)

One topic per paragraph. Plain paragraphs, no bullets, no headings. The greeting and the `~Josh` sign-off are not
counted as paragraphs. The tier is named in words ("Moderate impact potential"), and where several tiers are in the
alert the text explains where each applies. Never invent an update day: if none is given, "I'll update as guidance
firms up".

| Tier | Paragraphs | Topics, in order | Words |
|---|---|---|---|
| Minor | 3 | scenario (thesis and setup) / expected impacts / what to do | 150-250 |
| Moderate | 5 | thesis / setup and confidence / timing and where (capitals and big towns named) / expected impacts / what to do | 350-500 |
| Significant | 7 | thesis / setup and what makes it unusual / how it unfolds / where, region by region and by tier / expected impacts / confidence and what could change / what to do | 550-750 |
| Major | as many as needed (usually 9-12) | thesis / setup and why it is exceptional / how it unfolds / where / impacts (homes, roads, power, water, communications, health) / who and what is most at risk / confidence and what could change / preparation timeline (now, before it arrives, during) / recovery / next update | detailed, no padding |

Severe-storm posts add the ingredients when the event is notable (MLCAPE, 0-6 km shear) but keep a plain-English sentence
beside them. The drafting prompt in the app (`base44/functions/postcodeAlert/entry.ts`: `VOICE`, `STRUCTURE`,
`EXAMPLES`) is the working copy of this section and includes one Minor and one Moderate style example.

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

1. "G'day <region>," opener (then straight into a thesis, never "I am tracking") and `~Josh` sign-off, for hand-written and Claude-drafted posts. Advice is forecast-style guidance, not commands. Paragraph count by tier: Minor 3, Moderate 5, Significant 7, Major as many as needed (21 Oct 2026).
2. Keep SWPAnnn numbering and the title format; continue automatically from the highest number (SWPA043, so next is SWPA044).
3. One master text per event, plus an optional editable regional paragraph.
4. Claude drafts, Josh proofreads. Never auto-publish.
5. Image drawn deterministically from data in the layout above. Basemap: NASA Blue Marble.

## 7. Hard rule

No test alert, email or notification goes to a real subscriber. Test sends go only to
josh@cyclonesoz.com.au (hard-coded in the send function), test mode is the default, and live
sending stays off until Josh says go.
