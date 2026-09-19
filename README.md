![Vercel](https://vercelbadge.vercel.app/api/real-jiakai/running_page_24)

# Introduction

The repo stores my running site, based on [running_page](https://github.com/yihong0618/running_page).

## Strava Web sync (optional)

Upstream also supports syncing through a Strava web session when the API application is inactive. After signing in to Strava, copy the `strava_remember_token` cookie from the browser developer tools and store it in the `STRAVA_JWT` environment variable for local use:

```bash
python run_page/strava_web_sync.py "$STRAVA_JWT" --days 7
```

For GitHub Actions, set `RUN_TYPE` to `strava_web` in `.github/workflows/run_data_sync.yml`, add the `STRAVA_JWT` repository secret, and optionally set the `STRAVA_WEB_DAYS` repository variable (default: `7`). Add `--only-run` to the local command to import running activities only.
