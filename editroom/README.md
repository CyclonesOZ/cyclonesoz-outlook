# Outlook edit room

Josh's morning editor for the daily category outlook (manual outlook, Oct 2026).

**Open it:** http://localhost:8417/ on the Mac (bookmark it). A LaunchAgent keeps the server running:
`~/Library/LaunchAgents/au.com.cyclonesoz.edit-room.plist`, logs in `~/.cyclonesoz/edit-room/`.

**Each morning:** the editor starts from your last published edit carried forward (yesterday's
days 2-8 are today's days 1-7; the new day 8 comes from the model). Squares outlined magenta are
where the new model run changed its mind and now disagrees sharply with you. Paint, box or outline
squares with a category or Upgrade/Downgrade; days 5-8 use the public map's coarse scale. Open
Preview to see the public page itself drawing your draft: anything you changed that its smoothing
would remove is outlined red. Then Publish.

**Squares (from 1 Nov 2026):** the squares are 0.36 degrees, about four times as many as the model's own
0.72 degree points (about 5,200 in all). The model run is unchanged. `pipeline/manual_compose.py`
(`densify`) keeps every model point exactly as it is and adds a point halfway between neighbours, flagged
`interp`, taking its tiers from the public map's own test so unedited shapes stay put (shapes overlap
92-94%; the odd borderline patch of a square or two can drop off, and Preview outlines any you changed in
red). Publish writes all of them, so `outlook.json` is about four times the size. The **overnight run stays
on the model's own points** (Josh, 1 Nov 2026): it reads your edit at those points and publishes the old grid,
exactly as before, so between the 4 am run and your morning Publish the public maps are on the model's
squares. Your painted finer squares are kept in `edits.json` and carry forward into the editor each morning.
The **7x7** brush covers what 3x3 did before. An edit made on the old squares carries onto the new ones by
the same test.
`pipeline/verify.py` skips the added points. To go back, revert the commit: edits and files on the finer
squares still read fine, and the next publish is back to the model's own points.

**Publishing is live (from 8 Oct 2026):** Publish checks the outlook (every point and day present,
day 1 is today, every member summary agrees with your map), commits it to main with the Mac's
GitHub login and pushes; the editor then watches the website until it serves your outlook. The app
caches it for up to 30 minutes more. To drop back to sandbox mode (Publish only writes to
`~/.cyclonesoz/edit-room/sandbox/`, viewable at http://localhost:8417/sandbox/), create the file
`~/.cyclonesoz/edit-room/SANDBOX_MODE`; delete it to go live again.

**How it fits together:** `server.py` (standard-library Python, runs from its own clone in
`~/.cyclonesoz/edit-room/repo`, refreshed from GitHub when the editor loads) uses
`pipeline/manual_compose.py`, the same code the nightly pipeline uses to carry edits forward, so
the preview, the publish and the overnight carry-forward always agree.

**Restart / stop:**
`launchctl kickstart -k gui/$(id -u)/au.com.cyclonesoz.edit-room` /
`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/au.com.cyclonesoz.edit-room.plist`
