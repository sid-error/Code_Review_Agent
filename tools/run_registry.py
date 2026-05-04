"""
tools/run_registry.py — Persistent run history for the Streamlit UI.

Stores a JSON registry of all code-review workflow runs so the UI can
display history, resume paused runs, and re-run past configurations
even after the browser is closed and reopened.

File location: {project_root}/.code_review_runs.json
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_FILE = os.path.join(_PROJECT_ROOT, ".code_review_runs.json")


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def load_runs() -> List[Dict]:
    """Load all run entries (most recent first)."""
    if not os.path.isfile(RUNS_FILE):
        return []
    try:
        with open(RUNS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("runs", [])
    except Exception:
        return []


def save_runs(runs: List[Dict]) -> None:
    """Atomically write the runs list to disk."""
    try:
        tmp = RUNS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"runs": runs}, f, indent=2, ensure_ascii=False)
        os.replace(tmp, RUNS_FILE)
    except Exception:
        pass


def add_run(workflow_id: str, repo_path: str, output_dir: str,
            model: str, no_ai: bool, full_scan: bool) -> None:
    """Insert a new run entry at the front of the list."""
    abs_output = os.path.abspath(output_dir)
    runs = load_runs()
    runs.insert(0, {
        "workflow_id": workflow_id,
        "repo_path": repo_path,
        "repo_name": os.path.basename(repo_path),
        "output_dir": abs_output,
        "model": model,
        "no_ai": no_ai,
        "full_scan": full_scan,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "status": "running",          # running | paused | done | failed | cancelled
        "html_path": os.path.join(abs_output, "report.html"),
        "findings_count": 0,
        "severity_counts": {},
    })
    save_runs(runs)


def update_run(workflow_id: str, **kwargs) -> None:
    """Update fields on an existing run entry."""
    runs = load_runs()
    for run in runs:
        if run["workflow_id"] == workflow_id:
            run.update(kwargs)
            break
    save_runs(runs)


def delete_run(workflow_id: str) -> None:
    """Remove a run entry from the registry."""
    save_runs([r for r in load_runs() if r["workflow_id"] != workflow_id])


def get_run(workflow_id: str) -> Optional[Dict]:
    """Return a single run entry or None."""
    for r in load_runs():
        if r["workflow_id"] == workflow_id:
            return r
    return None
