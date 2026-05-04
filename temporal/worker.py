"""
temporal/worker.py — Temporal worker process for the Code Review Agent.

Run this script once (in a terminal or background process) to register the
CodeReviewWorkflow and all activities with the Temporal task queue.

Usage:
    python temporal/worker.py

The worker connects to the Temporal dev server at localhost:7233.
It will keep running until interrupted (Ctrl+C).
"""

import asyncio
import logging
import os
import sys

# Ensure project root is on sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from temporalio.worker import Worker

from temporal.client import get_client, TASK_QUEUE
from temporal.workflows import CodeReviewWorkflow
from temporal.activities import (
    act_scan_repo,
    act_compute_metrics,
    act_run_heuristics,
    act_run_semgrep,
    act_run_ai_analysis,
    act_merge_and_report,
    act_save_cache,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


async def main():
    log.info("Connecting to Temporal server…")
    client = await get_client()
    log.info("Connected. Starting worker on task queue '%s'…", TASK_QUEUE)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CodeReviewWorkflow],
        activities=[
            act_scan_repo,
            act_compute_metrics,
            act_run_heuristics,
            act_run_semgrep,
            act_run_ai_analysis,
            act_merge_and_report,
            act_save_cache,
        ],
    )

    log.info("Worker running. Press Ctrl+C to stop.")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
