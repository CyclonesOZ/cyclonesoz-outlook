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

# SPC-style category from averaged parameters: 0 none,1 TSTM,2 MRGL,3 SLGT,4 ENH,5 MDT,6 HIGH
categorise_vals <- function(cape, shr, scp, stp, ship){
  shr_kt <- shr * 1.94384
  c <- 0
  if (cape >= 150) c <- 1
  if ((cape >= 500 & shr_kt >= 20) | scp >= 1 | ship >= 0.5) c <- max(c, 2)
  if (scp >= 2 | stp >= 1 | ship >= 1 | (cape >= 1000 & shr_kt >= 30)) c <- max(c, 3)
  if (scp >= 4 | stp >= 2 | ship >= 2) c <- max(c, 4)
  if (scp >= 6 | stp >= 3 | ship >= 3) c <- max(c, 5)
  if (scp >= 10 | stp >= 5)            c <- max(c, 6)
  hatch <- as.integer(stp >= 1 | ship >= 1 | scp >= 4)
  list(cat=c, cape=round(cape), shear=round(shr_kt), scp=round(scp,1),
       stp=round(stp,1), ship=round(ship,1), hatch=hatch)
}

om_url <- function(lat, lon){
  lv <- paste0(c(
    paste0("temperature_",LEVELS,"hPa"),
    paste0("relative_humidity_",LEVELS,"hPa"),
    paste0("wind_speed_",LEVELS,"hPa"),
    paste0("wind_direction_",LEVELS,"hPa"),
    paste0("geopotential_height_",LEVELS,"hPa")), collapse=",")
  sfc <- "temperature_2m,dew_point_2m,surface_pressure,wind_speed_10m,wind_direction_10m"
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

# a day's value = average of the TOPN highest-severity hours
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
      scp  = nz(par[["SCP_new"]]), stp = nz(par[["STP_new"]]), ship = nz(par[["SHIP"]]))
  }
  if (length(rows) == 0) return(list(cat=0,cape=0,shear=0,scp=0,stp=0,ship=0,hatch=0))
  sev <- sapply(rows, function(r) r$sev)
  top <- rows[order(sev, decreasing=TRUE)[seq_len(min(TOPN, length(rows)))]]
  m <- function(k) mean(sapply(top, function(r) r[[k]]))
  categorise_vals(m("cape"), m("shr"), m("scp"), m("stp"), m("ship"))
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
