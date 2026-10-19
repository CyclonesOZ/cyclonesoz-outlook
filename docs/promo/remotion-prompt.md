Build a 30-second SILENT promotional video in Remotion for CyclonesOZ's paid severe-weather service, The Front Line. I will record a voiceover myself, so there is no music and no sound effects. The video's job is to show the audience exactly what they get: **severe weather alerts down to the postcode, the most detailed severe weather information in Australia.** Make it look like a premium broadcast weather product, not a stock template.

## Specs
- 1920x1080, 30 fps, exactly 900 frames. Also export a 1080x1920 vertical cut recomposed for portrait. Compositions `Promo16x9` and `Promo9x16` share components. H.264 MP4 with no audio track.
- Use `spring()` and `interpolate()` for all motion, plus a light film grain and vignette. Keep motion smooth and purposeful so it sits well under a voiceover. Give every scene a clear hold of at least 1.5 seconds with nothing moving except a slow push-in, so I can talk over it.
- Fonts (SIL Open Font License, Google Fonts): **Bebas Neue** for headlines and numbers, **Barlow Condensed** (600/700) for labels. Load with `@remotion/google-fonts`.
- Palette: background #0b0f14, glass panels rgba(8,12,18,0.88) with a 1px rgba(255,255,255,0.16) border, brand orange #ea580c, white text. Impact tiers are fixed colours: Minor #5fd0c4 (teal), Moderate #6bbf3e (green), Significant #f2b63c (yellow), Major #c8402f (red). Always show the tier name as text beside its colour.
- Assets are in `docs/promo/` of the repo (copy them into `public/`):
  - `warning-darling-downs-qld.png` (1400x1200, MODERATE, SE Queensland)
  - `warning-southwest-wa.png` (1400x1200, SIGNIFICANT, Perth and the south-west)
  - `warning-north-east-vic.png` (1400x1200, SIGNIFICANT, north-east Victoria)
  - `postcode-alert-email.png` (760 wide, the alert as it arrives in a subscriber's inbox)
  - `cyclonesoz-logo.png` (440x440, circular logo with transparent corners)
  Do not redraw or restyle these graphics. Animate around them (crop, push-in, parallax, masks, reveals).

## Storyboard (30 s)
1. **0:00-0:03 Hook (keep this exactly as described).** Black. A thin orange line wipes across, then huge Bebas text slams in one word at a time: "SEVERE WEATHER." then, on its own line in orange, "DOWN TO YOUR POSTCODE." Visual impact only: a quick scale-and-settle on the last word and a brief light flash, no sound.
2. **0:03-0:10 What the warning looks like.** `warning-darling-downs-qld.png` fills the frame with a slow push-in on the Toowoomba / Darling Downs area. A clean mask reveal fills the coloured postcode areas in (teal, then green, then yellow) with the glowing outlines drawing on. Keep on-screen text minimal: one small typed caption, "Your postcode. Your impact level." Hold the finished graphic for at least 2 seconds.
3. **0:10-0:16 Anywhere in Australia.** Two clean cuts (about 3 s each) to `warning-southwest-wa.png` and then `warning-north-east-vic.png`, each with a slow push-in and the tier badge scaling in. A "SAMPLE ALERT" chip stays visible bottom-left on every graphic so nobody mistakes them for live warnings. Add a small state tag top-left on each (QLD, WA, VIC) that animates in.
4. **0:16-0:23 What lands in your inbox.** A plain phone frame slides up. A notification banner drops in: "CyclonesOZ · SIGNIFICANT impact for postcode 4380 · Stanthorpe". It opens to `postcode-alert-email.png`, scrolling slowly from the header to the graphic. Highlight with an animated underline or glow, in this order: the tier badge, the "Valid from ... AEST" line (caption: "In your local time"), the forecaster's text (caption: "Written by a forecaster"), and the map (caption: "Your postcode on the map"). Keep each caption on screen for about 1.5 seconds.
5. **0:23-0:27 Four impact levels.** Clear the screen. The four-step tier bar builds left to right: MINOR, MODERATE, SIGNIFICANT, MAJOR, each segment popping in with its colour and a one-line meaning underneath: "Run-of-the-mill severe." / "More significant, more people." / "Once or twice a year." / "Life-threatening."
6. **0:27-0:30 Call to action.** Dark background with a subtle slow-moving map-grid texture. `cyclonesoz-logo.png` scales in with a soft glow. Text: "CYCLONESOZ" (large), "THE FRONT LINE" (orange, letter-spaced), "Severe weather alerts. Down to the postcode." and "cyclonesoz.com.au". Hold the final 20 frames. In small grey print along the bottom: "Automated guidance plus forecaster review. Not an official warning. Always check the Bureau of Meteorology."

## Rules
- No audio of any kind: no music, no sound effects, no audio track.
- Only use the supplied assets and the text above. Do not invent statistics, towns, quotes or testimonials, and do not show any subscriber names or emails.
- No Bureau of Meteorology or other third-party logos.
- Never crop the headline panel, tier badge or footer strip off the sample graphics.
- Keep all text inside a 90% safe area, and large enough to read on a phone.
- Put colours, copy and timings in one `config.ts` so I can tweak them without touching components.

Deliver: the project, `npm run render` scripts for both aspect ratios, and a short README listing frame ranges per scene so I can time my voiceover to them.
