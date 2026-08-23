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
sev_score <- function(p){
  cape <- nz(p[["MU_CAPE"]]); shr <- nz(p[["BS_EFF_MU"]])*1.94384
  scp  <- nz(p[["SCP_new"]]); stp <- nz(p[["STP_new"]]); ship <- nz(p[["SHIP"]])
  2*scp + 2*stp + 2*ship + cape/500 + shr/20
}

# daily accumulated rainfall (mm, GFS's own total precip forecast) -> the same 0-5 scale,
# folded into the composite category below: any hazard capable of occurring on the day --
# hail, wind, tornado, or flash-flood rain -- lifts the overall risk shown on the map.
rain_cat <- function(mm){
  if (mm >= 150) return(5)
  if (mm >= 75)  return(4)
  if (mm >= 50)  return(3)
  if (mm >= 35)  return(2)
  if (mm >= 10)  return(1)
  0
}

# hail size tier: 0 none/sub-severe, 1 small (<2cm), 2 large (2-5cm), 3 giant (>5cm).
# SHIP is SPC's own significant-hail parameter (calibrated to >=2in/5cm hail potential), so
# its own 1/2 breakpoints are kept as the primary driver. frz_lvl_m is the altitude of the 0C
# level during the day's most unstable hours -- Raupach et al. 2023 (Mon. Wea. Rev. 151,
# doi:10.1175/mwr-d-22-0127.1) found melting-level height is the key variable needed to correct
# naive instability-shear hail proxies for Australia, and specifically that proxies without it
# OVERESTIMATE hail probability in Australia's tropical north. Both directions here follow that
# finding: a lower freezing level gives a falling hailstone less distance to melt (cold_aloft
# bumps the tier up), while an unusually high freezing level -- common in the tropics -- gives
# it much more distance to melt before reaching the ground (warm_aloft pulls the tier back down).
# The specific 3400m/4900m cutoffs are our own working thresholds from general hail-forecasting
# practice, not numbers taken from the paper (which uses a continuous correction, not a step),
# so treat the exact breakpoints as approximate even though the melting-level concept is now
# directly evidenced for Australia.
hail_tier <- function(ship, cape, frz_lvl_m){
  base <- if (ship >= 2) 3 else if (ship >= 1) 2 else if (ship >= 0.5 & cape >= 300) 1 else 0
  cold_aloft <- !is.na(frz_lvl_m) & frz_lvl_m < 3400 & cape >= 300
  warm_aloft <- !is.na(frz_lvl_m) & frz_lvl_m > 4900
  if (cold_aloft & base >= 1 & base < 3) base <- base + 1
  if (warm_aloft & base >= 1) base <- base - 1
  base
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
flood_cat <- function(rain_mm, rate_mm, pop){
  if (nz(pop) < 25) return(0L)
  rm <- nz(rain_mm); rt <- nz(rate_mm)
  if (rm >= 500 | rt >= 250) return(3L)   # high
  if (rm >= 250 | rt >= 175) return(2L)   # moderate
  if (rm >= 150 | rt >= 100) return(1L)   # slight
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
thunder_prob <- function(tprob, cape, rain_mm){
  if (nz(rain_mm) < 2)   return(0L)
  if (nz(cape) < 100)    return(as.integer(round(nz(tprob) * 0.3)))
  as.integer(round(nz(tprob)))
}

# SPC-style category from averaged parameters: 0 none,1 TSTM,2 MRGL,3 SLGT,4 MDT,5 HIGH
# ENH was removed and MDT dropped to its old bar (formerly ENH's threshold) so MDT reads as the
# practical ceiling most genuinely significant days reach; HIGH keeps its old (unchanged, and now
# the only tier above MDT) bar so it stays reserved for the rare, potentially life-threatening
# outbreak days -- the old MDT tier's own distinct threshold is gone; that value range now just
# stays at MDT rather than independently promoting to a tier of its own.
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
  if (cape >= 150) c <- 1
  if ((cape >= 450 & shr_kt >= 18) | (scp >= 0.9 & cape >= 900) | ship >= 0.45) c <- max(c, 2)
  if ((scp >= 1.8 & cape >= 900) | stp >= 0.9 | ship >= 0.9 | (cape >= 900 & shr_kt >= 27)) c <- max(c, 3)
  if ((scp >= 3.6 & cape >= 900) | stp >= 1.8 | ship >= 1.8) c <- max(c, 4)   # MDT
  if ((scp >= 9   & cape >= 900) | stp >= 4.5)               c <- max(c, 5)   # HIGH

  capped  <- nz(cin) <= -75      # stout cap even on the best hour of the day
  no_trig <- nz(rain_mm) < 2     # GFS's own 24h precip forecast shows essentially no rain
  if (no_trig)                    c <- 0
  if (capped & !no_trig & c >= 4) c <- c - 1

  rc <- rain_cat(nz(rain_mm))
  c  <- max(c, rc)

  hatch <- as.integer(stp >= 0.9 | ship >= 0.9 | scp >= 3.6 | rc >= 4)
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
  sfc <- "temperature_2m,dew_point_2m,surface_pressure,wind_speed_10m,wind_direction_10m,precipitation,precipitation_probability,freezing_level_height"
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
day_topN <- function(h, idxs, elev){
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
      tprob = nz(h[["precipitation_probability"]][i]))
  }
  rain_day  <- sum(sapply(idxs, function(i) nz(h[["precipitation"]][i])))
  rain_rate <- max(sapply(idxs, function(i) nz(h[["precipitation"]][i])))  # peak single-hour rate, for flood_cat()
  # day's peak-confidence hour (max, not mean) -- flood_cat() gates on the model's HIGHEST
  # same-day confidence that rain occurs at all, paired with the day's peak magnitude (also a max).
  rain_pop  <- max(sapply(idxs, function(i) nz(h[["precipitation_probability"]][i])))

  if (length(rows) == 0){
    rc <- rain_cat(rain_day)
    # no successful soundings this day -- no instability-based hour selection to lean on, so fall
    # back to the day's mean precip-probability (still whole-day, but mean rather than max keeps a
    # single spurious overnight-drizzle hour from dominating the fallback the way max did before).
    tprob_fallback <- mean(sapply(idxs, function(i) nz(h[["precipitation_probability"]][i])))
    return(list(cat=rc, cape=0, shear=0, scp=0, stp=0, ship=0, cin=0, rain=round(rain_day), hatch=as.integer(rc>=4),
                tprob=thunder_prob(tprob_fallback, 0, rain_day), hail=0,
                flood=flood_cat(rain_day, rain_rate, rain_pop), pop=round(rain_pop)))
  }
  sev <- sapply(rows, function(r) r$sev)
  top <- rows[order(sev, decreasing=TRUE)[seq_len(min(TOPN, length(rows)))]]
  m <- function(k) mean(sapply(top, function(r) r[[k]]))
  # coldest freezing level among the day's most unstable hours -- see hail_tier() for why
  frz_day <- suppressWarnings(min(sapply(top, function(r) r$frz), na.rm=TRUE))
  if (!is.finite(frz_day)) frz_day <- NA
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
             hail=hail_tier(peak_ship_hr$ship, peak_ship_hr$cape, frz_day),
             flood=flood_cat(rain_day, rain_rate, rain_pop), pop=round(rain_pop)))
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
    dres <- lapply(gp$idx, function(ix) day_topN(h, ix, elev))
    Sys.sleep(0.15)   # stay a courteous, gently-paced client per worker even with 4x concurrency
    list(lat=lat, lon=lon, d=dres, days=gp$days)
  }, error=function(e) NULL)
}

raw_results <- mclapply(seq_len(nrow(GRID)), process_point, mc.cores=NCORES, mc.preschedule=FALSE)

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
if (ok == 0) quit(status=1)

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
