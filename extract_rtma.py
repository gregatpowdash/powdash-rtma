#!/usr/bin/env python3
"""PowDash RTMA job: NOAA's hourly analysis of actual surface conditions at
each zone's peak, sent to the PowDash server.

Runs hourly on GitHub Actions (.github/workflows/rtma.yml):
  1. GET  POINTS_URL   -> [{id, lat, lon, ft, grid: "conus"|"ak"}, ...]
  2. Download the newest RTMA analysis (NOAA Open Data on AWS, bucket
     noaa-rtma-pds: rtma2p5 for the lower 48, akrtma for Alaska).
  3. Read temperature, wind speed, gust, direction and the grid's terrain
     height at the grid cell nearest each point (ecCodes).
  4. POST INGEST_URL with {"v": 1, "files": {...}, "points": {id: {...}}}
     (Authorization: Bearer RTMA_KEY).

Units sent: t_f (F), ws_mph, wg_mph, wd (degrees from), cell_ft (grid cell
height; the server corrects temperature to the peak's height), valid (unix).

Environment: RTMA_KEY (required unless DRY_RUN=1), POINTS_URL, INGEST_URL,
DRY_RUN=1 (print instead of posting), RTMA_FILE_CONUS / RTMA_FILE_AK (use a
local file instead of downloading, for testing).
"""
import datetime as dt
import json
import math
import os
import re
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np

POINTS_URL = os.environ.get("POINTS_URL", "https://api.powdash.co/v1/rtma-points.json")
INGEST_URL = os.environ.get("INGEST_URL", "https://api.powdash.co/admin/rtma")
BUCKET = "https://noaa-rtma-pds.s3.amazonaws.com"
UA = "powdash-rtma (sales@powdash.co)"

# Which files: per grid, the folder prefix and the analysis file name pattern
GRIDS = {
    "conus": ("rtma2p5.{ymd}/", re.compile(r"rtma2p5\.t(\d\d)z\.2dvaranl_ndfd\.grb2(_wexp)?$")),
    "ak": ("akrtma.{ymd}/", re.compile(r"akrtma\.t(\d\d)z\.2dvaranl_ndfd(_3p0)?\.grb2(_wexp)?$")),
}

# GRIB short names (ecCodes) for each quantity, in order of preference
NAMES = {
    "t": ["2t", "t"],
    "ws": ["10si", "si10", "ws", "wind"],
    "wd": ["10wdir", "wdir10", "wdir"],
    "wg": ["i10fg", "10fg", "gust"],
    "u": ["10u", "u10", "u"],
    "v": ["10v", "v10", "v"],
    "h": ["orog", "h", "gh", "z"],
}

MS_TO_MPH = 2.236936
M_TO_FT = 3.28084


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def newest_file(grid, now):
    """Key of the newest analysis file for a grid (today, else yesterday, UTC)."""
    prefix_fmt, pat = GRIDS[grid]
    for days_back in (0, 1):
        ymd = (now - dt.timedelta(days=days_back)).strftime("%Y%m%d")
        prefix = prefix_fmt.format(ymd=ymd)
        xml = http_get(f"{BUCKET}/?list-type=2&prefix={prefix}&max-keys=1000")
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys = [k.text for k in ET.fromstring(xml).findall(".//s3:Contents/s3:Key", ns)]
        best = None
        for k in keys:
            m = pat.search(k.rsplit("/", 1)[-1])
            if not m:
                continue
            hour = int(m.group(1))
            # prefer the extended (_wexp) file of the same hour: it has gusts
            rank = (hour, 1 if k.endswith("_wexp") else 0)
            if best is None or rank > best[0]:
                best = (rank, k)
        if best:
            return best[1]
    return None


def read_grib(path):
    """{quantity: (values, valid_unix)} plus (lats, lons) from one GRIB2 file."""
    import eccodes as ec  # imported here so the pure functions can be tested without it

    fields, inventory, lats, lons = {}, [], None, None
    with open(path, "rb") as f:
        while True:
            gid = ec.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                sn = ec.codes_get(gid, "shortName")
                lev_type = ec.codes_get(gid, "typeOfLevel")
                level = ec.codes_get(gid, "level")
                inventory.append(f"{sn}:{lev_type}:{level}")
                qty = next((q for q, names in NAMES.items() if sn in names), None)
                if qty is None or qty in fields:
                    continue
                if qty == "t" and sn == "t" and not (lev_type == "heightAboveGround" and level == 2):
                    continue
                if qty in ("u", "v") and sn in ("u", "v") and not (lev_type == "heightAboveGround" and level == 10):
                    continue
                if qty == "h" and lev_type != "surface":
                    continue
                vals = ec.codes_get_values(gid).astype("float64")
                missing = ec.codes_get(gid, "missingValue")
                vals[vals == missing] = np.nan
                if qty == "h" and sn == "z":
                    vals = vals / 9.80665          # geopotential -> metres
                date, time = ec.codes_get(gid, "validityDate"), ec.codes_get(gid, "validityTime")
                valid = int(dt.datetime.strptime(f"{date}{time:04d}", "%Y%m%d%H%M")
                            .replace(tzinfo=dt.timezone.utc).timestamp())
                fields[qty] = (vals, valid)
                if lats is None:
                    lats = ec.codes_get_array(gid, "latitudes").astype("float64")
                    lons = ec.codes_get_array(gid, "longitudes").astype("float64")
            finally:
                ec.codes_release(gid)
    print(f"  {os.path.basename(path)}: {len(inventory)} fields: {', '.join(inventory[:30])}")
    return fields, lats, lons


def nearest_index(lats, lons, lat, lon):
    """Index of the grid cell nearest a point (equirectangular distance)."""
    lon = ((lon + 180) % 360) - 180
    glon = ((lons + 180) % 360) - 180
    dlat = lats - lat
    dlon = (glon - lon) * math.cos(math.radians(lat))
    return int(np.nanargmin(dlat * dlat + dlon * dlon))


def point_values(fields, idx):
    """F / mph / degrees / ft at one grid index; None where a field is missing."""
    def get(q):
        if q not in fields:
            return None
        v = fields[q][0][idx]
        return None if not np.isfinite(v) else float(v)

    t_k, ws, wd, wg = get("t"), get("ws"), get("wd"), get("wg")
    u, v = get("u"), get("v")
    if ws is None and u is not None and v is not None:
        ws = math.hypot(u, v)
    if wd is None and u is not None and v is not None and (u or v):
        wd = (math.degrees(math.atan2(-u, -v)) + 360) % 360   # direction the wind blows FROM
    h = get("h")
    r1 = lambda x: None if x is None else round(x, 1)
    return {
        "t_f": r1(None if t_k is None else (t_k - 273.15) * 9 / 5 + 32),
        "ws_mph": r1(None if ws is None else ws * MS_TO_MPH),
        "wg_mph": r1(None if wg is None else wg * MS_TO_MPH),
        "wd": r1(wd),
        "cell_ft": r1(None if h is None else h * M_TO_FT),
    }


def extract(points, fields, lats, lons, max_miles=5.0):
    """{id: values} for the points on this grid. Points farther than max_miles
    from any grid cell (outside the grid) are skipped."""
    out = {}
    valid = next(iter(fields.values()))[1] if fields else None
    for p in points:
        i = nearest_index(lats, lons, p["lat"], p["lon"])
        dist_mi = 69.0 * math.hypot(lats[i] - p["lat"],
                                    (((lons[i] - p["lon"] + 180) % 360) - 180) * math.cos(math.radians(p["lat"])))
        if dist_mi > max_miles:
            continue
        vals = point_values(fields, i)
        if vals["t_f"] is None and vals["ws_mph"] is None:
            continue
        out[p["id"]] = {"valid": valid, **vals}
    return out


def main():
    dry = os.environ.get("DRY_RUN") == "1"
    key = os.environ.get("RTMA_KEY", "")
    if not key and not dry:
        sys.exit("RTMA_KEY is not set")
    points = json.loads(http_get(POINTS_URL))
    by_grid = {}
    for p in points:
        by_grid.setdefault(p.get("grid", "conus"), []).append(p)
    now = dt.datetime.now(dt.timezone.utc)
    result, files = {}, {}
    for grid, pts in by_grid.items():
        local = os.environ.get(f"RTMA_FILE_{grid.upper()}")
        try:
            if local:
                path, name = local, os.path.basename(local)
            else:
                k = newest_file(grid, now)
                if not k:
                    print(f"{grid}: no analysis file found")
                    continue
                name = k
                fd, path = tempfile.mkstemp(suffix=".grb2")
                os.close(fd)
                print(f"{grid}: downloading {k}")
                with open(path, "wb") as f:
                    f.write(http_get(f"{BUCKET}/{k}", timeout=300))
            fields, lats, lons = read_grib(path)
            got = extract(pts, fields, lats, lons)
            missing = [q for q in ("t", "ws", "wg", "h") if q not in fields]
            print(f"{grid}: {len(got)}/{len(pts)} points; missing fields: {missing or 'none'}")
            result.update(got)
            files[grid] = name
        except Exception as e:  # one grid failing shouldn't stop the other
            print(f"{grid}: FAILED {e!r}")
    if not result:
        sys.exit("no points extracted")
    body = json.dumps({"v": 1, "files": files, "points": result}).encode()
    sample = list(result.items())[:3]
    print("sample:", json.dumps(sample))
    if dry:
        print(f"DRY_RUN: would post {len(body)} bytes for {len(result)} points")
        return
    req = urllib.request.Request(INGEST_URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        print("posted:", r.status, r.read()[:200].decode(errors="replace"))


if __name__ == "__main__":
    main()
