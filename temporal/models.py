"""
temporal/models.py — Shared dataclasses used by workflow, activities, and the Streamlit UI.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RunConfig:
    """
    All parameters needed to start a code review run.
    Passed from Streamlit → Temporal workflow → each activity.
    """
    repo_path: str
    output_dir: str = "."
    model: str = "gemini-2.5-pro"
    no_ai: bool = False
    full_scan: bool = False


@dataclass
class ProgressUpdate:
    """
    Written to the progress JSON file by each activity so the Streamlit UI can poll it.
    """
    step: int           # Current step number (1-based)
    total_steps: int    # Total expected steps
    phase: str          # Human-readable phase name, e.g. "Running heuristic analysis"
    status: str         # "running" | "done" | "paused" | "error"
    detail: str = ""    # Extra info, e.g. "12/45 files"
    error: str = ""     # Error message if status == "error"

    # Cumulative findings counts (populated after merge step)
    findings_count: int = 0
    severity_counts: dict = field(default_factory=dict)

    # Token usage (populated after AI step)
    token_usage: dict = field(default_factory=dict)
