# powdash-rtma

Hourly job for PowDash. It reads NOAA's Real-Time Mesoscale Analysis (RTMA, an
hourly analysis of actual surface temperature and wind on a 2.5 km grid) at
each zone's peak, and sends the values to the PowDash server, which shows them
as the "<Peak> (est.)" station (temperature corrected to the peak's height).

- Data: NOAA Open Data on AWS, bucket `noaa-rtma-pds` (public domain, no key).
- Points: `GET https://api.powdash.co/v1/rtma-points.json` (from the server's zone list).
- Sends: `POST https://api.powdash.co/admin/rtma`, `Authorization: Bearer <RTMA_KEY>`.

## Setup (once)
1. Make a GitHub repository from this folder. Public is fine (no secrets in it) and gets
   unlimited Actions minutes. A private one uses about 1 to 2 minutes of the 2,000 free minutes per run.
2. Make a key: `openssl rand -hex 24`.
3. In the repository: Settings > Secrets and variables > Actions > New repository secret.
   Name `RTMA_KEY`, value the key.
4. On the server: `cd powdash-server && npx wrangler secret put RTMA_KEY` (same key), then deploy.
5. Actions tab > rtma > Run workflow. The log shows the fields read and how many points were sent.

## Local test without sending
```
pip install -r requirements.txt
DRY_RUN=1 python extract_rtma.py
python test/test_extract.py && python test/test_listing.py
```
