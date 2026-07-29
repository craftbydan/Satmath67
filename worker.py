"""Always-on background worker: runs the Phase 4 scheduled jobs forever.

Use this on platforms without a native cron-job service type (Railway,
Fly.io, a bare VPS/Docker host) by deploying it as a second process
alongside the web service. On Render, prefer the native Cron Job services
in render.yaml (which call run_job.py) instead -- they're cheaper (no 24/7
dyno) and can't double-fire across replicas the way this can if
accidentally started more than once.
"""

import asyncio
import logging
import signal

from app.db import init_db
from app.scheduler import shutdown_scheduler, start_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pj.worker")


async def main() -> None:
    await init_db()
    start_scheduler()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info("Worker running; waiting for jobs / shutdown signal")
    await stop.wait()

    shutdown_scheduler()
    logger.info("Worker shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
