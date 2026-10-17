Build a 30-second promotional video in Remotion for CyclonesOZ's paid severe-weather service, The Front Line. Message: **CyclonesOZ now issues severe weather alerts down to the postcode, the most detailed severe weather information in Australia.** Make it look like a premium broadcast weather product, not a stock template.

## Specs
- 1920x1080, 30 fps, exactly 900 frames. Also export a 1080x1920 vertical cut (same story, recomposed for portrait) for Reels/Shorts.
- Compositions: `Promo16x9` and `Promo9x16`, sharing components. Render to H.264 MP4. Use `spring()` / `interpolate()` for all motion; no abrupt linear moves. Add a light film-grain/vignette overlay.
- Fonts (both SIL Open Font License, Google Fonts): **Bebas Neue** for big headlines and numbers, **Barlow Condensed** (600/700) for labels and small text. Load with `@remotion/google-fonts`.
- Palette: background #0b0f14, glass panels rgba(8,12,18,0.88) with a 1px rgba(255,255,255,0.16) border, brand accent orange #ea580c, white text. Impact tiers are fixed colours: Minor #5fd0c4 (teal), Moderate #6bbf3e (green), Significant #f2b63c (yellow), Major #c8402f (red). Always show the tier name as text next to its colour.
- Assets are in `docs/promo/` of the repo (copy them into `public/`):
  - `warning-darling-downs-qld.png` (1400x1200, MODERATE, SE Queensland)
  - `warning-southwest-wa.png` (1400x1200, SIGNIFICANT, Perth and the south-west)
  - `warning-north-east-vic.png` (1400x1200, SIGNIFICANT, north-east Victoria)
  - `postcode-alert-email.png` (760 wide, the alert as it arrives in a subscriber's inbox)
  - `cyclonesoz-logo.png` (440x440, circular logo with transparent corners)
  Do not redraw or restyle these graphics. Animate around them (crop, push-in, parallax, masks, reveals).

## Storyboard (30 s)
1. **0:00-0:03 Hook.** Black. A thin orange line wipes across, then huge Bebas text slams in one word at a time: "SEVERE WEATHER." then, on its own line in orange, "DOWN TO YOUR POSTCODE." Quiet low rumble, then a hit on the last word.
2. **0:03-0:09 The idea.** Cut to `warning-darling-downs-qld.png` filling the frame, slow push-in on the Toowoomba / Darling Downs area. Animate a clean mask reveal so the coloured postcode areas appear to fill in area by area (teal first, then green, then yellow), with the glowing outlines drawing on. Lower-third text, typed on: "Not a region. Not a state. YOUR postcode." Small counter in the corner ticks up to "2,600+ POSTCODE AREAS".
3. **0:09-0:16 Across Australia.** Quick, rhythmic cuts (about 2 s each, whip-pan or slide transitions) through the three warning graphics: Queensland, then south-west WA, then north-east Victoria. On each cut, punch in on the tier badge (top right) so "MODERATE" / "SIGNIFICANT" scales in with a small shake. A sample-label chip "SAMPLE ALERT" stays visible bottom-left of every graphic so nobody mistakes them for live warnings.
4. **0:16-0:22 It finds you.** A phone (simple flat device frame, no brand) slides up. A notification banner drops in: "CyclonesOZ · SIGNIFICANT impact for postcode 4380 · Stanthorpe". Tap, then the phone shows `postcode-alert-email.png` scrolling slowly from the header down to the graphic. Highlight (animated underline or glow) these lines as they pass: the tier badge, "Valid from Thursday 22 October at 2:00 PM AEST", and the footer line "Your postcode is in the MODERATE impact area for this event." Subtitle: "The right alert. The right tier. Your local time."
5. **0:22-0:26 Four impact levels.** Clear the screen. The four-step tier bar builds left to right: MINOR, MODERATE, SIGNIFICANT, MAJOR, each segment popping in with its colour and a one-line meaning underneath: "Run-of-the-mill severe." / "More significant, more people." / "Once or twice a year." / "Life-threatening." Tight snare roll building underneath.
6. **0:26-0:30 Call to action.** Dark background with a subtle slow-moving map-grid texture. `cyclonesoz-logo.png` scales in with a soft glow. Text: "CYCLONESOZ" (large), "THE FRONT LINE" (orange, letter-spaced), then "Australia's most detailed severe weather alerts. Down to the postcode." and "cyclonesoz.com.au". Final 8 frames hold. In small grey print along the bottom: "Automated guidance plus forecaster review. Not an official warning. Always check the Bureau of Meteorology."

## Sound
Use a royalty-free cinematic/tech bed (low pulse building to the tier-bar section, resolving on the logo). Whoosh on each transition, a soft click on the notification, a bass hit on "DOWN TO YOUR POSTCODE" and on the logo. Make audio a separate track so it can be swapped. If no licensed audio is on hand, render silent with placeholder cues marked in comments.

## Rules
- Only use the supplied assets and text above. Do not invent statistics, towns, quotes or testimonials, and do not show any subscriber names or emails.
- No Bureau of Meteorology or other third-party logos.
- Keep the sample graphics' text legible: never crop the headline panel, tier badge or footer strip off the frame.
- Keep all text inside a 90% safe area, and large enough to read on a phone.
- Make colours, copy and timings props in one `config.ts` so I can tweak them without touching components.

Deliver: the project, `npm run render` scripts for both aspect ratios, and a short README listing frame ranges per scene.
