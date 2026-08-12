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

# daily accumulated rainfall (mm, GFS's own total precip forecast) -> the same 0-6 scale,
# folded into the composite category below: any hazard capable of occurring on the day --
# hail, wind, tornado, or flash-flood rain -- lifts the overall risk shown on the map.
rain_cat <- function(mm){
  if (mm >= 150) return(6)
  if (mm >= 100) return(5)
  if (mm >= 75)  return(4)
  if (mm >= 50)  return(3)
  if (mm >= 35)  return(2)
  if (mm >= 10)  return(1)
  0
}

# hail size tier: 0 none/sub-severe, 1 small (<2cm), 2 large (2-5cm), 3 giant (>5cm).
# SHIP is SPC's own significant-hail parameter (calibrated to >=2in/5cm hail potential), so
# its own 1/2 breakpoints are kept as the primary driver. frz_lvl_m is the altitude of the 0C
# level during the day's most unstable hours: a lower freezing level gives a falling hailstone
# less distance to melt, so unusually cold air aloft can support larger hail than SHIP alone
# would suggest on a modest-CAPE day. 3400m and the resulting one-tier bump are our own working
# threshold from general hail-forecasting practice (lower 0C level -> bigger hail, all else
# equal), not a published Australian-calibrated number -- same caveat as the category thresholds.
hail_tier <- function(ship, cape, frz_lvl_m){
  base <- if (ship >= 2) 3 else if (ship >= 1) 2 else if (ship >= 0.5 | cape >= 500) 1 else 0
  cold_aloft <- !is.na(frz_lvl_m) & frz_lvl_m < 3400 & cape >= 300
  if (cold_aloft & base >= 1 & base < 3) base <- base + 1
  base
}

# flash-flood risk: 0 low, 1 high. Reuses the same daily-rain total already computed for
# rain_cat(); 50mm/day is the same threshold that lifts the main category to SLGT-equivalent.
flood_risk <- function(rain_mm){ as.integer(nz(rain_mm) >= 50) }

# SPC-style category from averaged parameters: 0 none,1 TSTM,2 MRGL,3 SLGT,4 ENH,5 MDT,6 HIGH
# cin = MU_CIN (J/kg, <=0, from the same sounding as cape/shr) and shw = GFS's own forecast
# convective showers (mm, max over the whole day) act as an initiation check: CAPE alone is
# a very low bar in the moist tropics, so if the model's own convection scheme sees nothing
# forming all day, the category is zeroed rather than firing on bare environment. rain_mm is
# the day's accumulated total precip, independently folded in via rain_cat() so heavy-rain
# days show up even when the severe-hazard parameters alone wouldn't flag anything.
#
# CAPE/SCP/STP/SHIP threshold NOTE: the numeric cutoffs below (SCP 1/2/4/6/10, STP 1/2/3/5,
# SHIP 0.5/1/2/3, and the CAPE+shear combo gates) are the US Storm Prediction Center's own
# values, calibrated against the US Great Plains severe-report climatology. Published Australian
# work (Allen, Karoly & Mills 2011, Aust. Met. Ocean. J. 61; Allen & Karoly 2014, Int. J.
# Climatol. 34) finds Australian severe thunderstorms commonly occur at lower CAPE than their US
# counterparts for a comparable severe outcome -- but a proper regional discriminant needs a fit
# against Australian report data this pipeline doesn't have access to. The ~20% reduction applied
# here (~10% on the wind-speed terms, since shear is measured directly rather than a derived proxy)
# is a directional nudge in that documented direction, not a fitted recalibration -- treat category
# boundaries as approximate until validated against real Australian severe reports.
categorise_vals <- function(cape, shr, scp, stp, ship, cin, shw, rain_mm){
  shr_kt <- shr * 1.94384
  c <- 0
  if (cape >= 150) c <- 1
  if ((cape >= 400 & shr_kt >= 18) | scp >= 0.8 | ship >= 0.4) c <- max(c, 2)
  if (scp >= 1.6 | stp >= 0.8 | ship >= 0.8 | (cape >= 800 & shr_kt >= 27)) c <- max(c, 3)
  if (scp >= 3.2 | stp >= 1.6 | ship >= 1.6) c <- max(c, 4)
  if (scp >= 4.8 | stp >= 2.4 | ship >= 2.4) c <- max(c, 5)
  if (scp >= 8   | stp >= 4)                 c <- max(c, 6)

  capped  <- nz(cin) <= -75      # stout cap even on the best hour of the day
  no_trig <- nz(shw) < 0.1       # GFS's own cumulus scheme sees nothing forming all day
  if (no_trig)                    c <- 0
  if (capped & !no_trig & c >= 4) c <- c - 1

  rc <- rain_cat(nz(rain_mm))
  c  <- max(c, rc)

  hatch <- as.integer(stp >= 0.8 | ship >= 0.8 | scp >= 3.2 | rc >= 4)
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
  sfc <- "temperature_2m,dew_point_2m,surface_pressure,wind_speed_10m,wind_direction_10m,showers,precipitation,precipitation_probability,freezing_level_height"
  sprintf(paste0("https://api.open-meteo.com/v1/forecast?latitude=%.3f&longitude=%.3f",
    "&hourly=%s,%s&forecast_days=%d&timezone=auto&wind_speed_unit=kn&cell_selection=nearest"),
    lat, lon, sfc, lv, FDAYS)
}

fetch_point <- function(lat, lon){
  for (a in 1:4){
    r <- tryCatch(fromJSON(om_url(lat,lon)), error=function(e) NULL)
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

# a day's value = average of the TOPN highest-severity hours. shw/rain are checked across
# the WHOLE day (not just the top-N severity hours) since those hours are picked by an
# instability score, not a precip score -- the day's actual rain chance/total can peak at
# an hour that score didn't select.
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
  shw_day   <- max(sapply(idxs, function(i) nz(h[["showers"]][i])))
  rain_day  <- sum(sapply(idxs, function(i) nz(h[["precipitation"]][i])))

  if (length(rows) == 0){
    rc <- rain_cat(rain_day)
    # no successful soundings this day -- no instability-based hour selection to lean on, so fall
    # back to the day's mean precip-probability (still whole-day, but mean rather than max keeps a
    # single spurious overnight-drizzle hour from dominating the fallback the way max did before).
    tprob_fallback <- mean(sapply(idxs, function(i) nz(h[["precipitation_probability"]][i])))
    return(list(cat=rc, cape=0, shear=0, scp=0, stp=0, ship=0, cin=0, rain=round(rain_day), hatch=as.integer(rc>=4),
                tprob=round(tprob_fallback), hail=0, flood=flood_risk(rain_day)))
  }
  sev <- sapply(rows, function(r) r$sev)
  top <- rows[order(sev, decreasing=TRUE)[seq_len(min(TOPN, length(rows)))]]
  m <- function(k) mean(sapply(top, function(r) r[[k]]))
  # coldest freezing level among the day's most unstable hours -- see hail_tier() for why
  frz_day <- suppressWarnings(min(sapply(top, function(r) r$frz), na.rm=TRUE))
  if (!is.finite(frz_day)) frz_day <- NA
  cv <- categorise_vals(m("cape"), m("shr"), m("scp"), m("stp"), m("ship"), m("cin"), shw_day, rain_day)
  # thunderstorm chance: Open-Meteo's own ensemble-based precipitation_probability (%), averaged
  # over the SAME top-N instability-ranked hours as cape/shear/ship, not the whole day -- a whole-day
  # max picks up unrelated overnight drizzle (Open-Meteo's ensemble can be very confident about light,
  # non-convective rain at 7am) and reports it as a dramatic "thunderstorm chance" for the day.
  c(cv, list(tprob=round(m("tprob")), hail=hail_tier(m("ship"), m("cape"), frz_day), flood=flood_risk(rain_day)))
}

cat(sprintf("Processing %d grid points (%d days, avg of top %d hours)...\n", nrow(GRID), FDAYS, TOPN))
points <- vector("list", nrow(GRID)); day_labels <- NULL; ok <- 0

for (k in seq_len(nrow(GRID))){
  lat <- GRID[k,1]; lon <- GRID[k,2]
  r <- fetch_point(lat, lon)
  if (is.null(r)){ points[[k]] <- NULL; next }
  h <- r$hourly; elev <- r$elevation
  gp <- day_groups(h$time)
  if (is.null(day_labels)) day_labels <- format(as.Date(gp$days), "%a %e %b")
  dres <- lapply(gp$idx, function(ix) day_topN(h, ix, elev))
  points[[k]] <- list(lat=lat, lon=lon, d=dres)
  ok <- ok + 1
  if (k %% 50 == 0) cat(sprintf("  %d/%d\n", k, nrow(GRID)))
  Sys.sleep(0.2)
}

points <- Filter(Negate(is.null), points)
out <- list(run_date = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz="UTC"),
            days = if (is.null(day_labels)) paste("Day", seq_len(FDAYS)) else day_labels,
            points = points)
write_json(out, OUT, auto_unbox=TRUE, digits=2)
cat(sprintf("Wrote %s  (%d points OK)\n", OUT, ok))
if (ok == 0) quit(status=1)
