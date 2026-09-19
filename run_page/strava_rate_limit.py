"""Wait for Strava's overall and read request quotas before the next call."""

import time


def _quota_pair(value):
    if not isinstance(value, str):
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        pair = tuple(int(part.strip()) for part in parts)
    except ValueError:
        return None
    return pair if all(number >= 0 for number in pair) else None


def wait_for_strava_quota(headers):
    """Use response headers to pause until every exhausted quota resets.

    Strava resets short quotas on UTC quarter hours and daily quotas at
    midnight UTC. Stravalib calls this callback after each API response.
    """
    if not headers:
        return
    headers = {key.lower(): value for key, value in headers.items()}
    exhausted_periods = set()
    for prefix in ("x-ratelimit", "x-readratelimit"):
        limits = _quota_pair(headers.get(f"{prefix}-limit"))
        usage = _quota_pair(headers.get(f"{prefix}-usage"))
        if limits is None or usage is None:
            continue
        for limit, used, period in zip(limits, usage, (15 * 60, 24 * 60 * 60)):
            if used >= limit:
                exhausted_periods.add(period)

    if not exhausted_periods:
        return

    now = time.time()
    resume_at = max((now // period + 1) * period + 1 for period in exhausted_periods)
    remaining = resume_at - now
    print(f"Strava API quota reached; waiting {remaining:.1f} seconds.", flush=True)
    while remaining > 0:
        time.sleep(min(remaining, 60))
        remaining = resume_at - time.time()
