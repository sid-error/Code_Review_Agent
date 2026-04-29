"""
Tool 2: file_metrics.py
Computes basic metrics for source files: size in KB and line count.
"""

import os
from typing import Dict


def get_file_metrics(file_info: Dict) -> Dict:
    """
    Compute size_kb and line_count for a given file.

    Args:
        file_info: A dict with at least a 'path' key (from repo_scanner).

    Returns:
        The same dict enriched with 'size_kb' and 'line_count'.
    """
    path = file_info["path"]

    try:
        size_bytes = os.path.getsize(path)
        size_kb = round(size_bytes / 1024, 2)
    except OSError:
        size_kb = 0.0

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            line_count = sum(1 for _ in f)
    except OSError:
        line_count = 0

    return {
        **file_info,
        "size_kb": size_kb,
        "line_count": line_count,
    }


def get_metrics_for_all(files: list) -> list:
    """
    Apply get_file_metrics to a list of file_info dicts.

    Args:
        files: List of dicts from scan_repo().

    Returns:
        List of enriched dicts with size_kb and line_count.
    """
    return [get_file_metrics(f) for f in files]
