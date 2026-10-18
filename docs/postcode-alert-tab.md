# Postcode Alert tab (Base44 app "CyclonesOZ", id 69fbea2b450e42a3a7c55ce9)

Status 17 Oct 2026: built and saved as checkpoints in the Base44 app. **Not published to the live site.**
The old Post Forecast page is untouched.

## What was added to the Base44 app

| Path | What |
|---|---|
| `/post-alert` (`src/pages/PostAlert.jsx`) | The new admin page, linked from Admin as "Postcode Alert (new)". |
| `src/components/admin/PostcodeMap.jsx` | Leaflet map of all 2,641 postcode areas: Click / Paint / Box / Lasso, tier brush, undo, search, coast / state / sector border toggles. |
| `src/lib/postcodeGeo.js` | Decodes `public/geo/poa_2021.topo.json` + `postcodes.json`; derives coast, state and sector border lines from the shapes. |
| `src/lib/alertImage.js` | Draws the alert image (dark greyscale terrain, glowing tier areas, header panel + tier badge, tier-coloured town dots, footer with valid times and SWPA number, logo). |
| `base44/functions/postcodeAlert/entry.ts` | Backend: `counts`, `draft` (Claude), `test`, `publish`. Admin only. |
| Entities `PostcodeAlert`, `AlertAuditLog` | Admin-only records of each alert and an audit trail. |
| `public/geo/*`, `public/fonts/*`, `public/brand/cyclonesoz-logo.png` | Data, self-hosted fonts (Bebas Neue, Barlow Condensed; SIL OFL), logo. |

## Standing rule (Josh, 18 Oct 2026)

**Only Josh sends live alerts. Never an assistant, never Base44.** `ALERT_LIVE_SEND` is not set and must never be
set by anyone but Josh. The same rule is at the top of the Base44 app's `CLAUDE.md` for other agents.

## Safeguards (Josh's hard rule)

- `test` emails **josh@cyclonesoz.com.au only** (constant in the function), one email per tier present.
- `publish` returns 403 unless the secret `ALERT_LIVE_SEND` is exactly `true`. It is not set. It also needs
  `confirm: "SEND"`, and the page only enables Publish after a test send and typing SEND.
- No existing broadcast function was called.

## Fixes made along the way

- `postcodes.json` kept only 8 localities per postcode (hid Toowoomba, Ipswich, Roma ...). Now all of them.
- `src/lib/postcodeData.js` and `base44/shared/postcodeSectors.ts`: 634 postcodes were in no sector list
  (4508 Deception Bay, PO Boxes, WA/VIC areas). They now fall back to the nearest listed postcode's sector in
  the same state (`EXTRA_SECTOR`). This is the sign-up bug. Only 6958 (no coordinates) remains unmatched.
  Takes effect on the live site only after the app is published.

## Still to do

1. Run the real tab in the preview as admin (the sandbox CLI wants a device login: `npx base44 exec`).
2. Resend plan and limits are still unknown; check before the first real send.
3. Publish the Base44 app when Josh says go (the published site keeps the old function code until then).
4. Alert framework (thresholds) comes after the tab; branch `claude/alert-scan` is parked.
