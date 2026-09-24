#!/usr/bin/env Rscript
# ---------------------------------------------------------------------------
# CyclonesOZ automated severe-storm outlook  (8-day, ~1000 points)
# GFS profiles (via Open-Meteo JSON) -> thundeR parameters -> SPC-style category
# Each day = the AVERAGE of the 6 highest-severity hours in that day.
# Days 1-4 use the full TSTM..HIGH scale; days 5-8 are shown coarser by the viewer.
# Writes docs/outlook.json for the web viewer. Runs daily in GitHub Actions.
# ---------------------------------------------------------------------------
suppressMessages({
  library(thunder)
  library(jsonlite)
})

GRID   <- fromJSON("data/grid.json")           # matrix [,1]=lat [,2]=lon
OUT    <- "docs/outlook.json"
ARCHIVE_DIR <- "docs/archive"
# ---- 3-hourly frame product (14 Sep 2026) ----
# Every forecast day also gets a 3-hourly breakdown: 8 days x 8 frames = 64 frames the viewer can
# slide through. Days 5-8 inherit the daily product's extended-range rule in the viewer, so they
# show Category and Thunderstorm Chance only. This costs NO extra fetching and NO extra sounding maths -- day_topN() already runs a
# full sounding_compute() on every one of the 192 hours and then throws the hourly detail away
# when it collapses each day to a top-6-hour mean. Frames just keep what was already computed.
# FRAME_TRIG is the rain gate for a 3-hour window (Josh: 0.5mm), against the 2mm the whole-day
# category uses; the conditional/ECMWF/neighbour machinery is daily-total logic and is switched
# OFF for frames (Josh's option 2), so a frame shows the straightforward category for its own
# window and the daily panel stays the official product.
# Frames live UNDER docs/archive because the workflow's commit step adds "docs/outlook.json
# docs/archive" and changing that list needs the workflow OAuth scope; move them to docs/frames
# when that scope is next available.
# Single off switch for the whole frame product: set FALSE and the pipeline behaves exactly as it
# did before 14 Sep 2026 -- no frames computed, no frame files written, outlook.json unchanged.
# See ROLLBACK.md.
ENABLE_FRAMES <- TRUE
FRAME_DAYS  <- 8   # 4 -> 8 on 14 Sep 2026: soundings for days 5-8 are already computed too, so the
                   # only cost is four more ~200KB files, and the viewer loads one day at a time
FRAME_HOURS <- 3
FRAME_TRIG  <- 0.5
FRAME_DIR   <- file.path(ARCHIVE_DIR, "frames")
FRAME_COLS  <- c("cat","tprob","hail","wind","flood","cape","shear","ship","rain")
# ---- vertical resolution, and the API key that pays for it (14 Sep 2026) ----
# Open-Meteo prices a request as max(1, variables*models/10) * max(1, days/14) * locations, so our
# one-location sounding costs (levels*5 + 10 surface)/10 calls -- 9 at 16 levels, 19.5 at 37. The
# free tier allows 10,000 calls a DAY, which one 1032-point run has always blown through partway;
# that, not the code, is what produced the endless HTTP 429s, the 88-476 first-pass failures and
# the hour-long runs. So the level count is tied to whether a key is present: with one we use the
# full 37 levels Open-Meteo actually serves below 100hPa, without one we stay on the old 16 and the
# run still fits the shape it always had. Never hard-code the key here -- this repo is public. It
# comes from the OPENMETEO_KEY secret via the workflow's env.
OM_KEY <- Sys.getenv("OPENMETEO_KEY", "")
# 16 levels leaves 100hPa gaps right through the mid-levels (900-850-800-700-600-500), which is
# exactly where CAPE integration, lapse rates and DCAPE are most sensitive. The full set halves
# every one of those gaps.
LEVELS_BASE <- c(1000,975,950,925,900,850,800,700,600,500,400,300,250,200,150,100)
LEVELS_FULL <- c(1000,975,950,925,900,875,850,825,800,775,750,725,700,675,650,625,600,575,550,525,
                 500,475,450,425,400,375,350,325,300,275,250,225,200,175,150,125,100)
LEVELS <- if (nzchar(OM_KEY)) LEVELS_FULL else LEVELS_BASE
FDAYS  <- 8                                     # forecast days
TOPN   <- 6                                     # average the N highest-severity hours

# --- LEAD-TIME CALIBRATION, 18 Sep 2026 -------------------------------------------------
# The first calibration in this project made against real observations rather than against our
# own output. pipeline/verify.py scores every archived run over NOAA's CPC gauge analysis; on
# the first 12 September 2026 days the storm-day frequency bias under a flat 2mm trigger was
#     day 1-5: 1.11 1.09 1.04 1.13 1.20     day 6-8: 2.28 2.71 2.47
# i.e. at range we painted two to three times as many storm days as actually happened. Raising
# the trigger with lead returns every lead to ~1.0, and at days 7-8 it also RAISES skill
# (CSI 0.076 -> 0.087 and 0.095 -> 0.135) because the removed area was almost entirely false.
#
# Note this is the OPPOSITE of what a forecast-vs-forecast comparison suggested. Measured
# against our own day-1 output the long-lead storm area looked too SMALL, so the indicated fix
# was to lower the trigger at range. Day 1 was itself over-forecasting by 1.11, so "smaller
# than day 1" still meant bigger than reality. Only the gauge data separated the two.
LEAD_TRIG <- c(2, 2, 2, 2, 2.5, 5, 8, 8)        # rain trigger (mm) by lead, day 1 first
# Rungs picked on CSI as well as bias, not bias alone. Scored over the 12 gauge-verified days
# this ladder is the only candidate that beats the old flat 2mm on BOTH: mean CSI 0.179 vs
# 0.177, and mean |bias-1| 0.114 vs 0.628.
#   day 4 left at 2mm  -- lifting it traded 7% of the day's skill for a bias move of 1.13->0.90,
#                         which is no closer to 1. Not worth it.
#   day 6 at 5 not 6   -- 6mm scored bias 1.07 but halved CSI (0.078->0.043) and made FAR
#                         slightly WORSE, i.e. it was cutting blind. 5mm keeps bias at 1.22,
#                         in line with day 7, and recovers most of the skill.
# Day 6 is weak whatever we do: CSI there falls monotonically as the trigger rises, so no rung
# buys both honesty and skill. At 0.05 it is barely distinguishable from chance -- a limit of
# the model at that range, not of this calibration.

# Severe (MRGL+) composites are deflated slightly at day 2, where MRGL+ point-days ran ~25%
# above the day-1 analysis of the same dates. UNVERIFIED against observations: a rain gauge
# cannot tell MRGL from MDT, so this rests on forecast-vs-forecast evidence and is deliberately
# a single number to back out. Set to 1.0 to disable.
LEAD_SEV_K <- c(1.0, 0.875, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)

# Severity ceiling by lead. At days 6-8 the storm-day CSI is ~0.08 -- barely distinguishable
# from chance -- so a confident MDT/HIGH call there asserts far more than the model supports.
MAX_CAT_BY_LEAD <- c(4, 4, 4, 3, 3, 2, 2, 2)    # day 1-3 may reach HIGH, 4-5 MDT, 6-8 MRGL

lead_of <- function(lead) max(1L, min(8L, as.integer(lead) + 1L))   # 0-based lead -> 1-based index

# TSTM floor scales with mid-level temperature instead of being a flat 150 J/kg (Josh,
# 18 Sep 2026: "cold season storms can develop with CAPE values over 200, warm season storms
# with CAPE values over 500"). The calendar is the wrong switch for this -- what actually makes
# a modest CAPE productive is cold, steep mid-levels, which is why a cold-season 200 J/kg day
# works and a tropical 300 J/kg day under a warm 500hPa does not. A month-based rule would also
# be wrong year-round in the tropics and ambiguous at the shoulder.
#
# Caught on the 17 Sep run: 24 points along the QLD coast were drawn TSTM for 24 Sep, including
# one near Cairns with a 500hPa temperature of -5.1 C and an 850-500 lapse rate of 4.8 C/km --
# a warm, stable mid-troposphere in which no amount of low-level CAPE produces a storm. Under
# this floor that point needs 500 J/kg and has 190, so it drops, while Rockhampton (-14.7 C,
# 7.4 C/km, 480 J/kg) needs 332 and is kept.
tstm_floor <- function(t500){
  if (is.na(t500)) return(350)                  # midpoint when the level is missing
  if (t500 <= -20) return(200)
  if (t500 >= -8)  return(500)
  200 + 300 * (t500 + 20) / 12                  # linear between the two anchors
}

# Day 1's anchor date. The job runs at 18Z (02:00 AWST/04:00 AEST) specifically so it's
# ready right after Australian local midnight -- but 18Z is still the SAME UTC calendar
# day, so anchoring "Day 1" to raw UTC "today" (via forecast_days) labelled every run with
# the date that had just ended in Australia, one day stale by the time anyone looked at it.
# Anchoring instead to the AWST calendar date at request time (UTC+8, the country's LAST
# timezone to roll over each day) fixes this for both the 18Z schedule and any ad-hoc
# manual run: Day 1 always comes out as whichever Australian day has most recently started.
START_DATE <- format(Sys.time() + 8*3600, "%Y-%m-%d", tz="UTC")
END_DATE   <- as.character(as.Date(START_DATE) + FDAYS - 1)

# ---- historical reconstruction mode (added 9 Sep 2026) ----
# A one-off run anchored to a PAST Day 1, built from Open-Meteo's archive of past model runs:
# historical-forecast-api.open-meteo.com serves the forecast exactly as it was issued at the
# time, with the same variables as the live endpoint (verified live for 2025-11-01: all 192
# hours, every pressure level, soil moisture, freezing level and the ECMWF model all present),
# so the result is what this outlook WOULD have shown that morning -- not a hindcast fitted to
# what later happened. Triggered by pipeline/historical_run.txt holding a YYYY-MM-DD date (or
# the OUTLOOK_HIST_DATE env var for local use), and in Actions ONLY on a manual
# workflow_dispatch: the scheduled 18Z run ignores the file entirely, so leaving it in the repo
# can never hijack the live outlook. Output goes to docs/archive/<date>.json only -- never to
# docs/outlook.json -- and the date is pinned in archive/pinned.json so the rolling prune at the
# bottom of this script leaves it alone. run_date is stamped as what the live schedule would
# have written that morning (18Z the previous UTC day = 02:00 AWST on Day 1).
HIST_FILE <- "pipeline/historical_run.txt"
HIST_DATE <- NULL
hist_candidate <- Sys.getenv("OUTLOOK_HIST_DATE", "")
if (hist_candidate == "" && file.exists(HIST_FILE) && identical(Sys.getenv("GITHUB_EVENT_NAME"), "workflow_dispatch"))
  hist_candidate <- trimws(readLines(HIST_FILE, warn=FALSE)[1])
if (grepl("^\\d{4}-\\d{2}-\\d{2}$", hist_candidate) && as.Date(hist_candidate) < as.Date(START_DATE)) HIST_DATE <- hist_candidate
if (!is.null(HIST_DATE)){
  START_DATE <- HIST_DATE
  END_DATE   <- as.character(as.Date(START_DATE) + FDAYS - 1)
  cat(sprintf("HISTORICAL RECONSTRUCTION: Day 1 = %s, from the Open-Meteo historical-forecast archive\n", START_DATE))
}
# The key is only valid on the customer endpoint, and only for the live forecast API -- the keyed
# historical host answers 403 -- so a historical reconstruction stays on the free archive host and
# on the 16-level set it was built with.
# braces are load-bearing here: at top level R ends the statement at the end of the if-branch, so
# a bare `else` starting the next line is a parse error ("unexpected 'else'"). Keeping `} else` on
# one line is what makes a multi-line conditional legal outside a function body.
OM_HOST <- if (!is.null(HIST_DATE)) {
             "https://historical-forecast-api.open-meteo.com"
           } else if (nzchar(OM_KEY)) {
             "https://customer-api.open-meteo.com"
           } else {
             "https://api.open-meteo.com"
           }
OM_AUTH <- if (nzchar(OM_KEY) && is.null(HIST_DATE)) paste0("&apikey=", OM_KEY) else ""
if (!is.null(HIST_DATE)) LEVELS <- LEVELS_BASE
cat(sprintf("Open-Meteo: %s, %d pressure levels, %d variables/request (~%.1f API calls per point)\n",
            if (nzchar(OM_AUTH)) "keyed customer endpoint" else "free endpoint (no OPENMETEO_KEY set)",
            length(LEVELS), length(LEVELS)*5 + 10, max(1, (length(LEVELS)*5 + 10)/10)))

nz <- function(x){ if (is.null(x) || is.na(x)) 0 else x }

# Grid spacing, read from the grid itself rather than hard-coded, so the neighbour rule keeps
# meaning "the 8 surrounding points" if the lattice ever changes again. 1.22 * spacing is the old
# fixed 1.0 deg expressed against the 0.82 deg grid it was written for.
GRID_STEP <- local({
  d <- diff(sort(unique(round(GRID[,1], 2)))); d <- d[d > 0]
  if (!length(d)) 0.82 else as.numeric(names(sort(table(round(d, 2)), decreasing=TRUE))[1])
})
NB_RADIUS <- 1.22 * GRID_STEP
cat(sprintf("Grid: %d points, %.2f deg spacing (~%.0f km), neighbour radius %.2f deg\n",
            nrow(GRID), GRID_STEP, GRID_STEP*111, NB_RADIUS))

dewpoint <- function(T, RH){
  RH[is.na(RH)] <- 1; RH[RH < 1] <- 1
  a <- 17.625; b <- 243.04
  g <- log(RH/100) + (a*T)/(b+T)
  (b*g)/(a-g)
}

# continuous severity score used to rank the hours of a day
# REVERTED to SCP_new/STP_new on 27 Aug 2026: tried SCP_new_LM/STP_new_LM (thundeR's own
# Southern-Hemisphere-documented left-mover variants -- the bare versions use the Northern
# Hemisphere right-moving-supercell convention, which is the wrong hemisphere for this country)
# but the switch was incomplete on its own. Checked live: STP_new_LM came back as a flat 0 on
# every single elevated-risk (cat>=2) point-day in the validation run, and SCP_new_LM was
# overwhelmingly negative even on those same days (range -1.1 to +0.2, never reaching the 0.9
# threshold that promotes a day to MRGL) -- the field choice is the documented-correct one, but
# the existing absolute thresholds below were tuned against the OLD field's typical range and are
# now essentially unreachable against the new one's much smaller/differently-signed values, so
# SCP/STP silently stopped contributing to the category system almost entirely rather than just
# being hemisphere-imperfect. Reverted to the known-working (if hemisphere-imperfect) fields until
# the _LM switch can be redone together with a proper threshold recalibration against real data.
sev_score <- function(p){
  cape <- nz(p[["MU_CAPE"]]); shr <- nz(p[["BS_EFF_MU"]])*1.94384
  scp  <- sh_composite(p, "SCP_new"); stp <- sh_composite(p, "STP_new"); ship <- nz(p[["SHIP"]])   # left-mover composites, see sh_composite()
  2*scp + 2*stp + 2*ship + cape/500 + shr/20
}

# daily accumulated rainfall (mm, GFS's own total precip forecast) -> the same 0-4 scale,
# folded into the composite category below: any hazard capable of occurring on the day --
# hail, wind, tornado, or flash-flood rain -- lifts the overall risk shown on the map.
# RESCALED 26 Aug 2026 alongside categorise_vals()'s SLIGHT removal (0-5 -> 0-4): the old
# 50mm rung mapped onto SLIGHT, which no longer exists as a tier -- dropped rather than
# folded into either neighbour, so 35-74mm now reads as MRGL and 75mm+ as MDT directly.
rain_cat <- function(mm){
  if (mm >= 150) return(4)
  if (mm >= 75)  return(3)
  if (mm >= 35)  return(2)
  if (mm >= 10)  return(1)
  0
}

# hail size tier: 0 none/sub-severe, 1 small (0-3cm), 2 large (3-6cm), 3 very large (6cm+).
# Size bands widened 26 Aug 2026 (was <2/2-5/>5cm) -- no change to the tier PROMOTION logic
# below, just to where the resulting tier is labelled on the map (see docs/index.html).
# SHIP is SPC's own significant-hail parameter (calibrated to >=2in/5cm hail potential), so
# its own 1/2 breakpoints are kept as the primary driver -- an approximate mapping onto the new
# bands rather than a literal recalibration, since SHIP itself doesn't have a citable 3cm/6cm
# formulation to draw from. frz_lvl_m is the altitude of the 0C level during the day's most
# unstable hours -- Raupach et al. 2023 (Mon. Wea. Rev. 151, doi:10.1175/mwr-d-22-0127.1) found
# melting-level height is the key variable needed to correct naive instability-shear hail
# proxies for Australia, and specifically that proxies without it OVERESTIMATE hail probability
# in Australia's tropical north. Both directions here follow that finding: a lower freezing
# level gives a falling hailstone less distance to melt (cold_aloft bumps the tier up), while an
# unusually high freezing level -- common in the tropics -- gives it much more distance to melt
# before reaching the ground (warm_aloft pulls the tier back down). The specific 3400m/4900m
# cutoffs are our own working thresholds from general hail-forecasting practice, not numbers
# taken from the paper (which uses a continuous correction, not a step), so treat the exact
# breakpoints as approximate even though the melting-level concept is now directly evidenced
# for Australia.
# t500 ADDED 26 Aug 2026 as a second, independent cold-aloft signal: freezing-level height alone
# is a proxy for melt distance, but says nothing about how cold the hail-growth layer itself is
# -- two days can share the same freezing level while one has a much colder mid-troposphere
# above it, which grows larger stones before they ever start falling. 500hPa temperature is the
# standard level used for exactly this in operational hail forecasting (a "cold pool aloft"
# check independent of surface/low-level conditions). -20C is the general large-hail-supportive
# benchmark at that level; either cold signal (low freezing level OR cold 500hPa) is now enough
# to trigger cold_aloft, so a day that's cold aloft in EITHER sense gets caught, not just the
# freezing-level case alone -- this pipeline previously only ever looked at freezing level.
# REBUILT 24 Sep 2026 (Josh: "the hail risks were wildly overestimated"). The 23 Sep run
# published 34 points at Very large (6cm+) whose DAILY MEAN SHIP was 0.40-1.80, median 0.80,
# against 0 that raw SHIP alone would have given. Two amplifiers were compounding:
#
#   1. The cold-aloft promotion fired almost everywhere. CAPE >= 300 with 500hPa <= -20C is
#      routine over southern Australia in September, so nearly every hail point was promoted a
#      full tier, and a promotion could reach tier 3 -- i.e. 6cm+ hail asserted off SHIP ~1.
#      It is now CAPE >= 500 with a 3000m freezing level or -25C aloft, and it can only lift
#      Small to Large. Very large hail must be earned on SHIP alone.
#   2. hail_tier() is fed the single PEAK-SHIP hour while everything else uses the top-6-hour
#      mean, so the number behind a hail tier is always higher than the SHIP shown in the
#      tooltip. That stays -- giant hail genuinely is a 1-2 hour window (see day_topN) -- but
#      it means the bars must be set against peak-hour SHIP, which is what the bands below do.
#
# Bands are Josh's, 24 Sep 2026: 0.5-1.5 small, 1.5-3 large, 3+ very large. These sit well
# above where our SHIP usually reaches (peak-hour p99.9 is 1.0, max 2.3 across the archive),
# and that is deliberate: 6cm+ hail should be close to dormant outside a genuinely extreme
# summer day. A top tier that fires 34 times in a quiet September is the bug, not the fix.
hail_tier <- function(ship, cape, frz_lvl_m, t500){
  base <- if (ship >= 3) 3 else if (ship >= 1.5) 2 else if (ship >= 0.5 & cape >= 300) 1 else 0
  # COLD-ALOFT PROMOTION REMOVED 25 Sep 2026. Even after being tightened to CAPE 500 with a
  # 3000m freezing level, it was INVERTING the hail field. On the 27 Sep panel the SW corner of
  # WA drew Large hail on SHIP 0.30 and SCP 1.3, while the Goldfields drew only Small on SHIP
  # 1.00 and SCP 9.5 -- a textbook supercell environment rated below a cold front. The whole
  # difference was the freezing level: 2740m on the coast against 3320m inland.
  #
  # The physics it came from is real (Raupach et al. 2023: melting-level height is what naive
  # instability-shear proxies miss over Australia, and they OVERESTIMATE hail without it). But
  # it was applied as a full tier promotion with no requirement that a hail-producing updraft
  # exist at all, so a maritime cold front with 780 J/kg outranked a supercell. A low freezing
  # level means less melting of whatever hail forms; it is not itself a reason to expect hail.
  # If it returns it should MODULATE the SHIP bars, not add a tier on top of them.
  warm_aloft <- !is.na(frz_lvl_m) & frz_lvl_m > 4900
  if (warm_aloft & base >= 1) base <- base - 1    # kept: a very high freezing level melts hail out
  base
}

# fire danger tier: 0 none/low-moderate, 1 Moderate, 2 High, 3 Extreme, 4 Catastrophic.
# BOM/AFDRS's own gridded fire danger ratings aren't available through any public,
# machine-readable feed at the point-level, 8-day-ahead resolution this pipeline needs (checked:
# the RFS's public feed is fire-district-level Total-Fire-Ban text for today/tomorrow only, not a
# raw forecast grid; other BOM AFDRS distribution is map-tile/GIS product aimed at human viewing,
# not a documented API for an unauthenticated automated pull). So this instead computes a proxy
# from the McArthur Mark 5 Forest Fire Danger Index -- a real, long-published formula:
#   FFDI = 2 * exp(-0.45 + 0.987*ln(DF) - 0.0345*RH + 0.0338*T + 0.0234*V)
#   (T degC, RH %, V km/h, DF = Drought Factor 0-10)
# The real DF comes from BOM's Griffith Drought Factor, itself built off a multi-week
# Keetch-Byram-style rainfall history this pipeline doesn't fetch. Substituted here with a DF
# proxy from GFS's own forecast shallow soil moisture (soil_moisture_0_to_1cm): the model's
# land-surface scheme already integrates antecedent rainfall into that value, so no separate
# historical-lookback fetch is needed the way it would be for a literal drought index. A day
# with meaningful forecast rain of its own additionally caps the tier at Moderate outright,
# since falling rain suppresses fire spread even where the soil started dry.
# Tiers are loosely anchored to the well-known published McArthur breakpoints (FFDI 12/25/50/100)
# collapsed onto the 4 requested labels -- NOT the official modern AFDRS numeric thresholds,
# which vary by fuel type/jurisdiction and aren't published as one simple formula. Treat this as
# a general fire-weather-risk indicator, not an authoritative rating.
#
# split in two: ffdi_hour() is the per-hour physics (day_topN calls it once per hour and keeps
# the day's WORST hour, since fire danger is about the peak burn-period window, not a daily
# average that would wash out an afternoon spike with cool overnight hours); fire_tier() maps
# that peak value to a tier and applies the same-day-rain cap.
ffdi_hour <- function(temp_c, rh, wind_kmh, soil_m){
  wetness <- (nz(soil_m) - 0.05) / (0.35 - 0.05)   # 0.05..0.35 m3/m3 ~ dry..wet working range
  wetness <- min(1, max(0, wetness))
  DF <- max(1, 10 * (1 - wetness))
  2 * exp(-0.45 + 0.987*log(DF) - 0.0345*nz(rh) + 0.0338*nz(temp_c) + 0.0234*nz(wind_kmh))
}
fire_tier <- function(ffdi, rain_mm){
  tier <- if (ffdi >= 100) 4L else if (ffdi >= 50) 3L else if (ffdi >= 25) 2L else if (ffdi >= 12) 1L else 0L
  if (nz(rain_mm) >= 10) tier <- min(tier, 1L)
  tier
}

# damaging-wind tier: 0 none, 1 Damaging (>=90km/h gust potential), 2 Destructive (>=125km/h),
# 3 Very Destructive (>=160km/h). 90 and 125km/h are BOM's own criteria for issuing and escalating
# a Severe Thunderstorm Warning for damaging/destructive winds; 160km/h is this pipeline's own
# extension for the rarer very-destructive tier.
#
# REBUILT 10 Sep 2026 on downdraft physics (per Josh: "build the DCAPE pipeline"). The previous
# version used only WNDG = (CAPE/2000)*(shear/20) -- instability x organisation -- which says
# nothing about the DOWNDRAFT that actually produces damaging surface gusts, so a hot, dry,
# high-based inland environment with modest CAPE and weak shear (a classic Australian microburst
# setup) scored ~0 while a moist, sheared coastal supercell environment scored highest whether or
# not wind was its main hazard. It also double-counted the CAPE/shear already driving the category.
# Inputs now, each the mean over the day's top-N hours:
#   dcape  thundeR's DCAPE (J/kg): evaporative-cooling downdraft potential, the core signal.
#          ~700 is where damaging gusts become plausible, 1000+ strong, 1300+ extreme.
#   dd700  700hPa dewpoint depression (degC) from the hourly fields: dry mid-levels feed the
#          evaporative cooling; >= 8 supportive, >= 12 very dry.
#   lr03   0-3km lapse rate (degC/km): a steep, deep-mixed boundary layer lets a downdraft
#          accelerate to the surface; >= 7 supportive.
#   dcp    thundeR's Derecho Composite Parameter (DCAPE x MUCAPE x 0-6km shear x mean wind):
#          the organised-system (bow echo / derecho) route to destructive winds; ~1 supportive,
#          2+ strongly so.
#   cape / shr_kt: storm intensity and organisation, as before but no longer the whole story.
# Gate is now TIERED (was a flat MDT gate): Damaging is reachable from MRGL, since damaging gusts
# are common on marginal days -- a downburst needs a storm, not a moderate-risk environment --
# while Destructive and above still need MDT+ (see the end of the function). TSTM-only days (no
# severe environment at all) and rain-gated days stay zero.
# Breakpoints are physically standard (SPC's DCAPE and DCP guidance, the 700hPa dryness rule of
# thumb) but hand-assembled into tiers; there is no citable DCAPE-to-gust-speed conversion, so
# treat exact tier edges as approximate the same way hail_tier()'s are.
wind_tier <- function(dcape, dd700, lr03, dcp, cape, shr, cat){
  # entry gate is TSTM (was MRGL): damaging gusts inside a general storm chance are real, and
  # day_topN() upgrades any such day to MRGL straight after, so the published map still never
  # carries a Damaging tier below MRGL -- it arrives there by lifting the category, not by
  # showing winds under it. Destructive+ still needs MDT (the cap at the end of this function).
  if (nz(cat) < 1) return(0L)
  dcape <- nz(dcape); dd700 <- nz(dd700); lr03 <- nz(lr03); dcp <- nz(dcp); cape <- nz(cape)
  shr_kt <- nz(shr) * 1.94384
  very_destructive <- (dcape >= 1300 & shr_kt >= 40 & cape >= 2000) | dcp >= 3
  destructive      <- (dcape >= 1000 & shr_kt >= 30 & cape >= 1000) | (dcape >= 1300 & dd700 >= 12) | dcp >= 1.5
  # DCP route carries a DCAPE >= 500 floor (Josh, 11 Sep 2026): on the first live run half the
  # Damaging points came in on DCP alone with DCAPE 410-560, i.e. no real downdraft signal.
  damaging         <- (dcape >= 700 & (dd700 >= 8 | lr03 >= 7 | shr_kt >= 25)) | (dcp >= 0.5 & dcape >= 500)
  tier <- if (very_destructive) 3L else if (destructive) 2L else if (damaging) 1L else 0L
  # Tiered gate (Josh, 10 Sep 2026): Damaging (90-125km/h) is reachable from MRGL -- a marginal
  # day can and does produce damaging gusts -- but Destructive (125km/h+) and Very Destructive
  # additionally need the day's overall environment at MDT or higher. That is on top of, not
  # instead of, the parameter conditions above: a moderate day still has to have the downdraft
  # and organisation numbers line up to reach those tiers.
  if (nz(cat) < 3) tier <- min(tier, 1L)
  tier
}

# Excessive Rainfall Outlook: 3-tier flash-flood risk (0 none, 1 slight, 2 moderate, 3 high),
# modelled on the US Weather Prediction Center's own Excessive Rainfall Outlook. WPC's real ERO is
# built from genuine ensemble probability -- the % of ensemble members whose forecast exceeds a
# given rainfall threshold. This pipeline only calls Open-Meteo's single deterministic GFS run
# (not its separate ensemble/GEFS endpoint), so there is no true "25% of members" figure available
# here without a second API call per point, which would double per-point request volume and
# runtime for an 8-day, ~1000-point grid that already sees some transient per-point failures.
# As a proxy instead: a tier fires when the DETERMINISTIC forecast -- either the day's 24h total
# (rain_mm) or its single highest hourly rate (rate_mm) -- reaches that tier's magnitude
# threshold, gated by Open-Meteo's own precipitation_probability (pop, its ensemble-based
# confidence that measurable rain occurs at all that day) clearing 25%. That reuses pop as a floor
# on model confidence rather than as the literal "chance of exceeding X mm" the thresholds are
# framed around -- treat this as a magnitude-tiered rainfall outlook gated by model confidence,
# not a true probabilistic ERO, unless/until the pipeline adds a per-point ensemble call.
# TROPIC-OF-CAPRICORN SPLIT added 26 Aug 2026: the thresholds above were set against tropical-
# north rainfall climatology, where a 500mm/24h day, while extreme, is a real monsoon/tropical-low
# event that happens most wet seasons. That same 500mm in temperate southern Australia is far
# further outside the ordinary range and far more damaging relative to what the ground/drainage
# there is built for -- the fixed national threshold was letting genuinely excessive southern rain
# events go unflagged because they never approached the north's bar. South of -23.5 deg (the
# Tropic of Capricorn), thresholds are scaled to 40% of the northern figures; north of it, unchanged.
flood_cat <- function(rain_mm, rate_mm, pop, lat){
  if (nz(pop) < 25) return(0L)
  rm <- nz(rain_mm); rt <- nz(rate_mm)
  f <- if (nz(lat) > -23.5) 1 else 0.4
  if (rm >= 500*f | rt >= 250*f) return(3L)   # high
  if (rm >= 250*f | rt >= 175*f) return(2L)   # moderate
  if (rm >= 150*f | rt >= 100*f) return(1L)   # slight
  0L
}

# thunderstorm-chance gate: Open-Meteo's precipitation_probability is "probability of any rain",
# not thunderstorm-specific, and there's no real thunderstorm-probability field available for this
# region/model (checked: ECMWF's lightning_density field exists in Open-Meteo's UI but returns no
# data for any location when queried live, so it isn't usable). This keeps the raw rain-probability
# proxy from showing a nontrivial "thunderstorm chance" on days GFS's own deterministic forecast
# doesn't actually expect meaningful rain for -- zeroed below the same 2mm/24h no-rain gate the
# category uses (see categorise_vals()), heavily discounted when the day's CAPE is negligible even
# if some rain probability remains.
# SCALE FACTORS HALVED 25 Aug 2026: even outside the low-CAPE case, showing the raw "any rain"
# probability unmodified overstated true thunderstorm chance across the board -- "probability of
# any rain" is a strictly larger, easier-to-satisfy event than "probability of a thunderstorm"
# specifically (stratiform/frontal rain with no convection at all still counts toward Open-Meteo's
# figure), so the whole map read as systematically too high, confirmed live 25 Aug 2026. Both
# branches scaled down by the same 0.5 factor -- 0.3->0.15 for the already-discounted low-CAPE
# case, 1.0->0.5 for the previously-undiscounted case -- keeping the low-CAPE case discounted
# further than the higher-CAPE one, just at half the previous magnitude throughout.
thunder_prob <- function(tprob, cape, rain_mm){
  if (nz(rain_mm) < 2)   return(0L)
  if (nz(cape) < 100)    return(as.integer(round(nz(tprob) * 0.15)))
  as.integer(round(nz(tprob) * 0.5))
}
# Thunderstorm-chance consistency floor (7 Sep 2026). A Category Outlook tier means storms are
# expected in the area, so the Thunderstorm Chance pane must show at least "Chance" (20 -- the
# viewer's lower tprob band, see HAZ_SPECS.tprob in docs/index.html) wherever cat >= 1, and
# "Likely" (80) wherever cat >= 3, since MDT/HIGH imply storms are all but certain. Before this,
# 234 of the 385 category point-days in one run had tprob < 20: thunder_prob() halves Open-Meteo's
# mean precip probability, which over the dry interior sits at 10-30% even on days the sounding
# parameters clearly support storms, so the category pane showed TSTM/MRGL over areas the thunder
# pane left blank. Applied everywhere cat is set -- both day_topN() branches and the ECMWF
# second-opinion un-gating -- so the two panes can never contradict each other.
tprob_floor <- function(tprob, cat){
  if (nz(cat) >= 3) max(nz(tprob), 80L) else if (nz(cat) >= 1) max(nz(tprob), 20L) else nz(tprob)
}

# SPC-style category from averaged parameters: 0 none,1 TSTM,2 MRGL,3 MDT,4 HIGH
# SLGT was removed 26 Aug 2026 (on top of ENH's earlier removal): four tiers reads cleaner than
# five and SLGT sat in a spot forecasters and the general public alike found hard to distinguish
# from MRGL at a glance. Its own former threshold isn't reassigned anywhere -- a day that would
# have hit SLGT under the old scale now simply stays at MRGL, it doesn't get folded upward into
# MDT -- so MDT keeps exactly the bar it already had (unchanged from the ENH removal above) and
# still reads as the practical ceiling most genuinely significant days reach. HIGH also keeps its
# old, unchanged, deliberately extreme bar -- explicitly for exceptional, potentially
# life-threatening outbreak days only, not a normal "top of the scale" tier.
# cin = MU_CIN (J/kg, <=0, from the same sounding as cape/shr) is a suppression check on an
# already-triggering day (see capped below). rain_mm -- the day's accumulated 24h total precip --
# is the actual initiation gate: CAPE/SCP/STP/SHIP alone are a very low bar in the moist tropics,
# so a thermodynamically favorable sounding was firing a category even on days GFS's own
# deterministic forecast expected essentially no rain for (the northern-Australia false-positive
# spotted live 22 Aug 2026 -- elevated instability parameters, negligible forecast rainfall).
# Below 2mm/24h the whole instability-derived category is zeroed rather than firing on bare
# environment; the same rain_mm is independently folded back in via rain_cat() so heavy-rain days
# still show up even when the severe-hazard parameters alone wouldn't flag anything. (Previously
# gated on shw, GFS's own convective-showers diagnostic, at a 0.1mm bar -- too lenient, and a
# narrower signal than the day's actual total precip forecast; shw is no longer fetched.)
#
# CAPE/SCP/STP/SHIP threshold NOTE: the numeric cutoffs below (SCP 1/2/4/6/10, STP 1/2/3/5,
# SHIP 0.5/1/2/3, and the CAPE+shear combo gates) are the US Storm Prediction Center's own
# values, calibrated against the US Great Plains severe-report climatology. Published Australian
# work -- Allen, Karoly & Mills 2011, "A severe thunderstorm climatology for Australia and
# associated thunderstorm environments," Aust. Met. Ocean. J. 61, doi:10.22499/2.6103.001; Allen
# & Karoly 2013, "A climatology of Australian severe thunderstorm environments 1979-2011," Int.
# J. Climatol., doi:10.1002/joc.3667 -- built a real Australian severe-thunderstorm-report
# database (2003-2010) and derived their own CAPE/deep-layer-shear discriminants from proximity
# soundings against it, rather than reusing the US SPC numbers unmodified. That confirms
# region-specific recalibration is the right thing to do here in principle. What this pipeline
# does NOT have: the actual fitted discriminant values from those papers, or the underlying
# report database to fit its own. Both papers are paywalled past their abstracts, and web-search
# summaries of their content returned mutually inconsistent numbers for the same discriminant
# (checked and rejected during this session rather than trusted) -- so no specific numeric
# threshold from them has been verified well enough to put into a live public product. A uniform
# ~10% reduction is applied here (dropped from an earlier ~20% on the CAPE/SCP/STP/SHIP terms,
# now matching the ~10% already used on the wind-speed/shear terms) as an unvalidated directional
# nudge, not a fitted recalibration -- treat category boundaries as approximate until either the
# papers' actual numbers or a real Australian severe-report dataset are available to fit against.
#
# SCP CAPE-FLOOR NOTE: STP and SHIP both have CAPE as a direct multiplicative term in their own
# formula, so a high STP/SHIP already implies decent instability was present -- they self-limit.
# SCP does not: its shear term saturates (capped at 1.0 past 20 m/s) but its SRH term does not,
# so strong deep-layer shear/helicity alone can push SCP past these thresholds even with fairly
# ordinary CAPE, which is exactly the cool-season "strong shear, modest instability" pattern
# common with vigorous southern-Australia winter fronts -- confirmed live 16 Aug 2026 (SW WA,
# cape ~600-800 J/kg, scp 1.7-2.3 firing SLGT with hail/flood both 0 and tprob only ~25%, i.e.
# no other hazard signal at all). Every scp branch below now also requires cape >= 900, matching
# the bar the tier-3 cape+shear branch already uses (1000 J/kg at the same ~10% reduction) --
# the shear-only path shouldn't reach a higher tier with less instability than the explicitly
# CAPE-gated path at the same tier.
# ---- tropical coastal zone (12 Sep 2026) ----
# North of a Carnarvon -> Rockhampton line AND within 200km of the coast, the build-up/wet-season
# airmass carries high CAPE with modest shear almost daily and sea-breeze showers put a trace (or a
# couple of mm) of rain somewhere nearly every day, so rain is not the evidence of a severe-storm
# trigger there that it is further south -- seen live 12 Sep 2026 as a conditional MRGL band down
# the Kimberley coast off 0.1mm GFS / 0.9mm ECMWF. Per Josh: up here MRGL and above need >= 3mm of
# forecast rain, and the conditional (trace / neighbour) rules do not apply at all. TSTM -- the
# general, non-severe storm chance -- keeps the normal 2mm gate. The coast test uses the viewer's
# own simplified coastline (docs/coastline.geo.json, ~1800 vertices ~40km apart, so the 200km ring
# is good to about +/-20km), read once at load rather than per point.
coast_xy <- local({
  txt <- paste(readLines("docs/coastline.geo.json", warn=FALSE), collapse="")
  m <- regmatches(txt, gregexpr("\\[ *-?[0-9.]+ *, *-?[0-9.]+ *\\]", txt))[[1]]
  do.call(rbind, lapply(m, function(p) as.numeric(strsplit(gsub("\\[|\\]| ", "", p), ",")[[1]])))   # cols: lon, lat
})
coast_km <- function(lat, lon){
  dlat <- (coast_xy[,2] - lat) * pi/180; dlon <- (coast_xy[,1] - lon) * pi/180
  a <- sin(dlat/2)^2 + cos(lat*pi/180) * cos(coast_xy[,2]*pi/180) * sin(dlon/2)^2
  min(6371 * 2 * asin(sqrt(pmin(1, a))))
}
tropical_coastal <- function(lat, lon){
  line_lat <- -24.88 + (lon - 113.66) * ((-23.38 - (-24.88)) / (150.51 - 113.66))   # Carnarvon -> Rockhampton
  isTRUE(lat > line_lat && coast_km(lat, lon) <= 200)
}

# strict=TRUE marks the tropical coastal zone: 3mm floor for MRGL+, no conditional un-gating
# trig: the rain total that counts as a trigger for this window (2mm for a whole day, FRAME_TRIG
# for a 3-hour frame). The trace and tropical bars are expressed as multiples of it so they scale
# together and the daily numbers stay exactly what they were (0.1*2 = 0.2mm, 1.5*2 = 3mm).
# allow_conditional=FALSE switches off the trace/neighbour downgrade path for frames.
categorise_vals <- function(cape, shr, scp, stp, ship, cin, rain_mm, strict=FALSE,
                            trig=2, allow_conditional=TRUE, t500=NA, sev_k=1){
  shr_kt <- shr * 1.94384
  c <- 0
  if (cape >= tstm_floor(t500)) c <- 1                                                      # TSTM
  # sev_k deflates only the SEVERE composites, never the TSTM floor above -- the storm-day
  # frequency bias is already near 1 at every lead we deflate, so the general storm area must
  # not move. Defaults to 1 (no change) for every caller that does not pass a lead.
  scpS <- scp * sev_k; stpS <- stp * sev_k; shipS <- ship * sev_k; capeS <- cape * sev_k
  # MRGL floor RAISED 13 Sep 2026 (Josh: "1000 J/kg and 25kts shear"), from CAPE 450 / 18kt.
  # 450 J/kg with 18kt is an ordinary shower environment, and it was admitting essentially every
  # storm day as a severe risk: on the 12 Sep run all 132 MRGL point-days qualified through that
  # route and 93 of them through it alone. The two old side-routes went with it, because a rule
  # that admits a weaker day than the stated floor IS the floor: SCP 0.9 with CAPE 900 is a
  # marginal supercell composite at best, and a bare SHIP 0.45 is well below any real hail signal
  # (SHIP now reaches MRGL through hail_tier instead, which needs ~1.0, or 0.5 with cold air
  # aloft). SCP survives at 2.5, which is a genuine supercell environment and is what keeps
  # high-shear / moderate-CAPE days (e.g. CAPE 600 with 50kt and SCP 3.3) from being under-called.
  # Large hail and damaging winds are the other way in -- see the upgrade in day_topN().
  # MRGL: the CAPE/shear floor is unchanged (Josh, 13 Sep). The SCP route moves 2.5 -> 3.0,
  # 24 Sep 2026: "supercells would automatically present a baseline marginal risk, so an SCP
  # greater than 3 should highlight marginal". SCP is the one composite of the three that our
  # data reaches properly (p99 3.8, max 14.7 across the archive), so it carries the ladder.
  if ((capeS >= 1000 & shr_kt >= 25) | scpS > 3) c <- max(c, 2)  # MRGL
  # MDT via the SCP route now also needs some STP or SHIP backing (the same 0.9 "sig" bar the
  # hatching uses) -- 7 Sep 2026, after a lone point at 24.0S 128.5E hit MDT on SCP 3.7 (vs the
  # 3.6 bar) with STP -0.6 and SHIP 0.7 on a day that was plainly not a 3-of-4 day. SCP alone is a
  # supercell-ENVIRONMENT composite; without any tornado or hail composite support it should
  # cap at MRGL. The direct STP/SHIP routes are unchanged.
  # MDT via SHIP uses a shear-dependent bar (Josh, 9 Sep 2026): 2.0 below 35kt, 1.5 at 35-50kt,
  # 1.2 above 50kt. Strong deep-layer shear organises the updraft that a given SHIP implies, so
  # the same hail composite means more in a 55kt environment than a 25kt one. Replaces both the
  # flat 1.8 bar and the interim "SHIP >= 0.9 is SIG" floor from earlier the same day.
  # MDT and HIGH REBUILT 24 Sep 2026 (Josh: "poor for MDT"). The old shear-banded SHIP bar and
  # the SCP 3.6 + supporting-composite route are both gone, replaced by an explicit ladder:
  #
  #   MDT   SCP >= 6                                    a clearly supercellular environment
  #         STP >= 2                                    "STP 2+ automatically moderate"
  #         CAPE >= 1500 AND (SHIP >= 2 OR SCP >= 6)     instability WITH organisation
  #         CAPE >= 3000 in an already-severe environment
  #   HIGH  SCP >= 8
  #         STP >= 5
  #         SCP >= 6 AND SHIP >= 3                       both at the same location
  #
  # STP below 2 is now ignored entirely ("anything below 2, ignore"), where it previously
  # contributed from 0.9. CAPE >= 1500 does NOT reach MDT on its own -- it needs SHIP or SCP
  # alongside it. On the archive the standalone reading gave 1,654 MDT point-days against 201
  # for this one, and 1,234 of those 1,654 were high CAPE with no organisation behind it at
  # all: pulse-storm air, not a moderate risk.
  sev_env <- (capeS >= 1000 & shr_kt >= 25) | scpS > 3        # already a severe environment
  mdt <- scpS >= 6 | stpS >= 2 |
         (capeS >= 1500 & (shipS >= 2 | scpS >= 6)) |
         (capeS >= 3000 & sev_env)
  if (mdt) c <- max(c, 3)                                                                   # MDT
  # HIGH (Josh, 9 Sep 2026): exceptionally potent only -- CAPE >= 4000, SHIP >= 2.5, SCP >= 9
  # ("huge") and 10mm+ rain, ALL required. The old (SCP>=9 & CAPE>=900) | STP>=4.5 routes are
  # gone: HIGH is meant to be the rare outbreak signal, not something one composite can reach on
  # its own. A tornado-composite day without that hail/instability backing still lands at MDT
  # via the SIG floor.
  if (scpS >= 8 | stpS >= 5 | (scpS >= 6 & shipS >= 3)) c <- max(c, 4)                      # HIGH

  capped  <- nz(cin) <= -75      # stout cap even on the best hour of the day
  no_trig <- nz(rain_mm) < trig   # the model's own precip forecast shows essentially no rain

  # PREGATE added 5 Sep 2026: the category this day would show on thermodynamics alone, before
  # the rain-trigger gate below -- read by apply_ecmwf_second_opinion() to know what to restore a
  # day to if GFS's own rain forecast (and only that) turns out to be what suppressed it. The CIN
  # discount is applied here UNCONDITIONALLY, not gated on !no_trig the way the single-pass
  # version below it was -- pregate must reflect the same capped-aware value cat would get on a
  # rain-clearing day, so un-gating a day later never skips the CIN-suppression physics just
  # because rain happened to be the thing that zeroed it. Behavior-neutral for every existing
  # caller: on a gated day c is forced to 0 immediately below regardless of pregate, so this
  # doesn't change cat's value for anything already live -- only pregate (new, previously unused)
  # is affected.
  rc <- rain_cat(nz(rain_mm))
  # SIG ("hatched" / significant-severe) is now defined up front so it can LIFT the category:
  # per Josh, 9 Sep 2026, anything SIG is at least MDT. The 1 Nov 2025 reconstruction showed why
  # -- SHIP 0.9-1.9 with CAPE 1600-3000 and 35-50kt shear across SE QLD/NE NSW came out MRGL
  # nearly everywhere (MDT then needed SHIP >= 1.8), on a day that verified as widespread
  # moderate with very large hail. SCP-alone is dropped from the SIG definition at the same
  # time, consistent with the 7 Sep MDT calibration (SCP is an environment composite, not a
  # hazard one; SCP-only support caps at MRGL). Heavy-rain tier 3 remains SIG.
  # SHIP's SIG bar is the same shear-dependent MDT bar above, so "SIG is at least MDT" stays true.
  # SIG (the hatched significant-severe marker, and the "anything SIG is at least MDT" floor)
  # follows the new bars: large hail, a real tornado composite, or extreme rain. STP's old 0.9
  # contribution is gone with the rest of sub-2 STP.
  sig <- stpS >= 2 | shipS >= 1.5 | rc >= 3

  pregate <- c
  if (capped & pregate >= 3) pregate <- pregate - 1
  if (sig & pregate >= 1) pregate <- max(pregate, 3)   # SIG floor sits AFTER the CIN discount: SIG is always at least MDT

  c <- pregate
  conditional <- FALSE
  if (no_trig) c <- 0
  # CONDITIONAL MRGL (11 Sep 2026): the 2mm gate is binary, and a strongly favourable environment
  # with only a TRACE of forecast rain (0.2-2mm) is not the same thing as a dry one -- it is a
  # low-probability, initiation-dependent setup, which is exactly what a Marginal risk means. Caught
  # live on 12 Sep 2026 over the Nullarbor: CAPE 1952, 44kt shear, SCP 6.6, SHIP 1.2, GFS 0mm, and
  # ECMWF showing 0-0.5mm blobs across the region, drawn as "No storms". So: a day whose
  # thermodynamics alone reach MRGL+ and that carries a trace in GFS shows as MRGL, capped there
  # (never MDT/HIGH off a trace), and flagged conditional. The same rule is applied for an ECMWF
  # trace in apply_ecmwf_second_opinion() and for >=2mm at an adjacent grid point in
  # apply_neighbour_trigger(). Genuinely dry days (<0.2mm in both models, nothing nearby) still gate.
  # A conditional trigger COSTS ONE CATEGORY (12 Sep 2026). Flat "conditional -> MRGL" painted a
  # 1200km MRGL blanket across the WA interior on 14 Sep: scattered 0.1-0.5mm drizzle traces, each
  # lifting itself and its neighbours, over garden-variety CAPE 500-1000 / 20-40kt (pregate 2, SHIP
  # 0.1-0.4). Uncertainty about whether storms form at all is exactly what TSTM means, so an
  # ordinary MRGL environment on a trace now reads TSTM, while a genuinely severe environment
  # (pregate 3+: the Nullarbor case, SCP 7-10 with SHIP 1.2-1.5) still reads MRGL. Floor TSTM,
  # cap MRGL -- a trace never buys MDT or HIGH.
  if (no_trig & nz(rain_mm) >= 0.1*trig & pregate >= 2 & !strict & allow_conditional){
    c <- max(1, min(2, pregate - 1)); conditional <- TRUE
  }
  c  <- max(c, rc)
  if (strict & nz(rain_mm) < 1.5*trig) c <- min(c, 1)   # tropical coastal zone: MRGL+ needs 1.5x the trigger

  hatch <- as.integer(sig)
  list(cat=c, pregate=pregate, conditional=conditional, cape=round(cape), shear=round(shr_kt), scp=round(scp,1),
       stp=round(stp,1), ship=round(ship,1), cin=round(cin), rain=round(nz(rain_mm), 1), hatch=hatch)
}

om_url <- function(lat, lon){
  lv <- paste0(c(
    paste0("temperature_",LEVELS,"hPa"),
    paste0("relative_humidity_",LEVELS,"hPa"),
    paste0("wind_speed_",LEVELS,"hPa"),
    paste0("wind_direction_",LEVELS,"hPa"),
    paste0("geopotential_height_",LEVELS,"hPa")), collapse=",")
  sfc <- "temperature_2m,dew_point_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m,precipitation,precipitation_probability,freezing_level_height,soil_moisture_0_to_1cm"
  # timezone=UTC (not auto): with per-point local time, "Day 1" boundaries fell at a
  # different UTC instant in WA (UTC+8) vs the east coast (UTC+10/11), so the same day
  # label covered different absolute windows depending where a grid point sat. Forcing
  # UTC makes every point's day_groups() split on the same 00Z-24Z boundary, matching how
  # SPC-style outlooks use one fixed reference frame instead of each location's own midnight.
  # start_date/end_date (not forecast_days) pin that boundary to START_DATE/END_DATE (see
  # above) instead of letting Open-Meteo default to raw UTC "today".
  sprintf(paste0(OM_HOST, "/v1/forecast?latitude=%.3f&longitude=%.3f",
    "&hourly=%s,%s&start_date=%s&end_date=%s&timezone=UTC&wind_speed_unit=kn&cell_selection=nearest", OM_AUTH),
    lat, lon, sfc, lv, START_DATE, END_DATE)
}

fetch_point <- function(lat, lon){
  for (a in 1:4){
    # jsonlite::fromJSON(txt) guesses whether txt is a URL/path or literal JSON by checking
    # nchar(txt) against a ~2083-char threshold (the old IE max-URL-length); our request URLs
    # run ~2100 chars (16 pressure levels x 5 fields + surface vars + the start_date/end_date
    # params), so fromJSON stopped recognizing them as URLs and tried to parse the URL STRING
    # ITSELF as JSON -- an instant, deterministic failure with no network call ever made. Fetching
    # the body ourselves and handing fromJSON the raw JSON text (which always starts with '{')
    # sidesteps that guess entirely.
    r <- tryCatch({
      raw <- paste(readLines(om_url(lat,lon), warn=FALSE), collapse="")
      fromJSON(raw)
    }, error=function(e) NULL)
    if (!is.null(r) && !is.null(r$hourly)) return(r)
    Sys.sleep(1.2*a)
  }
  NULL
}

# Southern-Hemisphere supercell composites (9 Sep 2026). thundeR's SCP_new / STP_new are built on
# RIGHT-mover storm-relative helicity -- the Northern-Hemisphere convention, where the cyclonic
# supercell deviates to the right of the mean wind. In the Southern Hemisphere the mirror-image
# LEFT-mover is the cyclonic, dominant supercell, and the right-mover SRH has the opposite sign,
# so the RM composites read ~0 or negative in exactly the environments they are meant to flag:
# on the 1 Nov 2025 reconstruction SCP was -1.2..+1.5 and STP negative everywhere across SE QLD
# with CAPE 2500-3000 and 45-50kt shear, i.e. the two composites that drive MDT/HIGH contributed
# nothing. Every grid point here is in the Southern Hemisphere, so the _LM variants are used
# outright. abs() makes this robust to either sign convention thundeR may use for the LM
# helicity term (the LM member is the cyclonic one here, so its magnitude IS the supercell
# potential); the RM field is the fallback only if a thundeR build lacks the _LM output.
# safe read of a thundeR parameter by name: 0 if this build lacks it or it is NA
gpar <- function(par, k){ if (k %in% names(par)) nz(par[[k]]) else 0 }
# 700hPa dewpoint depression (degC) for hour i, NA if either field is missing
dd700_hour <- function(h, i){
  t7 <- h[["temperature_700hPa"]][i]; rh7 <- h[["relative_humidity_700hPa"]][i]
  if (is.null(t7) || is.null(rh7) || is.na(t7) || is.na(rh7)) return(NA)
  t7 - dewpoint(t7, rh7)
}
sh_composite <- function(par, base){
  lm <- paste0(base, "_LM")
  if (lm %in% names(par) && !is.na(par[[lm]])) abs(par[[lm]]) else nz(par[[base]])
}

build_profile <- function(h, i, elev){
  pres <- c(h[["surface_pressure"]][i])
  alt  <- c(if (!is.na(elev)) elev else 0)
  tmp  <- c(h[["temperature_2m"]][i])
  dpt  <- c(h[["dew_point_2m"]][i])
  wd   <- c(h[["wind_direction_10m"]][i])
  ws   <- c(h[["wind_speed_10m"]][i])
  sp   <- h[["surface_pressure"]][i]
  for (L in LEVELS){
    if (is.na(sp) || L >= sp) next
    T  <- h[[paste0("temperature_",L,"hPa")]][i]
    RH <- h[[paste0("relative_humidity_",L,"hPa")]][i]
    Z  <- h[[paste0("geopotential_height_",L,"hPa")]][i]
    WS <- h[[paste0("wind_speed_",L,"hPa")]][i]
    WD <- h[[paste0("wind_direction_",L,"hPa")]][i]
    if (any(is.na(c(T,RH,Z,WS,WD)))) next
    pres <- c(pres, L); alt <- c(alt, Z); tmp <- c(tmp, T)
    dpt <- c(dpt, dewpoint(T, RH)); wd <- c(wd, WD); ws <- c(ws, WS)
  }
  if (length(pres) < 5) return(NULL)
  o <- order(alt)
  list(pressure=pres[o], altitude=alt[o], temp=tmp[o], dpt=dpt[o], wd=wd[o], ws=ws[o])
}

# all hourly indices grouped by forecast day
day_groups <- function(times){
  dts <- substr(times, 1, 10)
  days <- unique(dts)[seq_len(min(FDAYS, length(unique(dts))))]
  list(idx = lapply(days, function(d) which(dts == d)), days = days)
}

# a day's value = average of the TOPN highest-severity hours. rain is checked across the
# WHOLE day (not just the top-N severity hours) since those hours are picked by an instability
# score, not a precip score -- the day's actual rain chance/total can peak at an hour that
# score didn't select.
day_topN <- function(h, idxs, elev, lat, strict=FALSE, want_frames=FALSE, lead=0){
  # lead is 0-based (0 = today). Everything lead-dependent is resolved once, here, so the
  # rest of the function reads the same as it did before.
  li      <- lead_of(lead)
  trig_d  <- LEAD_TRIG[li]
  sev_k   <- LEAD_SEV_K[li]
  cat_cap <- MAX_CAT_BY_LEAD[li]
  rows <- list()
  for (i in idxs){
    prof <- tryCatch(build_profile(h, i, elev), error=function(e) NULL)
    if (is.null(prof)) next
    par <- tryCatch(
      sounding_compute(prof$pressure, prof$altitude, prof$temp, prof$dpt, prof$wd, prof$ws, accuracy=1),
      error=function(e) NULL)
    if (is.null(par)) next
    rows[[length(rows)+1]] <- list(
      hr   = i,                       # hourly index, so frames can regroup these by 3-hour bin
      sev  = sev_score(par),
      cape = nz(par[["MU_CAPE"]]), shr = nz(par[["BS_EFF_MU"]]),
      scp  = sh_composite(par, "SCP_new"), stp = sh_composite(par, "STP_new"), ship = nz(par[["SHIP"]]),
      # raw signed left-mover values kept as diagnostics (null if the thundeR build lacks them)
      scp_lm = if ("SCP_new_LM" %in% names(par)) nz(par[["SCP_new_LM"]]) else NA,
      stp_lm = if ("STP_new_LM" %in% names(par)) nz(par[["STP_new_LM"]]) else NA,
      cin  = nz(par[["MU_CIN"]]), frz = h[["freezing_level_height"]][i],
      t500 = h[["temperature_500hPa"]][i],
      tprob = nz(h[["precipitation_probability"]][i]),
      # downdraft / damaging-wind inputs, see wind_tier()
      dcape = gpar(par, "DCAPE"), lr03 = gpar(par, "LR_03km"), dcp = gpar(par, "DCP"),
      dd700 = dd700_hour(h, i))
  }
  rain_day  <- sum(sapply(idxs, function(i) nz(h[["precipitation"]][i])))
  rain_rate <- max(sapply(idxs, function(i) nz(h[["precipitation"]][i])))  # peak single-hour rate, for flood_cat()
  # day's peak-confidence hour (max, not mean) -- flood_cat() gates on the model's HIGHEST
  # same-day confidence that rain occurs at all, paired with the day's peak magnitude (also a max).
  rain_pop  <- max(sapply(idxs, function(i) nz(h[["precipitation_probability"]][i])))
  # fire danger is purely surface-field driven (temp/RH/wind/soil moisture), no sounding needed,
  # so it's computed here once and shared by both branches below rather than only the successful-
  # soundings path. Kept as the day's WORST hour (see ffdi_hour()'s doc comment for why a max, not
  # a mean, across the day matters here).
  rh_hr    <- sapply(idxs, function(i) nz(h[["relative_humidity_2m"]][i]))
  temp_hr  <- sapply(idxs, function(i) nz(h[["temperature_2m"]][i]))
  wind_hr  <- sapply(idxs, function(i) nz(h[["wind_speed_10m"]][i])) * 1.852  # kn (fetch unit) -> km/h
  soil_hr  <- sapply(idxs, function(i) nz(h[["soil_moisture_0_to_1cm"]][i]))
  ffdi_day <- max(mapply(ffdi_hour, temp_hr, rh_hr, wind_hr, soil_hr))
  # 500hPa steering flow for the viewer's faint streamline overlay (added 4 Sep 2026): the day's
  # vector-mean wind as u/v components in knots (the fetch's wind_speed_unit=kn), meteorological
  # convention -- direction is where the wind blows FROM, so u = -ws*sin(dir), v = -ws*cos(dir).
  # Mean over ALL of the day's hours, not the top-N instability hours the severe parameters use:
  # this is context (the general mid-level flow pattern), not a severity input, and a whole-day
  # mean is the steadier picture of it. NA when the level is missing for the whole day (a fetch
  # gap) -- the viewer simply skips points without it. Shared by both return branches below.
  ws5 <- sapply(idxs, function(i) h[["wind_speed_500hPa"]][i])
  wd5 <- sapply(idxs, function(i) h[["wind_direction_500hPa"]][i])
  okw <- !is.na(ws5) & !is.na(wd5)
  if (any(okw)){
    u500 <- round(mean(-ws5[okw] * sin(wd5[okw] * pi/180)), 1)
    v500 <- round(mean(-ws5[okw] * cos(wd5[okw] * pi/180)), 1)
  } else { u500 <- NA; v500 <- NA }

  # 3-hourly frames for this day, built from the per-hour rows already computed above. Each frame
  # sums its window's precipitation, takes the peak probability, and means the instability over
  # whichever of its hours produced a sounding. A frame with no sounding at all falls back to the
  # rain category alone, the same way the whole-day branch below does.
  fr <- NULL
  if (want_frames){
    hrs  <- as.integer(substr(h[["time"]][idxs], 12, 13))
    bins <- hrs %/% FRAME_HOURS
    fr <- lapply(sort(unique(bins)), function(b){
      ii <- idxs[bins == b]
      rr <- Filter(function(r) (as.integer(substr(h[["time"]][r$hr], 12, 13)) %/% FRAME_HOURS) == b, rows)
      rain_f <- sum(sapply(ii, function(i) nz(h[["precipitation"]][i])))
      rate_f <- max(sapply(ii, function(i) nz(h[["precipitation"]][i])))
      pop_f  <- max(sapply(ii, function(i) nz(h[["precipitation_probability"]][i])))
      flood_f <- flood_cat(rain_f, rate_f, pop_f, lat)
      if (length(rr) == 0){
        rcf <- rain_cat(rain_f)
        return(list(t=substr(h[["time"]][ii[1]], 1, 16),
                    v=unname(c(rcf, tprob_floor(thunder_prob(pop_f, 0, rain_f), rcf), 0, 0, flood_f,
                               0, 0, 0, round(rain_f, 1)))))
      }
      mf   <- function(k) mean(sapply(rr, function(r) r[[k]]))
      mfna <- function(k){ v <- sapply(rr, function(r) r[[k]]); if (all(is.na(v))) NA else mean(v, na.rm=TRUE) }
      frzf <- suppressWarnings(min(sapply(rr, function(r) r$frz),  na.rm=TRUE)); if (!is.finite(frzf)) frzf <- NA
      t5f  <- suppressWarnings(min(sapply(rr, function(r) r$t500), na.rm=TRUE)); if (!is.finite(t5f))  t5f  <- NA
      pk   <- rr[[which.max(sapply(rr, function(r) r$ship))]]
      # the frame trigger tracks the day's trigger by the same ratio it always had (FRAME_TRIG
      # was 0.5 against a 2mm day), so frames stay consistent with the daily panel at every lead.
      cvf  <- categorise_vals(mf("cape"), mf("shr"), mf("scp"), mf("stp"), mf("ship"), mf("cin"),
                              rain_f, strict, trig=FRAME_TRIG * trig_d / 2, allow_conditional=FALSE,
                              t500=t5f, sev_k=sev_k)
      hf <- if (cvf$cat >= 1) hail_tier(pk$ship, pk$cape, frzf, t5f) else 0L
      wf <- wind_tier(mf("dcape"), mfna("dd700"), mf("lr03"), mf("dcp"), mf("cape"), mf("shr"), cvf$cat)
      if (cvf$cat == 1 && (hf >= 2 || wf >= 1)) cvf$cat <- 2L    # same hazard upgrade as the daily product
      if (cvf$cat < 2) { wf <- 0L; hf <- min(hf, 1L) }
      cvf$cat <- min(cvf$cat, cat_cap)
      list(t=substr(h[["time"]][ii[1]], 1, 16),
           v=unname(c(cvf$cat, tprob_floor(thunder_prob(mf("tprob"), mf("cape"), rain_f), cvf$cat),
                      hf, wf, flood_f, round(mf("cape")), round(mf("shr")*1.94384),
                      round(mf("ship"), 1), round(rain_f, 1))))
    })
  }

  if (length(rows) == 0){
    rc <- min(rain_cat(rain_day), cat_cap)
    # no successful soundings this day -- no instability-based hour selection to lean on, so fall
    # back to the day's mean precip-probability (still whole-day, but mean rather than max keeps a
    # single spurious overnight-drizzle hour from dominating the fallback the way max did before).
    tprob_fallback <- mean(sapply(idxs, function(i) nz(h[["precipitation_probability"]][i])))
    return(list(cat=rc, cape=0, shear=0, scp=0, stp=0, ship=0, cin=0, rain=round(rain_day), hatch=as.integer(rc>=3),
                tprob=tprob_floor(thunder_prob(tprob_fallback, 0, rain_day), rc), hail=0,
                flood=flood_cat(rain_day, rain_rate, rain_pop, lat), pop=round(rain_pop),
                fire=fire_tier(ffdi_day, rain_day), ffdi=round(ffdi_day), wind=0L,
                u500=u500, v500=v500, fr=fr))
  }
  sev <- sapply(rows, function(r) r$sev)
  top <- rows[order(sev, decreasing=TRUE)[seq_len(min(TOPN, length(rows)))]]
  m <- function(k) mean(sapply(top, function(r) r[[k]]))
  mna <- function(k){ v <- sapply(top, function(r) r[[k]]); if (all(is.na(v))) NA else mean(v, na.rm=TRUE) }
  # coldest freezing level AND coldest 500hPa temp among the day's most unstable hours -- see
  # hail_tier() for why both cold-aloft signals are taken as the day's min rather than paired to
  # one specific hour the way cape/ship below are.
  frz_day <- suppressWarnings(min(sapply(top, function(r) r$frz), na.rm=TRUE))
  if (!is.finite(frz_day)) frz_day <- NA
  t500_day <- suppressWarnings(min(sapply(top, function(r) r$t500), na.rm=TRUE))
  if (!is.finite(t500_day)) t500_day <- NA
  # hail comes from a brief, sharp peak (a supercell's giant-hail window is often 1-2 hours),
  # not a sustained multi-hour condition the way overall category risk is -- so unlike cape/shr/
  # scp/stp/ship/cin above (deliberately averaged across the top-N hours to represent the day's
  # sustained risk), hail_tier() is fed the SINGLE peak-SHIP hour among the top-N, using that
  # same hour's cape (not an independently-maxed cape from a different hour) so the two stay
  # physically paired. Backtested against 4 known Australian hail days (Canberra Jan 2022, SE
  # QLD Dec 2023, Casterton VIC Oct 2024, Boggabri NSW Dec 2024): averaging suppressed the one
  # case (SE QLD) where the peak hour's SHIP was itself borderline-favorable (1.09) down below
  # the tier-2 threshold; the other three cases showed a low SHIP even at their single best hour,
  # which this change does not fix -- that shortfall looks like GFS's synoptic-scale resolution
  # not capturing these often highly localized supercell environments, a harder problem than a
  # threshold or averaging tweak.
  peak_ship_hr <- top[[which.max(sapply(top, function(r) r$ship))]]
  cv <- categorise_vals(m("cape"), m("shr"), m("scp"), m("stp"), m("ship"), m("cin"), rain_day, strict,
                        trig=trig_d, t500=t500_day, sev_k=sev_k)
  # thunderstorm chance: Open-Meteo's own ensemble-based precipitation_probability (%), averaged
  # over the SAME top-N instability-ranked hours as cape/shear/ship, not the whole day -- a whole-day
  # max picks up unrelated overnight drizzle (Open-Meteo's ensemble can be very confident about light,
  # non-convective rain at 7am) and reports it as a dramatic "thunderstorm chance" for the day.
  # Large hail or damaging winds inside a general storm chance IS a marginal severe risk
  # (Josh, 13 Sep 2026), so compute both first and let them lift the category.
  hail_d <- if (cv$cat >= 1) hail_tier(peak_ship_hr$ship, peak_ship_hr$cape, frz_day, t500_day) else 0L
  wind_d <- wind_tier(m("dcape"), mna("dd700"), m("lr03"), m("dcp"), m("cape"), m("shr"), cv$cat)
  # Conditional days are excluded. Their flag means we are not confident storms form at all, and
  # hail/wind potential is conditional on a storm existing -- letting it lift them would undo the
  # 12 Sep conditional downgrade outright (18 of the 26 upgrades on that run's data were exactly
  # the trace-rain WA interior points that downgrade had just demoted).
  if (cv$cat == 1 && !isTRUE(cv$conditional) && (hail_d >= 2 || wind_d >= 1)) {
    cv$cat <- 2L
    cv$upgraded <- TRUE     # reached MRGL on the hazard itself, not on the CAPE/shear floor
  }
  # A day left below MRGL must not advertise a severe hazard its own category denies. In practice
  # this only bites conditional days: a non-conditional day with Large hail or a damaging gust was
  # just upgraded above, so it keeps both. A conditional one stayed at TSTM precisely because we
  # are not confident storms form, and on the first run of this change 8 such points were painting
  # a Damaging tier on the wind pane under a TSTM category -- the same cross-pane contradiction
  # the thunder-chance floor fixed in September.
  # Severity ceiling for this lead, applied AFTER the hazard upgrade so a day held down by the
  # ceiling cannot still publish a hazard tier its category denies (the same cross-pane
  # contradiction the conditional-day guard above fixes).
  if (cv$cat > cat_cap){ cv$cat <- cat_cap; cv$lead_capped <- TRUE }
  if (cv$cat < 2) { wind_d <- 0L; hail_d <- min(hail_d, 1L) }
  c(cv, list(tprob=tprob_floor(thunder_prob(m("tprob"), m("cape"), rain_day), cv$cat),
             # hail gated on the category (7 Sep 2026), the same way wind_tier() already is: no
             # storms means no hail. Before this, a marginal peak-hour SHIP (0.5-0.6) in a hot,
             # deeply capped (CIN -178), completely dry (0mm, 0% thunder chance) Kimberley airmass
             # drew a Small-hail zone on a day the category logic had correctly gated to 0.
             hail=hail_d,
             flood=flood_cat(rain_day, rain_rate, rain_pop, lat), pop=round(rain_pop),
             fire=fire_tier(ffdi_day, rain_day), ffdi=round(ffdi_day),
             wind=wind_d,
             dcape=round(m("dcape")),   # shown nowhere yet; kept so wind tiers can be checked against their driver
             scp_lm=round(m("scp_lm"),1), stp_lm=round(m("stp_lm"),1),   # diagnostic, see day_topN rows
             u500=u500, v500=v500, fr=fr))
}

# each point is a fully independent fetch+compute (no shared state), so this is embarrassingly
# parallel -- GitHub's ubuntu-latest runners give 4 vCPUs, and the old sequential loop spent most
# of its ~3.5h wall-clock either waiting on Open-Meteo's response or inside thundeR's per-hour
# sounding_compute() (up to 192 hours/point), both of which parallelize cleanly across points.
# mc.preschedule=FALSE hands points to workers one at a time as they free up rather than splitting
# the grid into 4 fixed static chunks up front, so one slow/retrying point doesn't leave a worker
# idle while the others finish their chunk.
suppressMessages(library(parallel))
# 10 workers, not the core count (14 Sep 2026). This loop is not CPU-bound: measured across two
# keyed runs, each point costs ~5.35s of which roughly half is a worker sitting blocked on a
# 195-variable, ~194KB HTTP response. Capping at the runner's 4 vCPUs therefore left the machine
# idle about half the time. Oversubscribing overlaps one worker's network wait with another's
# sounding maths. Only safe now that the key removed rate limiting -- on the free endpoint extra
# concurrency just bought extra 429s. If Open-Meteo ever starts refusing concurrent connections it
# will show up as a jump in first-pass failures; dial this back here.
NCORES <- 20
cat(sprintf("Processing %d grid points (%d days, avg of top %d hours) across %d workers...\n", nrow(GRID), FDAYS, TOPN, NCORES))

process_point <- function(k){
  tryCatch({
    lat <- GRID[k,1]; lon <- GRID[k,2]
    r <- fetch_point(lat, lon)
    if (is.null(r)) return(NULL)
    h <- r$hourly; elev <- r$elevation
    gp <- day_groups(h$time)
    trop <- tropical_coastal(lat, lon)
    dres <- lapply(seq_along(gp$idx), function(j)
                     day_topN(h, gp$idx[[j]], elev, lat, trop, want_frames = (ENABLE_FRAMES && j <= FRAME_DAYS), lead = j - 1L))
    if (!nzchar(OM_KEY)) Sys.sleep(0.15)   # courtesy pacing for the free endpoint; needless on a paid key
    list(lat=lat, lon=lon, d=dres, days=gp$days, tropical=trop)
  }, error=function(e) NULL)
}

# ECMWF second-opinion rain check, added 5 Sep 2026: GFS (0.25 deg) can under-forecast a real but
# localized rain trigger, silently zeroing an otherwise-favorable environment via categorise_vals()'s
# 2mm no_trig gate -- confirmed live, a point/day with CAPE 475 J/kg, shear 49kt, SCP 2.4 showed
# "No storms" purely because GFS's own rain forecast for it was 0mm, while a neighbouring, LESS
# favorable point (CAPE 380, shear 47kt, SCP 1.6) showed a TSTM signal because its rain cleared
# the gate. A full switch to ECMWF was considered and rejected: checked live against Open-Meteo's
# API, ecmwf_ifs025 has no soil_moisture_0_to_1cm at all (fire danger's only input) and no
# freezing_level_height (hail's primary cold-aloft signal, though the temperature_500hPa secondary
# signal added earlier does still work on it), and its pressure-level set doesn't match this
# pipeline's LEVELS (975hPa returned null in a live test) -- wholesale migration would silently
# break fire danger and degrade hail. Instead: only call ECMWF, only for point-days GFS's own
# thermodynamics already flagged as favorable (pregate>=1) but rain-gated (cat==0), purely to
# decide whether to un-gate that one day. ECMWF is never used for fire, hail, or anything else.
#
# Batched by calendar day rather than one request per candidate point-day: Open-Meteo's existing
# endpoint accepts comma-separated multi-location requests and returns one JSON object per point
# (confirmed live) -- at most a handful of extra requests per run, not one per candidate. Run as a
# sequential pass AFTER the main parallel mclapply + retry pass below, not inline inside the
# parallel workers, so no cross-worker shared state is needed to bound the added runtime.
#
# The 2mm window here matches GFS's own rain_day window exactly: om_url() already forces
# timezone=UTC so day_groups() splits every point on the same 00Z-24Z boundary (see the comment
# there), and this queries ECMWF with start_date=end_date=<the same date string>&timezone=UTC --
# same calendar day, same reference frame, no misalignment between the two models' rain figures.
#
# Failure mode is deliberately one-directional: any failure here (bad response, timeout, a chunk
# mismatch) just leaves a day exactly as gated as it already is -- this can only ever ADD signal,
# never remove it, and can never cause a point to fail or affect the completeness gate below,
# since it only mutates already-successful results after the retry pass has run.
# Raised 500->1000 on 3 Sep 2026: the first live run found 935 real candidates (cape>=150 is a
# low bar, so "primed but GFS-gated" turned out far more common than expected) and the 500 cap
# truncated ~47% of them unchecked -- not a bug being caught, just legitimate volume the original
# guess undershot. 1000 covers that run with headroom while still being a real guardrail against
# a genuinely pathological candidate count (e.g. a future change to the base thermodynamics
# thresholds firing far more broadly than intended), not a rubber-stamp raised to whatever showed
# up once.
MAX_ECMWF_CANDIDATES <- 3000   # 1000 -> 3000 on 9 Sep 2026: the 1 Nov 2025 reconstruction had 2637 candidates and lost 1637 of them to the cap
ECMWF_BATCH_SIZE <- 100        # conservative per-request chunk size

ecmwf_rain_batch <- function(lats, lons, date_str){
  n <- length(lats)
  url <- sprintf(
    paste0(OM_HOST, "/v1/forecast?latitude=%s&longitude=%s&hourly=precipitation&models=ecmwf_ifs025&start_date=%s&end_date=%s&timezone=UTC", OM_AUTH),
    paste(sprintf("%.3f", lats), collapse=","),
    paste(sprintf("%.3f", lons), collapse=","),
    date_str, date_str)
  r <- tryCatch({
    raw <- paste(readLines(url, warn=FALSE), collapse="")
    fromJSON(raw, simplifyVector=FALSE)
  }, error=function(e) NULL)
  if (is.null(r)) return(rep(NA_real_, n))
  # a single-location request returns a bare JSON object, not a 1-element array -- confirmed live;
  # missing this re-wrap would silently break every date-group with exactly one candidate, which
  # given the narrow trigger condition is a likely occurrence, not a rare edge case.
  if (n == 1) r <- list(r)
  if (length(r) != n) return(rep(NA_real_, n))   # defensive: response length must match request
  vapply(r, function(loc){
    p <- tryCatch(loc$hourly$precipitation, error=function(e) NULL)
    if (is.null(p)) return(NA_real_)
    sum(vapply(p, function(x) if (is.null(x)) 0 else as.numeric(x), numeric(1)))
  }, numeric(1))
}

apply_ecmwf_second_opinion <- function(raw_results){
  cand <- list()
  for (k in seq_along(raw_results)){
    res <- raw_results[[k]]
    if (is.null(res) || inherits(res, "try-error")) next
    for (j in seq_along(res$d)){
      dd <- res$d[[j]]
      if (!is.null(dd$cat) && dd$cat == 0 && !is.null(dd$pregate) && dd$pregate >= 1){
        cand[[length(cand)+1]] <- list(k=k, j=j, lat=res$lat, lon=res$lon, date=res$days[j])
      }
    }
  }
  if (length(cand) == 0) return(raw_results)
  if (length(cand) > MAX_ECMWF_CANDIDATES){
    cat(sprintf("ECMWF second-opinion: %d candidates exceeds the %d cap, truncating.\n", length(cand), MAX_ECMWF_CANDIDATES))
    cand <- cand[seq_len(MAX_ECMWF_CANDIDATES)]
  }
  cat(sprintf("ECMWF second-opinion: %d candidates flagged (pregate>=1, GFS rain below the lead trigger)\n", length(cand)))

  by_date <- split(cand, sapply(cand, function(c) c$date))
  n_checked <- 0; n_ungated <- 0
  for (date_str in names(by_date)){
    group <- by_date[[date_str]]
    for (start in seq(1, length(group), by=ECMWF_BATCH_SIZE)){
      chunk <- group[start:min(start+ECMWF_BATCH_SIZE-1, length(group))]
      lats <- sapply(chunk, function(c) c$lat); lons <- sapply(chunk, function(c) c$lon)
      ecmwf_rain <- ecmwf_rain_batch(lats, lons, date_str)
      for (i in seq_along(chunk)){
        c_ <- chunk[[i]]
        if (is.na(ecmwf_rain[i])) next
        n_checked <- n_checked + 1
        dd <- raw_results[[c_$k]]$d[[c_$j]]
        dd$rain_ecmwf <- round(ecmwf_rain[i], 1)
        strict <- isTRUE(raw_results[[c_$k]]$tropical)
        # This pass un-gates days the rain trigger zeroed, so its own bars must ride the same
        # lead ladder -- left at a flat 2mm it would hand straight back the long-lead area the
        # ladder was added to remove. The tropical (1.5x) and trace (0.1x) ratios are unchanged.
        tg   <- LEAD_TRIG[lead_of(c_$j - 1L)]
        ecap <- MAX_CAT_BY_LEAD[lead_of(c_$j - 1L)]
        if (ecmwf_rain[i] >= (if (strict) 1.5 * tg else tg)){
          dd$cat <- dd$pregate
          dd$conditional <- FALSE
          dd$tprob <- tprob_floor(dd$tprob, dd$cat)   # keep the thunder pane consistent with the restored category
          dd$ecmwf_ungated <- TRUE
          n_ungated <- n_ungated + 1
        } else if (strict & ecmwf_rain[i] >= tg & nz(dd$cat) == 0){
          # tropical coastal zone with 2-3mm in ECMWF: general storm chance only, no severe tier
          dd$cat <- 1L
          dd$tprob <- tprob_floor(dd$tprob, dd$cat)
          dd$ecmwf_ungated <- TRUE
          n_ungated <- n_ungated + 1
        } else if (!strict & ecmwf_rain[i] >= 0.1 * tg & nz(dd$pregate) >= 2 & nz(dd$cat) == 0){
          # ECMWF trace only: conditional, one category below the environment -- see categorise_vals()
          dd$cat <- max(1L, min(2L, as.integer(nz(dd$pregate)) - 1L))
          dd$conditional <- TRUE
          dd$tprob <- tprob_floor(dd$tprob, dd$cat)
          dd$ecmwf_ungated <- TRUE
          n_ungated <- n_ungated + 1
        }
        dd$cat <- min(nz(dd$cat), ecap)      # the lead ceiling outranks any un-gating
        raw_results[[c_$k]]$d[[c_$j]] <- dd
      }
    }
  }
  cat(sprintf("ECMWF second-opinion: %d checked successfully, %d un-gated\n", n_checked, n_ungated))
  raw_results
}

raw_results <- mclapply(seq_len(nrow(GRID)), process_point, mc.cores=NCORES, mc.preschedule=FALSE)

# retry pass, added 27 Aug 2026: a point failing here doesn't mean it's unrecoverable -- Open-Meteo
# degrading mid-run tends to cause a burst of transient failures (rate-limiting, timeouts) that
# often succeed on a fresh attempt once the immediate pressure has passed, rather than every failed
# point being permanently unreachable. Before this, the only way to recover a below-threshold run
# was to manually re-trigger the ENTIRE workflow from scratch -- another full 25min-2h run against
# a still-degraded API, repeated three times in a row on 26-27 Aug 2026 with no guarantee of
# improvement each time. Retrying only the failed subset here is far cheaper per attempt and can
# turn a run that would have failed the completeness floor into one that clears it, without a
# human needing to notice the failure and manually retry. Exactly one retry round -- not a loop --
# so a genuinely bad Open-Meteo day still fails fast and predictably rather than this step alone
# silently retrying forever and blowing out the job's wall-clock budget.
failed_idx <- which(sapply(raw_results, function(r) is.null(r) || inherits(r, "try-error")))
if (length(failed_idx) > 0) cat(sprintf("First pass: %d/%d failed. Retrying failed points...\n", length(failed_idx), nrow(GRID)))
# Up to RETRY_ROUNDS rounds (was exactly one), each after a RETRY_PAUSE_S pause -- 11 Sep 2026.
# The failures this is rescuing are Open-Meteo HTTP 429 rate limits, which are per-minute/hour
# quotas: retrying the moment the first pass ends just re-hits the same exhausted window, which
# is why 10 Sep's scheduled run recovered only 154 of 296 and missed the 85% floor at 80%.
# Pausing first lets the window reset, and a second round catches what the first still lost.
# Still bounded, not a loop: at most 2 rounds x (pause + retry), ~6-8 extra minutes worst case,
# so a genuinely dead Open-Meteo day still fails fast rather than running for hours.
# PAUSE SHORTENED ON A KEYED RUN, 24 Sep 2026. The 120s above is a rate-limit window, and the
# paid endpoint has no per-minute limit -- the same reason the courtesy Sys.sleep() in the main
# loop is already skipped when keyed. It was costing a flat two minutes on most runs to retry
# one to three points out of 1342 (23 Sep: 3 points, 2m03s of pause). A short backoff is still
# worth keeping for transient network errors, which do settle; the two-minute quota wait is not.
RETRY_ROUNDS <- 2; RETRY_PAUSE_S <- if (nzchar(OM_KEY)) 10 else 120
for (round in seq_len(RETRY_ROUNDS)) {
  if (length(failed_idx) == 0) break
  cat(sprintf("Retry round %d: pausing %ds for the rate-limit window, then retrying %d points...\n", round, RETRY_PAUSE_S, length(failed_idx)))
  Sys.sleep(RETRY_PAUSE_S)
  retry_results <- mclapply(failed_idx, process_point, mc.cores=NCORES, mc.preschedule=FALSE)
  recovered <- 0
  for (j in seq_along(failed_idx)) {
    res <- retry_results[[j]]
    if (!is.null(res) && !inherits(res, "try-error")) recovered <- recovered + 1
    raw_results[[failed_idx[j]]] <- res
  }
  cat(sprintf("Retry round %d recovered %d/%d previously-failed points.\n", round, recovered, length(failed_idx)))
  failed_idx <- which(sapply(raw_results, function(r) is.null(r) || inherits(r, "try-error")))
}

# Neighbourhood trigger (11 Sep 2026), run after the ECMWF pass so rain_ecmwf is populated. A
# 0.25 deg model routinely misplaces convective initiation by a grid cell or two, so a point with
# a MRGL+ environment and no rain of its own, sitting next to a point where either model DOES put
# at least a trace (>=0.2mm), is a plausible-but-uncertain storm location -- shown as conditional MRGL (capped), the
# same treatment as a trace. "Adjacent" = within 1.0 deg in both lat and lon on the 0.82 deg
# lattice, i.e. the 8 surrounding points. Purely additive: only ever lifts a gated 0 to 2.
apply_neighbour_trigger <- function(raw_results){
  ok <- which(sapply(raw_results, function(r) !is.null(r) && !inherits(r, "try-error")))
  if (length(ok) == 0) return(raw_results)
  lat <- sapply(ok, function(k) raw_results[[k]]$lat); lon <- sapply(ok, function(k) raw_results[[k]]$lon)
  n_lifted <- 0
  for (a in seq_along(ok)){
    k <- ok[a]; res <- raw_results[[k]]
    if (isTRUE(res$tropical)) next   # tropical coastal zone: no conditional un-gating at all
    nb <- ok[abs(lat - lat[a]) <= NB_RADIUS & abs(lon - lon[a]) <= NB_RADIUS & ok != k]
    if (length(nb) == 0) next
    for (j in seq_along(res$d)){
      dd <- res$d[[j]]
      # pregate >= 3 (was 2): borrowing a neighbour's rain is the weakest evidence there is, so it
      # is reserved for environments that are genuinely severe on their own. With the old bar every
      # ordinary MRGL point next to a drizzle trace was lifted, which is what spread the WA blanket.
      if (nz(dd$cat) != 0 || nz(dd$pregate) < 3) next
      wet <- any(sapply(nb, function(m){
        nd <- raw_results[[m]]$d; if (length(nd) < j) return(FALSE)
        # a TRACE (>=0.2mm) at the neighbour is enough (Josh, 11 Sep 2026; was >=2mm): the point
        # itself already needs MRGL+ thermodynamics, and the result is capped at conditional MRGL
        x <- nd[[j]]; !is.null(x) && (nz(x$rain) >= 0.2 || nz(x$rain_ecmwf) >= 0.2)
      }))
      if (wet){
        dd$cat <- min(2L, MAX_CAT_BY_LEAD[lead_of(j - 1L)])   # ceiling outranks the lift
        dd$conditional <- TRUE; dd$neighbour_trigger <- TRUE
        dd$tprob <- tprob_floor(dd$tprob, dd$cat)
        raw_results[[k]]$d[[j]] <- dd
        n_lifted <- n_lifted + 1
      }
    }
  }
  cat(sprintf("Neighbourhood trigger: %d gated point-days lifted to conditional MRGL\n", n_lifted))
  raw_results
}

# Diagnostic added 3 Sep 2026: a small, not-yet-explained gap has shown up twice between
# (points valid after retry) and the final "points OK" count -- 91 points on one run, 18 on
# another, both correlated with how much Open-Meteo rate-limiting (HTTP 429s) hit that run.
# apply_ecmwf_second_opinion() only mutates d[[j]] sub-fields on already-valid points and never
# touches the outer per-point entry, so by inspection it shouldn't be able to cause this -- this
# log line checks that directly by comparing valid counts immediately either side of the call,
# rather than continuing to infer it from timing.
valid_before_ecmwf <- sum(sapply(raw_results, function(r) !is.null(r) && !inherits(r, "try-error")))

raw_results <- apply_ecmwf_second_opinion(raw_results)
raw_results <- apply_neighbour_trigger(raw_results)

valid_after_ecmwf <- sum(sapply(raw_results, function(r) !is.null(r) && !inherits(r, "try-error")))
if (valid_after_ecmwf != valid_before_ecmwf) {
  cat(sprintf("WARNING: valid point count changed across apply_ecmwf_second_opinion(): %d -> %d\n",
              valid_before_ecmwf, valid_after_ecmwf))
} else {
  cat(sprintf("Valid points unchanged across ECMWF second-opinion pass: %d\n", valid_before_ecmwf))
}

points <- vector("list", nrow(GRID)); day_labels <- NULL; ok <- 0
frames <- vector("list", nrow(GRID)); frame_ll <- vector("list", nrow(GRID))
for (k in seq_along(raw_results)){
  res <- raw_results[[k]]
  if (is.null(res) || inherits(res, "try-error")) next
  if (is.null(day_labels)) day_labels <- format(as.Date(res$days), "%a %e %b")
  # fr rides along on each day record so it survives the parallel workers; split it out here so
  # outlook.json keeps exactly the schema it had and the frames go to their own files.
  frames[[k]] <- lapply(res$d[seq_len(min(FRAME_DAYS, length(res$d)))], function(dd) dd$fr)
  frame_ll[[k]] <- c(res$lat, res$lon)
  points[[k]] <- list(lat=res$lat, lon=res$lon, tropical=isTRUE(res$tropical),
                      d=lapply(res$d, function(dd){ dd$fr <- NULL; dd }))
  ok <- ok + 1
}

points <- Filter(Negate(is.null), points)
# full_hazards: every hazard in this file was computed by the CURRENT pipeline, so the viewer may
# show all planes for it even when it's loaded as an archive (older archives predate some hazards
# or used since-changed thresholds, and the viewer restricts those to Category Outlook).
out <- list(run_date = if (is.null(HIST_DATE)) format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz="UTC")
                       else paste0(as.Date(START_DATE) - 1, "T18:00:00Z"),
            days = if (is.null(day_labels)) paste("Day", seq_len(FDAYS)) else day_labels,
            full_hazards = TRUE,
            historical = !is.null(HIST_DATE),
            coverage = round(ok / nrow(GRID), 3),   # fraction of grid points with data (viewer shows it when partial)
            points = points)
OUT_PATH <- if (is.null(HIST_DATE)) OUT else file.path(ARCHIVE_DIR, paste0(START_DATE, ".json"))
if (!dir.exists(dirname(OUT_PATH))) dir.create(dirname(OUT_PATH), recursive=TRUE)
write_json(out, OUT_PATH, auto_unbox=TRUE, digits=2)
cat(sprintf("Wrote %s  (%d points OK)\n", OUT_PATH, ok))
# a bare "not literally zero" check let a genuinely broken run (556/1032, 54%, 22 Aug 2026 --
# Open-Meteo degrading partway through and every retry after that point failing) through as
# "success": the workflow committed and published a map with an entire missing hemisphere of
# real data, silently extrapolated over by the viewer's IDW field into a shape that looked like
# a real forecast signal but wasn't. MIN_OK_FRAC refuses to publish anything that incomplete --
# the workflow step then exits non-zero, "Commit result" never runs, and the previous (complete)
# outlook.json stays live rather than being overwritten by a half-empty one.
# Briefly lowered to 0.70 on 27 Aug 2026 after two consecutive same-day runs (71%, then 57%)
# both got blocked during a genuine multi-hour Open-Meteo degradation. Restored to 0.85 the same
# day once the retry pass above (added alongside) proved it can rescue a run on its own -- the
# very next run after the retry pass shipped went from failing outright to 100% complete, so the
# lowered floor's extra risk (missing points cluster geographically, not randomly, so a
# 70%-complete run can still mean one whole region gets silently interpolated over by the
# viewer's IDW field) is no longer a trade worth taking now that there's a cheaper fix for the
# actual problem it was compensating for.
MIN_OK_FRAC <- 0.85
if (ok < MIN_OK_FRAC * nrow(GRID)) {
  if (!is.null(HIST_DATE)) {
    # a historical reconstruction is a one-off demo, not the live product: a partial map is
    # worth far more than no map, and the viewer states the coverage on its banner. The live
    # run keeps the hard floor below.
    cat(sprintf("Only %d/%d points OK (%.0f%%) -- below the %.0f%% floor, but this is a historical reconstruction: publishing with partial coverage.\n",
                ok, nrow(GRID), 100*ok/nrow(GRID), 100*MIN_OK_FRAC))
  } else {
    cat(sprintf("Only %d/%d points OK (%.0f%%) -- below the %.0f%% completeness floor, not publishing this run.\n",
                ok, nrow(GRID), 100*ok/nrow(GRID), 100*MIN_OK_FRAC))
    quit(status=1)
  }
}

# 3-hourly frame files, one per day, written only for live runs (a historical reconstruction is a
# one-off demo and does not need them). Each point's 8 frames are compact numeric arrays in
# FRAME_COLS order rather than named objects, which keeps the four files a few hundred KB each
# instead of a few MB.
if (ENABLE_FRAMES && is.null(HIST_DATE)) {
  if (!dir.exists(FRAME_DIR)) dir.create(FRAME_DIR, recursive=TRUE)
  all_times <- character(0)
  for (j in seq_len(FRAME_DAYS)) {
    fpts <- list(); times_j <- NULL
    for (k in seq_along(frames)) {
      fk <- frames[[k]]
      if (is.null(fk) || length(fk) < j || is.null(fk[[j]])) next
      day_fr <- fk[[j]]
      if (is.null(times_j)) times_j <- sapply(day_fr, function(f) f$t)
      fpts[[length(fpts)+1]] <- list(lat=frame_ll[[k]][1], lon=frame_ll[[k]][2],
                                     f=lapply(day_fr, function(f) f$v))
    }
    if (length(fpts) == 0) next
    all_times <- c(all_times, times_j)
    write_json(list(day=j, times=times_j, cols=FRAME_COLS, points=fpts),
               file.path(FRAME_DIR, sprintf("d%d.json", j)), auto_unbox=TRUE, digits=2)
    cat(sprintf("Wrote %s/d%d.json  (%d points x %d frames)\n", FRAME_DIR, j, length(fpts), length(times_j)))
  }
  write_json(list(run_date=out$run_date, days=out$days[seq_len(FRAME_DAYS)],
                  frame_hours=FRAME_HOURS, times=all_times, cols=FRAME_COLS),
             file.path(FRAME_DIR, "index.json"), auto_unbox=TRUE)
}

# archive this run for the viewer's historical-run picker, dated by START_DATE (the run's own
# Day-1 anchor) so a given archive file always matches what that date's Day 1 actually looked
# like when it was generated -- same convention the "Update outlook YYYY-MM-DD" commit message
# already uses. Kept to a rolling ARCHIVE_DAYS window (pruned every run) so the repo doesn't
# grow unbounded; index.json lists what's currently available so the viewer doesn't have to
# guess dates and eat 404s.
ARCHIVE_DAYS <- 14
if (!dir.exists(ARCHIVE_DIR)) dir.create(ARCHIVE_DIR, recursive=TRUE)
if (is.null(HIST_DATE)) file.copy(OUT, file.path(ARCHIVE_DIR, paste0(START_DATE, ".json")), overwrite=TRUE)
# pinned.json: dates exempt from the rolling prune (historical reconstructions live here; the
# 14-day window is measured from the LIVE run's date, which would otherwise drop them next run)
PINNED_FILE <- file.path(ARCHIVE_DIR, "pinned.json")
pinned <- if (file.exists(PINNED_FILE)) as.character(unlist(fromJSON(PINNED_FILE))) else character(0)
if (!is.null(HIST_DATE)){ pinned <- sort(unique(c(pinned, START_DATE))); write_json(pinned, PINNED_FILE) }
existing <- list.files(ARCHIVE_DIR, pattern="^\\d{4}-\\d{2}-\\d{2}\\.json$")
existing_dates <- sub("\\.json$", "", existing)
# prune relative to the live calendar, not START_DATE -- a historical run must not shift the window
cutoff <- as.Date(format(Sys.time() + 8*3600, "%Y-%m-%d", tz="UTC")) - ARCHIVE_DAYS
keep <- existing_dates[!is.na(as.Date(existing_dates)) & (as.Date(existing_dates) >= cutoff | existing_dates %in% pinned)]
stale <- setdiff(existing, paste0(keep, ".json"))
if (length(stale) > 0) file.remove(file.path(ARCHIVE_DIR, stale))
write_json(sort(keep), file.path(ARCHIVE_DIR, "index.json"))  # NOT auto_unbox: must stay an
# array even when only one date exists yet, since the viewer always expects to parse a list
cat(sprintf("Archived run for %s (%d dates kept, %d pruned)\n", START_DATE, length(keep), length(stale)))

# --- observational verification ---------------------------------------------------------
# Scores the run archive against NOAA's CPC rain-gauge analysis (see pipeline/verify.py for why
# that source and what it can and cannot check). Invoked from here rather than as its own
# workflow step purely so it needs no change to .github/workflows -- the commit step already
# picks up everything under docs/archive, which is where both the observation cache and
# skill.json land.
#
# Wrapped so it can never fail the build: verification is a diagnostic, and a bad day at NOAA's
# OPeNDAP server must not cost us an outlook. Skipped on historical reconstructions, which would
# otherwise re-score the whole archive against a date they have nothing to say about.
if (is.null(HIST_DATE)) {
  vres <- tryCatch(system2("python3", c("pipeline/verify.py"), stdout=TRUE, stderr=TRUE),
                   error=function(e) paste("verification could not start:", conditionMessage(e)))
  cat(paste(vres, collapse="\n"), "\n")
  st <- attr(vres, "status")
  if (!is.null(st) && st != 0) cat(sprintf("Verification exited %s -- outlook is unaffected.\n", st))
}
