"""Run a single Phase 4 background job once, then exit.

Intended for platform-native schedulers (Render Cron Jobs, GitHub Actions,
an external cron-as-a-service webhook) rather than an always-on process --
each invocation is a fresh, isolated run, so there's no duplicate-firing
risk even if the web service is scaled to multiple instances.

Usage:
    python run_job.py daily-digest
    python run_job.py stale-reminder
"""

import argparse
import asyncio
import logging

from app.db import init_db
from app.scheduler import check_stale_requests, send_daily_digest

logging.basicConfig(level=logging.INFO)

JOBS = {
    "daily-digest": send_daily_digest,
    "stale-reminder": check_stale_requests,
}


async def main(job_name: str) -> None:
    await init_db()
    await JOBS[job_name]()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=sorted(JOBS))
    args = parser.parse_args()
    asyncio.run(main(args.job))
