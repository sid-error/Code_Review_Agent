"""
Tool 5: merger.py
Deduplicates and merges findings from heuristic and AI analysis.
"""

from typing import List, Dict


def merge_findings(heuristic_issues: List[Dict], ai_issues: List[Dict]) -> List[Dict]:
    """
    Merge heuristic and AI findings, deduplicating by (file, type) key.
    AI findings take precedence (richer descriptions) over heuristic ones.

    Args:
        heuristic_issues: List of issues from heuristic_analyzer.
        ai_issues: List of issues from llm_analyzer / agents.

    Returns:
        Deduplicated, sorted list of all findings.
    """
    seen = {}  # key -> issue

    # Add heuristic findings first
    for issue in heuristic_issues:
        key = _make_key(issue)
        if key not in seen:
            seen[key] = issue

    # Add AI findings — override if same key (AI has richer descriptions)
    for issue in ai_issues:
        key = _make_key(issue)
        seen[key] = issue  # Always prefer AI finding for the same slot

    merged = list(seen.values())

    # Sort by severity: high → medium → low → info
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    merged.sort(key=lambda x: (severity_order.get(x.get("severity", "info"), 99),
                                x.get("file", "")))

    return merged


def _make_key(issue: Dict) -> str:
    """Create a deduplication key from file + type + approximate line."""
    file_key = issue.get("file", "")
    type_key = issue.get("type", "")
    # Round line numbers to nearest 10 to group nearby same-type issues
    line = issue.get("line") or 0
    line_bucket = (line // 10) * 10
    return f"{file_key}::{type_key}::{line_bucket}"
