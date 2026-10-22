import math, sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import extract_rtma as x

# fake 2.5 km-ish grid over Utah, lons in 0..360 like GRIB often gives
la, lo = np.meshgrid(np.arange(36.0, 43.0, 0.0225), np.arange(-115.0, -108.0, 0.03), indexing="ij")
lats, lons = la.ravel(), (lo.ravel() + 360)
n = lats.size
valid = 1768762800
t = 263.15 + 0 * lats                     # -10 C everywhere = 14 F
t[0] = np.nan
fields = {
    "t": (t, valid),
    "u": (np.full(n, 5.0), valid),        # wind from the west...
    "v": (np.full(n, 0.0), valid),        # u=+5 -> blowing toward east -> from 270
    "wg": (np.full(n, 10.0), valid),
    "h": (np.full(n, 2500.0), valid),
}
pts = [{"id": "salt-lake", "lat": 40.567, "lon": -111.633, "ft": 11068},
       {"id": "outside", "lat": 47.0, "lon": -121.0, "ft": 5000}]
out = x.extract(pts, fields, lats, lons)
print(out)
p = out["salt-lake"]
assert "outside" not in out
assert abs(p["t_f"] - 14.0) < 0.05, p
assert abs(p["ws_mph"] - 11.2) < 0.1, p
assert abs(p["wd"] - 270) < 0.1, p
assert abs(p["wg_mph"] - 22.4) < 0.1, p
assert abs(p["cell_ft"] - 8202.1) < 0.2, p
assert p["valid"] == valid
# nearest index really is nearest
i = x.nearest_index(lats, lons, 40.567, -111.633)
assert abs(lats[i] - 40.567) < 0.012 and abs(lons[i] - 360 - (-111.633)) < 0.016
# north wind: u=0, v=-5 (blowing south) -> from 0/360
f2 = dict(fields, u=(np.zeros(n), valid), v=(np.full(n, -5.0), valid))
assert x.extract(pts[:1], f2, lats, lons)["salt-lake"]["wd"] in (0.0, 360.0)
# file-name patterns
assert x.GRIDS["conus"][1].search("rtma2p5.t18z.2dvaranl_ndfd.grb2_wexp")
assert x.GRIDS["conus"][1].search("rtma2p5.t18z.2dvaranl_ndfd.grb2")
assert not x.GRIDS["conus"][1].search("rtma2p5.t18z.2dvaranl_ndfd.grb2_wexp.idx")
assert x.GRIDS["ak"][1].search("akrtma.t06z.2dvaranl_ndfd_3p0.grb2")
print("OK")
