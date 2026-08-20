library(jsonlite)
cat("R version:", R.version.string, "\n")
cat("jsonlite version:", as.character(packageVersion("jsonlite")), "\n")

FDAYS <- 8
START_DATE <- format(Sys.time() + 8*3600, "%Y-%m-%d", tz="UTC")
END_DATE   <- as.character(as.Date(START_DATE) + FDAYS - 1)
cat("START_DATE:", START_DATE, "END_DATE:", END_DATE, "\n")

LEVELS <- c(1000,975,950,925,900,850,800,750,700,650,600,550,500,450,400,350)
sfc <- "temperature_2m,dew_point_2m,surface_pressure,wind_direction_10m,wind_speed_10m,precipitation,precipitation_probability,showers,cape"
lv <- paste(sapply(LEVELS, function(L) paste0(
  "temperature_",L,"hPa,relative_humidity_",L,"hPa,geopotential_height_",L,"hPa,wind_speed_",L,"hPa,wind_direction_",L,"hPa"
)), collapse=",")

om_url <- function(lat, lon){
  sprintf(paste0("https://api.open-meteo.com/v1/forecast?latitude=%.3f&longitude=%.3f",
    "&hourly=%s,%s&start_date=%s&end_date=%s&timezone=UTC&wind_speed_unit=kn&cell_selection=nearest"),
    lat, lon, sfc, lv, START_DATE, END_DATE)
}

pts <- list(c(-31.95, 115.86), c(-33.87, 151.21), c(-27.47, 153.03))
for (p in pts) {
  lat <- p[1]; lon <- p[2]
  u <- om_url(lat, lon)
  cat("\n--- point", lat, lon, "---\n")
  cat("URL length:", nchar(u), "\n")
  t0 <- Sys.time()
  r <- tryCatch(fromJSON(u), error = function(e) { cat("ERROR (direct url):", conditionMessage(e), "\n"); NULL })
  t1 <- Sys.time()
  cat("elapsed (direct url):", as.numeric(difftime(t1, t0, units="secs")), "s\n")
  if (!is.null(r)) {
    cat("names(r):", paste(names(r), collapse=", "), "\n")
    cat("has hourly:", !is.null(r$hourly), "\n")
  } else {
    cat("r is NULL (direct url)\n")
  }

  # candidate fix: fetch the body ourselves via readLines so fromJSON always
  # receives raw JSON text (starts with '{'), never ambiguous with a URL/path
  t2 <- Sys.time()
  r2 <- tryCatch({
    raw <- paste(readLines(u, warn=FALSE), collapse="")
    fromJSON(raw)
  }, error = function(e) { cat("ERROR (readLines fix):", conditionMessage(e), "\n"); NULL })
  t3 <- Sys.time()
  cat("elapsed (readLines fix):", as.numeric(difftime(t3, t2, units="secs")), "s\n")
  if (!is.null(r2)) {
    cat("names(r2):", paste(names(r2), collapse=", "), "\n")
    cat("has hourly (fix):", !is.null(r2$hourly), "\n")
  } else {
    cat("r2 is NULL (readLines fix)\n")
  }
}
