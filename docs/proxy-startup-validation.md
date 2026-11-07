# Authenticated proxy startup on Raspberry Pi

## Problem and change

On the Pi's Chromium 156, authenticated requests intermittently timed out at
120 seconds before FlareSolverr logged navigation to the requested URL. Failed
browsers were observed on the default search/new-tab page. The custom Network
CDP calls ran before `get_webdriver()` returned, so exceptions there also escaped
the caller's browser cleanup.

The fix starts Chromium on `about:blank`, combines the feature switches into one
argument, retains the unpacked authentication extension until browser shutdown,
and cleans up both partial construction and post-construction failures.

Before target navigation, the proxy worker is connected through the local
DevTools endpoint, resumed if waiting for a debugger, and checked for the
requested proxy settings and a registered authentication listener. Missing or
incorrect settings cause failure and cleanup rather than a direct request.
The readiness check has a 15-second budget. Username and password are redacted
from incoming-request logs.

The startup changes are a combined repair, not proof of a single Chromium defect.
An initial test with blank-page startup but without the readiness check returned
the direct IP; this is why HTTP success alone is not treated as proxy success.

## Pi validation — 2026-11-07, America/Toronto

- Image: `flaresolverr:proxy-startup-fix-20261107`.
- Container: `flaresolverr-proxy-smoke-20261107`, bound to `127.0.0.1:8193`.
- Chromium / ChromeDriver: 156.0.8078.79; existing dependency layers reused.
- The existing business extension was mounted read-only; the same blocked-domain
  settings and 2 GiB shared-memory setting were used.
- Scheduler stopped and automatic startup disabled throughout testing.
- No DriveTest requests were sent by these tests.

| Test | Result |
| --- | --- |
| Eight regression tests | Passed: extension lifetime, both CDP failure points, partial construction, quit failure, proxy-readiness failure, and rejection of invalid proxy state |
| Initial direct baseline | Canada, 4.793 s |
| Three fresh French proxy sessions × two URLs | 6/6 passed, 5.719–19.835 s |
| Restarted container, direct baseline | Canada, 4.755 s |
| One fresh French proxy session × two URLs after restart | 2/2 passed, 6.820–8.478 s |
| Injected `Network.enable` failure with a real Chromium process | Browser exited and temporary proxy extension directory removed |

The URLs were `https://ipinfo.io/json` and
`https://www.cloudflare.com/cdn-cgi/trace`. Every proxied response reported France
and an IP different from the direct baseline. Each provider session returned the
same IP across the two query services. After the first six requests there were no
remaining Chromium / ChromeDriver processes or temporary proxy worker files.

This is a small sequential smoke test, not a long-running/concurrency benchmark
or a guarantee of passing DriveTest's challenge pages. The original container on
port 8191 was not replaced by this validation.

## Repeat without credentials in the repository

With the isolated container running on port 8193:

```sh
python3 scripts/proxy_smoke_test.py \
  --env-file /home/wa007/code/drive-test-assistant/.env \
  --endpoint http://127.0.0.1:8193/v1 \
  --country fr --rounds 3
```

The script reads `PROXY_URL`, `PROXY_USERNAME`, and `PROXY_PASSWORD_PREFIX`
locally, generates a fresh provider session for each round, and stops at the first
failure. It prints IP, country, elapsed time and verification result, never proxy
credentials. It does not invoke the scheduler or change its state.

Run the regression tests using the built image:

```sh
docker run --rm -e PYTHONPATH=/app \
  -v "$PWD/tests:/tests:ro" \
  flaresolverr:proxy-startup-fix-20261107 \
  python -m unittest discover -s /tests -v
```
