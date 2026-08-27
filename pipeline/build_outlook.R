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
LEVELS <- c(1000,975,950,925,900,850,800,700,600,500,400,300,250,200,150,100)
FDAYS  <- 8                                     # forecast days
TOPN   <- 6                                     # average the N highest-severity hours

# Day 1's anchor date. The job runs at 18Z (02:00 AWST/04:00 AEST) specifically so it's
# ready right after Australian local midnight -- but 18Z is still the SAME UTC calendar
# day, so anchoring "Day 1" to raw UTC "today" (via forecast_days) labelled every run with
# the date that had just ended in Australia, one day stale by the time anyone looked at it.
# Anchoring instead to the AWST calendar date at request time (UTC+8, the country's LAST
# timezone to roll over each day) fixes this for both the 18Z schedule and any ad-hoc
# manual run: Day 1 always comes out as whichever Australian day has most recently started.
START_DATE <- format(Sys.time() + 8*3600, "%Y-%m-%d", tz="UTC")
END_DATE   <- as.character(as.Date(START_DATE) + FDAYS - 1)

nz <- function(x){ if (is.null(x) || is.na(x)) 0 else x }

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
  scp  <- nz(p[["SCP_new"]]); stp <- nz(p[["STP_new"]]); ship <- nz(p[["SHIP"]])
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
hail_tier <- function(ship, cape, frz_lvl_m, t500){
  base <- if (ship >= 2) 3 else if (ship >= 1) 2 else if (ship >= 0.5 & cape >= 300) 1 else 0
  cold_aloft <- cape >= 300 & ((!is.na(frz_lvl_m) & frz_lvl_m < 3400) | (!is.na(t500) & t500 <= -20))
  warm_aloft <- !is.na(frz_lvl_m) & frz_lvl_m > 4900
  if (cold_aloft & base >= 1 & base < 3) base <- base + 1
  if (warm_aloft & base >= 1) base <- base - 1
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
# 3 Very Destructive (>=160km/h) -- 90 and 125km/h are BOM's own real criteria for issuing and
# escalating a Severe Thunderstorm Warning for damaging/destructive winds; 160km/h is this
# pipeline's own extension for the rarer very-destructive tier, not a distinct BOM threshold.
# Magnitude comes from SPC's WNDG parameter -- a real, published convective wind-damage-potential
# index, WNDG = (MU_CAPE/2000) * (effective shear in m/s / 20) -- run through an unvalidated,
# hand-picked mapping onto these three tiers, since WNDG doesn't have a citable direct-to-gust-
# speed conversion; treat the exact breakpoints as approximate the same way hail_tier()'s are.
# EXPLICITLY zeroed below MDT (cat>=3 under the current 0-4 TSTM/MRGL/MDT/HIGH scale -- this was
# SLGT+ before SLGT was removed 26 Aug 2026; the literal "3" didn't need to change, only what it
# now means did, since MDT shifted down a slot to fill SLGT's old number): this is a "does the
# day's overall severe environment even support it" gate layered on top of the magnitude calc,
# unlike hail_tier(), which is shown regardless of the day's overall category.
# THRESHOLDS HALVED 25 Aug 2026: the original 0.6/1.4/2.4 guesses turned out to sit well above
# what this pipeline's own CAPE/shear ever actually produces for a genuinely SLGT+ day -- checked
# against the live run that day, every one of that day's 20 SLGT+ point-days computed a WNDG of
# 0.07-0.94 (median ~0.45), so only the single most extreme point in the whole country ever cleared
# the old 0.6 floor. NSW's north-coast SLGT points that day (CAPE ~900-1000 J/kg, shear ~28-36kt --
# a genuine, garden-variety severe-thunderstorm wind setup) sat at 0.34-0.44, comfortably inside a
# real damaging-gust risk but always displaying as "none" on the map. Halved rather than re-derived
# from scratch (still no citable WNDG-to-tier conversion exists), which lands the same top point at
# Destructive and puts most other SLGT+ days into Damaging -- validate again if a future run's
# WNDG distribution looks meaningfully different from this one.
wind_tier <- function(cape, shr, cat){
  if (nz(cat) < 3) return(0L)
  wndg <- (nz(cape)/2000) * (nz(shr)/20)
  if (wndg >= 1.2) return(3L)
  if (wndg >= 0.7) return(2L)
  if (wndg >= 0.3) return(1L)
  0L
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
categorise_vals <- function(cape, shr, scp, stp, ship, cin, rain_mm){
  shr_kt <- shr * 1.94384
  c <- 0
  if (cape >= 150) c <- 1                                                                   # TSTM
  if ((cape >= 450 & shr_kt >= 18) | (scp >= 0.9 & cape >= 900) | ship >= 0.45) c <- max(c, 2)  # MRGL
  if ((scp >= 3.6 & cape >= 900) | stp >= 1.8 | ship >= 1.8) c <- max(c, 3)   # MDT
  if ((scp >= 9   & cape >= 900) | stp >= 4.5)               c <- max(c, 4)   # HIGH

  capped  <- nz(cin) <= -75      # stout cap even on the best hour of the day
  no_trig <- nz(rain_mm) < 2     # GFS's own 24h precip forecast shows essentially no rain
  if (no_trig)                    c <- 0
  if (capped & !no_trig & c >= 3) c <- c - 1

  rc <- rain_cat(nz(rain_mm))
  c  <- max(c, rc)

  hatch <- as.integer(stp >= 0.9 | ship >= 0.9 | scp >= 3.6 | rc >= 3)
  list(cat=c, cape=round(cape), shear=round(shr_kt), scp=round(scp,1),
       stp=round(stp,1), ship=round(ship,1), cin=round(cin), rain=round(nz(rain_mm)), hatch=hatch)
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
  sprintf(paste0("https://api.open-meteo.com/v1/forecast?latitude=%.3f&longitude=%.3f",
    "&hourly=%s,%s&start_date=%s&end_date=%s&timezone=UTC&wind_speed_unit=kn&cell_selection=nearest"),
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
day_topN <- function(h, idxs, elev, lat){
  rows <- list()
  for (i in idxs){
    prof <- tryCatch(build_profile(h, i, elev), error=function(e) NULL)
    if (is.null(prof)) next
    par <- tryCatch(
      sounding_compute(prof$pressure, prof$altitude, prof$temp, prof$dpt, prof$wd, prof$ws, accuracy=1),
      error=function(e) NULL)
    if (is.null(par)) next
    rows[[length(rows)+1]] <- list(
      sev  = sev_score(par),
      cape = nz(par[["MU_CAPE"]]), shr = nz(par[["BS_EFF_MU"]]),
      scp  = nz(par[["SCP_new"]]), stp = nz(par[["STP_new"]]), ship = nz(par[["SHIP"]]),
      cin  = nz(par[["MU_CIN"]]), frz = h[["freezing_level_height"]][i],
      t500 = h[["temperature_500hPa"]][i],
      tprob = nz(h[["precipitation_probability"]][i]))
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

  if (length(rows) == 0){
    rc <- rain_cat(rain_day)
    # no successful soundings this day -- no instability-based hour selection to lean on, so fall
    # back to the day's mean precip-probability (still whole-day, but mean rather than max keeps a
    # single spurious overnight-drizzle hour from dominating the fallback the way max did before).
    tprob_fallback <- mean(sapply(idxs, function(i) nz(h[["precipitation_probability"]][i])))
    return(list(cat=rc, cape=0, shear=0, scp=0, stp=0, ship=0, cin=0, rain=round(rain_day), hatch=as.integer(rc>=3),
                tprob=thunder_prob(tprob_fallback, 0, rain_day), hail=0,
                flood=flood_cat(rain_day, rain_rate, rain_pop, lat), pop=round(rain_pop),
                fire=fire_tier(ffdi_day, rain_day), ffdi=round(ffdi_day), wind=0L))
  }
  sev <- sapply(rows, function(r) r$sev)
  top <- rows[order(sev, decreasing=TRUE)[seq_len(min(TOPN, length(rows)))]]
  m <- function(k) mean(sapply(top, function(r) r[[k]]))
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
  cv <- categorise_vals(m("cape"), m("shr"), m("scp"), m("stp"), m("ship"), m("cin"), rain_day)
  # thunderstorm chance: Open-Meteo's own ensemble-based precipitation_probability (%), averaged
  # over the SAME top-N instability-ranked hours as cape/shear/ship, not the whole day -- a whole-day
  # max picks up unrelated overnight drizzle (Open-Meteo's ensemble can be very confident about light,
  # non-convective rain at 7am) and reports it as a dramatic "thunderstorm chance" for the day.
  c(cv, list(tprob=thunder_prob(m("tprob"), m("cape"), rain_day),
             hail=hail_tier(peak_ship_hr$ship, peak_ship_hr$cape, frz_day, t500_day),
             flood=flood_cat(rain_day, rain_rate, rain_pop, lat), pop=round(rain_pop),
             fire=fire_tier(ffdi_day, rain_day), ffdi=round(ffdi_day),
             wind=wind_tier(m("cape"), m("shr"), cv$cat)))
}

# each point is a fully independent fetch+compute (no shared state), so this is embarrassingly
# parallel -- GitHub's ubuntu-latest runners give 4 vCPUs, and the old sequential loop spent most
# of its ~3.5h wall-clock either waiting on Open-Meteo's response or inside thundeR's per-hour
# sounding_compute() (up to 192 hours/point), both of which parallelize cleanly across points.
# mc.preschedule=FALSE hands points to workers one at a time as they free up rather than splitting
# the grid into 4 fixed static chunks up front, so one slow/retrying point doesn't leave a worker
# idle while the others finish their chunk.
suppressMessages(library(parallel))
NCORES <- max(1, min(4, parallel::detectCores()))
cat(sprintf("Processing %d grid points (%d days, avg of top %d hours) across %d workers...\n", nrow(GRID), FDAYS, TOPN, NCORES))

process_point <- function(k){
  tryCatch({
    lat <- GRID[k,1]; lon <- GRID[k,2]
    r <- fetch_point(lat, lon)
    if (is.null(r)) return(NULL)
    h <- r$hourly; elev <- r$elevation
    gp <- day_groups(h$time)
    dres <- lapply(gp$idx, function(ix) day_topN(h, ix, elev, lat))
    Sys.sleep(0.15)   # stay a courteous, gently-paced client per worker even with 4x concurrency
    list(lat=lat, lon=lon, d=dres, days=gp$days)
  }, error=function(e) NULL)
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
if (length(failed_idx) > 0) {
  cat(sprintf("First pass: %d/%d failed. Retrying failed points...\n", length(failed_idx), nrow(GRID)))
  retry_results <- mclapply(failed_idx, process_point, mc.cores=NCORES, mc.preschedule=FALSE)
  recovered <- 0
  for (j in seq_along(failed_idx)) {
    res <- retry_results[[j]]
    if (!is.null(res) && !inherits(res, "try-error")) recovered <- recovered + 1
    raw_results[[failed_idx[j]]] <- res
  }
  cat(sprintf("Retry recovered %d/%d previously-failed points.\n", recovered, length(failed_idx)))
}

points <- vector("list", nrow(GRID)); day_labels <- NULL; ok <- 0
for (k in seq_along(raw_results)){
  res <- raw_results[[k]]
  if (is.null(res) || inherits(res, "try-error")) next
  if (is.null(day_labels)) day_labels <- format(as.Date(res$days), "%a %e %b")
  points[[k]] <- list(lat=res$lat, lon=res$lon, d=res$d)
  ok <- ok + 1
}

points <- Filter(Negate(is.null), points)
out <- list(run_date = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz="UTC"),
            days = if (is.null(day_labels)) paste("Day", seq_len(FDAYS)) else day_labels,
            points = points)
write_json(out, OUT, auto_unbox=TRUE, digits=2)
cat(sprintf("Wrote %s  (%d points OK)\n", OUT, ok))
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
  cat(sprintf("Only %d/%d points OK (%.0f%%) -- below the %.0f%% completeness floor, not publishing this run.\n",
              ok, nrow(GRID), 100*ok/nrow(GRID), 100*MIN_OK_FRAC))
  quit(status=1)
}

# archive this run for the viewer's historical-run picker, dated by START_DATE (the run's own
# Day-1 anchor) so a given archive file always matches what that date's Day 1 actually looked
# like when it was generated -- same convention the "Update outlook YYYY-MM-DD" commit message
# already uses. Kept to a rolling ARCHIVE_DAYS window (pruned every run) so the repo doesn't
# grow unbounded; index.json lists what's currently available so the viewer doesn't have to
# guess dates and eat 404s.
ARCHIVE_DIR <- "docs/archive"
ARCHIVE_DAYS <- 14
if (!dir.exists(ARCHIVE_DIR)) dir.create(ARCHIVE_DIR, recursive=TRUE)
file.copy(OUT, file.path(ARCHIVE_DIR, paste0(START_DATE, ".json")), overwrite=TRUE)
existing <- list.files(ARCHIVE_DIR, pattern="^\\d{4}-\\d{2}-\\d{2}\\.json$")
existing_dates <- sub("\\.json$", "", existing)
cutoff <- as.Date(START_DATE) - ARCHIVE_DAYS
keep <- existing_dates[!is.na(as.Date(existing_dates)) & as.Date(existing_dates) >= cutoff]
stale <- setdiff(existing, paste0(keep, ".json"))
if (length(stale) > 0) file.remove(file.path(ARCHIVE_DIR, stale))
write_json(sort(keep), file.path(ARCHIVE_DIR, "index.json"))  # NOT auto_unbox: must stay an
# array even when only one date exists yet, since the viewer always expects to parse a list
cat(sprintf("Archived run for %s (%d dates kept, %d pruned)\n", START_DATE, length(keep), length(stale)))
