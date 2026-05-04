"""
tools/cache_manager.py -- File-hash cache for incremental scanning.

Stores a SHA-256 hash of each scanned source file in a hidden JSON file
inside the target repository. On subsequent runs, only files whose hash has
changed (or are brand-new) will be analysed; the rest are skipped and their
findings are carried forward from the previous report.
"""

import hashlib
import json
import os
from typing import Dict, List, Tuple

# Name of the cache file placed at the root of the scanned repository.
CACHE_FILENAME = ".code_review_cache.json"

# Directory (inside the repo) where the report JSON is stored.
REPORT_DIR_NAME = ".code_review_reports"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def compute_file_hash(path: str) -> str:
    """Return the SHA-256 hex digest of the file at *path*.

    Falls back to an empty string on read errors so callers treat the file
    as changed (safe default).
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(65_536), b""):
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def get_cache_path(repo_path: str) -> str:
    """Return the absolute path to the cache file for the given repo."""
    return os.path.join(os.path.abspath(repo_path), CACHE_FILENAME)


def get_report_json_path(repo_path: str) -> str:
    """Return the absolute path to report.json stored inside the scanned repo.

    The file is placed at:  <repo>/.code_review_reports/report.json
    This keeps it co-located with the cache and out of the repo root clutter.
    The directory is created automatically if it does not exist.
    """
    report_dir = os.path.join(os.path.abspath(repo_path), REPORT_DIR_NAME)
    os.makedirs(report_dir, exist_ok=True)
    return os.path.join(report_dir, "report.json")


def load_cache(repo_path: str) -> Dict[str, str]:
    """Load the cache from *repo_path*/.code_review_cache.json.

    Returns:
        A dict mapping relative_path -> sha256_hex_digest.
        Returns {} if the cache file does not exist or is malformed.
    """
    cache_path = get_cache_path(repo_path)
    if not os.path.isfile(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_cache(cache: Dict[str, str], repo_path: str) -> None:
    """Persist *cache* to disk at *repo_path*/.code_review_cache.json."""
    cache_path = get_cache_path(repo_path)
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        # Non-fatal: just warn; the next run will re-scan everything.
        print(f"  [cache] WARNING: could not write cache: {exc}")


# ---------------------------------------------------------------------------
# Change Detection
# ---------------------------------------------------------------------------

def filter_changed_files(
    files: List[Dict],
    cache: Dict[str, str],
) -> Tuple[List[Dict], List[Dict]]:
    """Split *files* into changed and unchanged lists based on content hashes.

    Args:
        files:  Full list of file dicts produced by scan_repo().
        cache:  Previously persisted hash map (relative_path -> sha256).

    Returns:
        (changed_files, unchanged_files) — both are lists of the same dicts
        enriched with a ``"current_hash"`` key for later cache updating.
    """
    changed: List[Dict] = []
    unchanged: List[Dict] = []

    for file_info in files:
        rel = file_info.get("relative_path", file_info.get("path", ""))
        current_hash = compute_file_hash(file_info["path"])
        file_info = {**file_info, "current_hash": current_hash}

        if cache.get(rel) == current_hash and current_hash != "":
            unchanged.append(file_info)
        else:
            changed.append(file_info)

    return changed, unchanged


def build_updated_cache(
    cache: Dict[str, str],
    changed_files: List[Dict],
    unchanged_files: List[Dict],
) -> Dict[str, str]:
    """Return a new cache dict merging old entries with fresh hashes.

    Args:
        cache:          The cache dict loaded at the start of the run.
        changed_files:  Files that were re-analysed this run (have current_hash).
        unchanged_files: Files that were skipped (have current_hash too).

    Returns:
        Updated cache ready to be saved to disk.
    """
    updated = dict(cache)
    for file_info in (*changed_files, *unchanged_files):
        rel = file_info.get("relative_path", file_info.get("path", ""))
        h = file_info.get("current_hash", "")
        if rel and h:
            updated[rel] = h
    return updated
