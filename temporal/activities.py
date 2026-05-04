"""
temporal/activities.py — Temporal activity definitions for the Code Review pipeline.

Each activity wraps one phase of the existing pipeline and writes progress
updates to a JSON sidecar file inside the scanned repository so the Streamlit
UI can poll it without needing a live connection to Temporal.

Progress file location:  <repo_path>/.code_review_progress.json

Activity execution order (orchestrated by CodeReviewWorkflow):
  1. act_scan_repo
  2. act_compute_metrics
  3. act_run_heuristics
  4. act_run_semgrep
  5. act_run_ai_analysis      (only when config.no_ai == False)
  6. act_merge_and_report
  7. act_save_cache
"""

import asyncio
import json
import os
import sys
from dataclasses import asdict
from typing import Any, Dict, List, Tuple

from temporalio import activity

# Ensure project root is on sys.path when the worker is launched from any CWD
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from temporal.models import RunConfig, ProgressUpdate


def _coerce_config(config) -> RunConfig:
    """
    Temporal's JSON converter sometimes deserializes a RunConfig dataclass
    as a plain dict instead of reconstructing the dataclass instance.
    This helper ensures we always have a proper RunConfig object.
    """
    if isinstance(config, dict):
        return RunConfig(**{k: v for k, v in config.items() if k in RunConfig.__dataclass_fields__})
    return config

# ── Progress helpers ────────────────────────────────────────────────────────

PROGRESS_FILENAME = ".code_review_progress.json"


def _progress_path(repo_path: str) -> str:
    return os.path.join(os.path.abspath(repo_path), PROGRESS_FILENAME)


def write_progress(repo_path: str, update: ProgressUpdate) -> None:
    """Atomically write a ProgressUpdate to the progress sidecar file."""
    path = _progress_path(repo_path)
    data = asdict(update)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass  # Non-fatal — UI will just show stale progress


# ── Activity 1: Scan ─────────────────────────────────────────────────────────

@activity.defn(name="act_scan_repo")
async def act_scan_repo(config: RunConfig) -> Dict:
    """
    Phase 1: Walk the repo and discover source files.
    """
    config = _coerce_config(config)
    from tools.repo_scanner import scan_repo
    from tools.cache_manager import load_cache, filter_changed_files

    write_progress(config.repo_path, ProgressUpdate(
        step=1, total_steps=_total_steps(config),
        phase="Scanning repository", status="running",
    ))

    all_files = scan_repo(config.repo_path)

    if not all_files:
        write_progress(config.repo_path, ProgressUpdate(
            step=1, total_steps=_total_steps(config),
            phase="Scanning repository", status="done",
            detail="No supported files found",
        ))
        return {"all_files": [], "changed_files": [], "unchanged_files": [], "cache": {}}

    # Always hash files so cache can be updated later, even on first run
    cache = {} if config.full_scan else load_cache(config.repo_path)
    changed_files, unchanged_files = filter_changed_files(all_files, cache)

    # Attach cache metadata so downstream activities can use it
    result = {
        "all_files": all_files,
        "changed_files": changed_files,
        "unchanged_files": unchanged_files,
        "cache": cache,
    }

    write_progress(config.repo_path, ProgressUpdate(
        step=1, total_steps=_total_steps(config),
        phase="Scanning repository", status="done",
        detail=(
            f"{len(all_files)} files found | "
            f"{len(changed_files)} changed | "
            f"{len(unchanged_files)} cached"
        ),
    ))

    # Temporal activities must return JSON-serialisable data
    return _serialise(result)


# ── Activity 2: Metrics ──────────────────────────────────────────────────────

@activity.defn(name="act_compute_metrics")
async def act_compute_metrics(config: RunConfig, scan_result: Dict) -> List[Dict]:
    """Phase 2: Compute size_kb and line_count for each changed file."""
    config = _coerce_config(config)
    from tools.file_metrics import get_metrics_for_all

    changed_files = scan_result.get("changed_files", [])

    write_progress(config.repo_path, ProgressUpdate(
        step=2, total_steps=_total_steps(config),
        phase="Computing file metrics", status="running",
        detail=f"{len(changed_files)} files",
    ))

    files_with_metrics = get_metrics_for_all(changed_files) if changed_files else []

    write_progress(config.repo_path, ProgressUpdate(
        step=2, total_steps=_total_steps(config),
        phase="Computing file metrics", status="done",
        detail=f"{len(files_with_metrics)} files measured",
    ))

    return _serialise(files_with_metrics)


# ── Activity 3: Heuristics ───────────────────────────────────────────────────

@activity.defn(name="act_run_heuristics")
async def act_run_heuristics(config: RunConfig, files_with_metrics: List[Dict]) -> List[Dict]:
    """Phase 3: Run radon + bandit + AST checks on all changed files."""
    config = _coerce_config(config)
    from tools.heuristic_analyzer import analyze_all_metrics

    write_progress(config.repo_path, ProgressUpdate(
        step=3, total_steps=_total_steps(config),
        phase="Running heuristic analysis (radon + bandit + AST)", status="running",
        detail=f"{len(files_with_metrics)} files",
    ))

    heuristic_issues = analyze_all_metrics(files_with_metrics) if files_with_metrics else []

    counts = _count_severities(heuristic_issues)
    write_progress(config.repo_path, ProgressUpdate(
        step=3, total_steps=_total_steps(config),
        phase="Running heuristic analysis (radon + bandit + AST)", status="done",
        detail=f"{len(heuristic_issues)} findings",
        severity_counts=counts,
    ))

    return _serialise(heuristic_issues)


# ── Activity 4: Semgrep ──────────────────────────────────────────────────────

@activity.defn(name="act_run_semgrep")
async def act_run_semgrep(config: RunConfig, files_with_metrics: List[Dict]) -> List[Dict]:
    """Phase 4: Run Semgrep multi-language static analysis."""
    config = _coerce_config(config)
    from tools.semgrep_analyzer import analyze_with_semgrep

    write_progress(config.repo_path, ProgressUpdate(
        step=4, total_steps=_total_steps(config),
        phase="Running Semgrep static analysis", status="running",
        detail=f"{len(files_with_metrics)} files in scope",
    ))

    # Run semgrep in a thread so the event loop stays responsive.
    # It can be slow (downloads rule packs from the internet on first run).
    semgrep_issues = await asyncio.to_thread(
        analyze_with_semgrep, config.repo_path, files_with_metrics
    ) if files_with_metrics else []

    counts = _count_severities(semgrep_issues)
    write_progress(config.repo_path, ProgressUpdate(
        step=4, total_steps=_total_steps(config),
        phase="Running Semgrep static analysis", status="done",
        detail=f"{len(semgrep_issues)} findings",
        severity_counts=counts,
    ))

    return _serialise(semgrep_issues)


# ── Activity 5: AI Analysis ──────────────────────────────────────────────────

@activity.defn(name="act_run_ai_analysis")
async def act_run_ai_analysis(
    config: RunConfig,
    files_with_metrics: List[Dict],
    pause_check_interval: int = 2,
) -> Dict:
    """
    Phase 5: Dispatch file chunks to the Gemini orchestrator agent.
    Returns: {"ai_issues": [...], "token_usage": {...}}
    """
    config = _coerce_config(config)

    if config.no_ai:
        write_progress(config.repo_path, ProgressUpdate(
            step=5, total_steps=_total_steps(config),
            phase="AI analysis", status="done",
            detail="Skipped (--no-ai mode)",
        ))
        return {"ai_issues": [], "token_usage": {}}

    from agent.runner import CodeReviewRunner
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        write_progress(config.repo_path, ProgressUpdate(
            step=5, total_steps=_total_steps(config),
            phase="AI analysis", status="done",
            detail="Skipped (GOOGLE_API_KEY not set)",
        ))
        return {"ai_issues": [], "token_usage": {}}

    if not files_with_metrics:
        write_progress(config.repo_path, ProgressUpdate(
            step=5, total_steps=_total_steps(config),
            phase="AI analysis", status="done",
            detail="No changed files to analyse",
        ))
        return {"ai_issues": [], "token_usage": {}}

    total = len(files_with_metrics)

    def _progress_cb(current: int, total_files: int, filename: str):
        # Heartbeat so Temporal knows activity is alive
        activity.heartbeat(f"Analysing {current}/{total_files}: {filename}")
        write_progress(config.repo_path, ProgressUpdate(
            step=5, total_steps=_total_steps(config),
            phase="Running AI analysis (Gemini via ADK)",
            status="running",
            detail=f"{current}/{total_files} — {os.path.basename(filename)}",
        ))

    runner = CodeReviewRunner()

    # runner.analyze_files() calls asyncio.run() internally.
    # The Temporal worker already owns an event loop, so we must run
    # analyze_files in a thread where no event loop is active.
    ai_issues, token_usage = await asyncio.to_thread(
        runner.analyze_files,
        files_with_metrics,
        _progress_cb,
    )

    counts = _count_severities(ai_issues)
    write_progress(config.repo_path, ProgressUpdate(
        step=5, total_steps=_total_steps(config),
        phase="Running AI analysis (Gemini via ADK)",
        status="done",
        detail=f"{len(ai_issues)} findings",
        severity_counts=counts,
        token_usage=token_usage,
    ))

    return _serialise({"ai_issues": ai_issues, "token_usage": token_usage})


# ── Activity 6: Merge & Report ───────────────────────────────────────────────

@activity.defn(name="act_merge_and_report")
async def act_merge_and_report(
    config: RunConfig,
    scan_result: Dict,
    heuristic_issues: List[Dict],
    semgrep_issues: List[Dict],
    ai_result: Dict,
) -> Dict:
    """Phase 6: Deduplicate all findings, generate report.html and report.json."""
    config = _coerce_config(config)
    from tools.merger import merge_findings
    from tools.cache_manager import get_report_json_path
    from report.generator import generate_report
    import json as _json

    all_files = scan_result.get("all_files", [])
    unchanged_files = scan_result.get("unchanged_files", [])
    ai_issues = ai_result.get("ai_issues", [])
    token_usage = ai_result.get("token_usage", {})

    write_progress(config.repo_path, ProgressUpdate(
        step=6, total_steps=_total_steps(config),
        phase="Merging findings and generating report", status="running",
    ))

    # Carry forward previous findings for unchanged files
    json_path = get_report_json_path(config.repo_path)
    previous_findings = []
    if unchanged_files and os.path.isfile(json_path):
        unchanged_rel = {f.get("relative_path", f.get("path", "")) for f in unchanged_files}
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                data = _json.load(fh)
            previous_findings = [f for f in data.get("findings", []) if f.get("file", "") in unchanged_rel]
        except (OSError, _json.JSONDecodeError):
            pass

    # Merge: heuristic + semgrep → AI on top → carry-forward
    new_findings = merge_findings(merge_findings(heuristic_issues, semgrep_issues), ai_issues)
    merged = merge_findings(new_findings, previous_findings)

    incremental_info = {
        "full_scan": config.full_scan or not scan_result.get("cache"),
        "total_files_in_repo": len(all_files),
        "files_scanned_this_run": len(scan_result.get("changed_files", [])),
        "files_skipped_from_cache": len(unchanged_files),
        "findings_carried_from_cache": len(previous_findings),
    }

    os.makedirs(config.output_dir, exist_ok=True)

    paths = generate_report(
        findings=merged,
        repo_path=config.repo_path,
        total_files=len(all_files),
        output_dir=config.output_dir,
        token_usage=token_usage if token_usage else None,
        incremental_info=incremental_info,
        json_path_override=json_path,
    )

    counts = _count_severities(merged)
    write_progress(config.repo_path, ProgressUpdate(
        step=6, total_steps=_total_steps(config),
        phase="Merging findings and generating report", status="done",
        detail=f"{len(merged)} unique findings",
        findings_count=len(merged),
        severity_counts=counts,
        token_usage=token_usage,
    ))

    return _serialise({"paths": paths, "findings_count": len(merged), "severity_counts": counts})


# ── Activity 7: Save Cache ───────────────────────────────────────────────────

@activity.defn(name="act_save_cache")
async def act_save_cache(config: RunConfig, scan_result: Dict) -> None:
    """Phase 7: Persist the updated file-hash cache."""
    config = _coerce_config(config)
    from tools.cache_manager import build_updated_cache, save_cache

    cache = scan_result.get("cache", {})
    changed_files = scan_result.get("changed_files", [])
    unchanged_files = scan_result.get("unchanged_files", [])

    updated_cache = build_updated_cache(cache, changed_files, unchanged_files)
    save_cache(updated_cache, config.repo_path)

    write_progress(config.repo_path, ProgressUpdate(
        step=7, total_steps=_total_steps(config),
        phase="Saving cache", status="done",
        detail=f"{len(updated_cache)} entries cached",
    ))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _total_steps(config: RunConfig) -> int:
    """Total pipeline steps. Always 7 (AI step is skipped internally if no_ai)."""
    return 7


def _count_severities(issues: List[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for iss in issues:
        s = iss.get("severity", "info").lower()
        counts[s] = counts.get(s, 0) + 1
    return counts


def _serialise(obj: Any) -> Any:
    """Ensure the object is JSON-serialisable (converts dataclass instances)."""
    return json.loads(json.dumps(obj, default=str))
