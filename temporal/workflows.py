"""
temporal/workflows.py — Temporal workflow definition for the Code Review pipeline.

CodeReviewWorkflow orchestrates all pipeline activities in order and supports
a pause/resume mechanism controlled by Temporal signals. The Streamlit UI sends
signals via the Temporal client; the workflow checks the paused flag before
dispatching the AI analysis activity.

Signals:
    pause()   — suspends execution before/after the AI analysis step
    resume()  — resumes a paused workflow

Queries:
    get_status()  — returns the current workflow status string

The AI analysis activity (act_run_ai_analysis) is the only step that benefits
from pause/resume because it is the only long-running, expensive step. All
other activities are fast (< 30 s) and run uninterrupted.

Crash recovery: If the worker process is killed mid-run, Temporal will
automatically replay the workflow from the last successfully completed activity
when the worker restarts. The file-hash cache ensures already-analysed files
are not re-sent to the AI.
"""

from datetime import timedelta
from typing import Dict, List

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import the activity stubs via the sandboxed-import mechanism
with workflow.unsafe.imports_passed_through():
    from temporal.activities import (
        act_scan_repo,
        act_compute_metrics,
        act_run_heuristics,
        act_run_semgrep,
        act_run_ai_analysis,
        act_merge_and_report,
        act_save_cache,
    )
    from temporal.models import RunConfig


# Shared retry policy — don't retry on user-facing failures (bad API key etc.)
_RETRY = RetryPolicy(maximum_attempts=2, backoff_coefficient=2.0)

# Long timeout for AI analysis (up to 2 hours for large repos)
_AI_TIMEOUT = timedelta(hours=2)

# Short timeout for fast activities
_FAST_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="CodeReviewWorkflow")
class CodeReviewWorkflow:
    """End-to-end code review pipeline as a Temporal durable workflow."""

    def __init__(self):
        self._paused = False
        self._status = "starting"

    # ── Signals ──────────────────────────────────────────────────────────────

    @workflow.signal
    async def pause(self) -> None:
        """Signal: pause before the next pausable checkpoint."""
        self._paused = True
        self._status = "paused"

    @workflow.signal
    async def resume(self) -> None:
        """Signal: resume a paused workflow."""
        self._paused = False
        self._status = "running"

    # ── Queries ──────────────────────────────────────────────────────────────

    @workflow.query
    def get_status(self) -> str:
        """Query: return the current workflow status."""
        return self._status

    # ── Main run ─────────────────────────────────────────────────────────────

    @workflow.run
    async def run(self, config: RunConfig) -> Dict:
        """
        Execute the full code review pipeline.

        Returns a summary dict:
            {
                "findings_count": int,
                "severity_counts": dict,
                "paths": {"html": ..., "json": ...},
                "token_usage": dict,
            }
        """
        self._status = "running"

        # ── Step 1: Scan ─────────────────────────────────────────────────────
        scan_result = await workflow.execute_activity(
            act_scan_repo,
            config,
            start_to_close_timeout=_FAST_TIMEOUT,
            retry_policy=_RETRY,
        )

        if not scan_result.get("all_files"):
            self._status = "done"
            return {"findings_count": 0, "severity_counts": {}, "paths": {}, "token_usage": {}}

        # ── Step 2: Metrics ──────────────────────────────────────────────────
        files_with_metrics: List[Dict] = await workflow.execute_activity(
            act_compute_metrics,
            args=[config, scan_result],
            start_to_close_timeout=_FAST_TIMEOUT,
            retry_policy=_RETRY,
        )

        # ── Step 3: Heuristics ───────────────────────────────────────────────
        heuristic_issues: List[Dict] = await workflow.execute_activity(
            act_run_heuristics,
            args=[config, files_with_metrics],
            start_to_close_timeout=_FAST_TIMEOUT,
            retry_policy=_RETRY,
        )

        # ── Step 4: Semgrep ──────────────────────────────────────────────────
        semgrep_issues: List[Dict] = await workflow.execute_activity(
            act_run_semgrep,
            args=[config, files_with_metrics],
            start_to_close_timeout=_FAST_TIMEOUT,
            retry_policy=_RETRY,
        )

        # ── Pause checkpoint: before expensive AI step ────────────────────────
        # Wait here until resume() signal arrives (if paused)
        if self._paused:
            await workflow.wait_condition(lambda: not self._paused)

        # ── Step 5: AI Analysis ──────────────────────────────────────────────
        ai_result: Dict = await workflow.execute_activity(
            act_run_ai_analysis,
            args=[config, files_with_metrics],
            start_to_close_timeout=_AI_TIMEOUT,
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )

        # ── Pause checkpoint: after AI, before report (for inspection) ────────
        if self._paused:
            await workflow.wait_condition(lambda: not self._paused)

        # ── Step 6: Merge & Report ───────────────────────────────────────────
        report_result: Dict = await workflow.execute_activity(
            act_merge_and_report,
            args=[config, scan_result, heuristic_issues, semgrep_issues, ai_result],
            start_to_close_timeout=_FAST_TIMEOUT,
            retry_policy=_RETRY,
        )

        # ── Step 7: Save Cache ───────────────────────────────────────────────
        await workflow.execute_activity(
            act_save_cache,
            args=[config, scan_result],
            start_to_close_timeout=_FAST_TIMEOUT,
            retry_policy=_RETRY,
        )

        self._status = "done"

        return {
            "findings_count": report_result.get("findings_count", 0),
            "severity_counts": report_result.get("severity_counts", {}),
            "paths": report_result.get("paths", {}),
            "token_usage": ai_result.get("token_usage", {}),
        }
