import sys, os, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import extract_rtma as x
def fake(url, timeout=60):
    if "prefix=rtma2p5.20260122/" in url:
        keys = ["rtma2p5.t17z.2dvaranl_ndfd.grb2_wexp", "rtma2p5.t18z.2dvaranl_ndfd.grb2", "rtma2p5.t18z.2dvaranl_ndfd.grb2_wexp",
                "rtma2p5.t18z.2dvaranl_ndfd.grb2_wexp.idx", "rtma2p5.t18z.2dvarerr_ndfd.grb2"]
    elif "prefix=akrtma.20260121/" in url:
        keys = ["akrtma.t23z.2dvaranl_ndfd_3p0.grb2"]
    else:
        keys = []
    pre = url.split("prefix=")[1].split("&")[0]
    body = "".join(f"<Contents><Key>{pre}{k}</Key></Contents>" for k in keys)
    return f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{body}</ListBucketResult>'.encode()
x.http_get = fake
now = dt.datetime(2026, 1, 22, 19, 40, tzinfo=dt.timezone.utc)
assert x.newest_file("conus", now) == "rtma2p5.20260122/rtma2p5.t18z.2dvaranl_ndfd.grb2_wexp", x.newest_file("conus", now)
assert x.newest_file("ak", now) == "akrtma.20260121/akrtma.t23z.2dvaranl_ndfd_3p0.grb2"
print("OK")
