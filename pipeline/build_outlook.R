#!/usr/bin/env Rscript
# ---------------------------------------------------------------------------
# CyclonesOZ automated severe-storm outlook
# GFS profiles (via Open-Meteo JSON) -> thundeR parameters -> SPC-style category
# For each day: sample the noon-6pm local window, average the parameters,
# then weight 20% toward the most severe hour. Writes docs/outlook.json.
# Runs daily in GitHub Actions.
# ---------------------------------------------------------------------------
suppressMessages({
  library(thunder)     # convective-parameter engine
  library(jsonlite)
})

GRID   <- fromJSON("data/grid.json")           # matrix [,1]=lat [,2]=lon
OUT    <- "docs/outlook.json"
LEVELS <- c(1000,975,950,925,900,850,800,700,600,500,400,300,250,200,150,100)
WIN_START <- 12    # local hour window start (noon)
WIN_END   <- 18    # local hour window end (6pm)
SEV_WEIGHT <- 0.2  # weight toward the most severe hour (0.2 = 80% mean + 20% max)

# --- dewpoint from RH + temp (Magnus) --------------------------------------
dewpoint <- function(T, RH){
  RH[is.na(RH)] <- 1; RH[RH < 1] <- 1
  a <- 17.625; b <- 243.04
  g <- log(RH/100) + (a*T)/(b+T)
  (b*g)/(a-g)
}

# blend a vector of hourly values: (1-w)*mean + w*max  -> lean toward severe
blend <- function(v){
  v <- v[!is.na(v)]
  if (length(v) == 0) return(0)
  (1 - SEV_WEIGHT) * mean(v) + SEV_WEIGHT * max(v)
}
pv <- function(par, name){ v <- par[[name]]; if (is.null(v) || is.na(v)) 0 else v }

# --- SPC-style category from (blended) thundeR parameters (TUNABLE) --------
# 0 none, 1 TSTM, 2 MRGL, 3 SLGT, 4 ENH, 5 MDT, 6 HIGH
categorise <- function(cape, shr, scp, stp, ship){
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
    "&hourly=%s,%s&forecast_days=4&timezone=auto&wind_speed_unit=kn&cell_selection=nearest"),
    lat, lon, sfc, lv)
}

fetch_point <- function(lat, lon){
  for (a in 1:4){
    r <- tryCatch(fromJSON(om_url(lat,lon)), error=function(e) NULL)
    if (!is.null(r) && !is.null(r$hourly)) return(r)
    Sys.sleep(1.2*a)
  }
  NULL
}

# build one profile (surface + levels above ground) for a given hourly index
build_profile <- function(h, i, elev){
  pres <- c(h[["surface_pressure"]][i])
  alt  <- c(if (!is.na(elev)) elev else 0)
  tmp  <- c(h[["temperature_2m"]][i])
  dpt  <- c(h[["dew_point_2m"]][i])
  wd   <- c(h[["wind_direction_10m"]][i])
  ws   <- c(h[["wind_speed_10m"]][i])
  sp   <- h[["surface_pressure"]][i]
  for (L in LEVELS){
    if (is.na(sp) || L >= sp) next                     # skip underground levels
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

# noon-6pm local-hour indices for each of the 4 forecast days
window_indices <- function(h){
  times <- h$time
  dts <- substr(times, 1, 10); hrs <- as.integer(substr(times, 12, 13))
  days <- unique(dts)[1:4]
  idxlist <- lapply(days, function(d) which(dts == d & hrs >= WIN_START & hrs <= WIN_END))
  list(days=days, idxlist=idxlist)
}

# compute the blended category for one day over its window hours
day_category <- function(h, inds, elev){
  cape <- c(); shr <- c(); scp <- c(); stp <- c(); ship <- c()
  for (i in inds){
    prof <- tryCatch(build_profile(h, i, elev), error=function(e) NULL)
    if (is.null(prof)) next
    par <- tryCatch(
      sounding_compute(prof$pressure, prof$altitude, prof$temp, prof$dpt, prof$wd, prof$ws, accuracy=1),
      error=function(e) NULL)
    if (is.null(par)) next
    cape <- c(cape, pv(par,"MU_CAPE")); shr <- c(shr, pv(par,"BS_EFF_MU"))
    scp  <- c(scp,  pv(par,"SCP_new")); stp <- c(stp, pv(par,"STP_new"))
    ship <- c(ship, pv(par,"SHIP"))
  }
  if (length(cape) == 0) return(list(cat=0,cape=0,shear=0,scp=0,stp=0,ship=0,hatch=0))
  categorise(blend(cape), blend(shr), blend(scp), blend(stp), blend(ship))
}

cat(sprintf("Processing %d grid points (noon-6pm window, %.0f%% severe-weighted)...\n",
            nrow(GRID), SEV_WEIGHT*100))
points <- vector("list", nrow(GRID)); day_labels <- NULL; ok <- 0

for (k in seq_len(nrow(GRID))){
  lat <- GRID[k,1]; lon <- GRID[k,2]
  r <- fetch_point(lat, lon)
  if (is.null(r)){ points[[k]] <- NULL; next }
  h <- r$hourly; elev <- r$elevation
  wi <- window_indices(h)
  if (is.null(day_labels)) day_labels <- format(as.Date(wi$days), "%a %e %b")
  dres <- list()
  for (di in seq_along(wi$idxlist)){
    inds <- wi$idxlist[[di]]
    dres[[di]] <- if (length(inds) == 0) list(cat=0,cape=0,shear=0,scp=0,stp=0,ship=0,hatch=0)
                  else day_category(h, inds, elev)
  }
  points[[k]] <- list(lat=lat, lon=lon, d=dres)
  ok <- ok + 1
  if (k %% 25 == 0) cat(sprintf("  %d/%d\n", k, nrow(GRID)))
  Sys.sleep(0.25)
}

points <- Filter(Negate(is.null), points)
out <- list(run_date = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz="UTC"),
            days = if (is.null(day_labels)) c("Day 1","Day 2","Day 3","Day 4") else day_labels,
            points = points)
write_json(out, OUT, auto_unbox=TRUE, digits=2)
cat(sprintf("Wrote %s  (%d points OK)\n", OUT, ok))
if (ok == 0) quit(status=1)
