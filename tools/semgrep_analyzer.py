"""
Tool 4 (new): semgrep_analyzer.py
Multi-language static analysis using Semgrep.

Runs `semgrep scan --json --config <ruleset>` against the repository root
and maps Semgrep's JSON output into the standard issue-dict schema used
throughout this pipeline:

    {
        "source":   "semgrep",
        "type":     <check_id>,
        "message":  <human-readable finding>,
        "severity": "critical" | "high" | "medium" | "low" | "info",
        "file":     <relative path>,
        "line":     <int or None>,
        "evidence": <matched code snippet>,
    }

Ruleset strategy (all free, no login required):
    - p/python       → Python files
    - p/javascript   → JS and TypeScript files
    - p/java         → Java files
    - p/golang       → Go files
    - p/default      → catch-all for Ruby, C#, PHP, Kotlin, Rust, Shell

Windows encoding note:
    Semgrep's remote rulesets contain non-ASCII Unicode characters that
    Windows cp1252 cannot encode. We set PYTHONUTF8=1 in the subprocess
    environment to force Python's UTF-8 mode inside the semgrep process.

Graceful degradation:
    - If Semgrep is not installed → returns [] with a warning printed.
    - If the scan times out       → returns [] with a warning printed.
    - If JSON decode fails        → returns [] silently.
    - If a per-language scan fails→ skips that language, continues others.
"""

import json
import os
import subprocess
from collections import defaultdict
from typing import Dict, List, Optional


# Map Semgrep severity labels to the pipeline's unified severity scale.
_SEVERITY_MAP: Dict[str, str] = {
    "ERROR":   "high",
    "WARNING": "medium",
    "INFO":    "low",
}

# Timeout per Semgrep invocation (seconds).
_TIMEOUT = 120

# Map language → freely-available Semgrep registry ruleset (no login needed).
_LANG_TO_RULESET: Dict[str, str] = {
    "python":     "p/python",
    "javascript": "p/javascript",
    "typescript": "p/javascript",  # JS ruleset covers TS
    "java":       "p/java",
    "go":         "p/golang",
    # Everything else uses the generic default pack
}
_DEFAULT_RULESET = "p/default"


def _build_env() -> Dict[str, str]:
    """Return a subprocess env dict with PYTHONUTF8=1 to avoid cp1252 errors."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return env


def _make_issue(
    check_id: str,
    message: str,
    severity: str,
    rel_path: str,
    line: Optional[int],
    evidence: str,
) -> Dict:
    return {
        "source":   "semgrep",
        "type":     check_id,
        "message":  message,
        "severity": severity,
        "file":     rel_path,
        "line":     line,
        "evidence": evidence,
    }


def _run_semgrep(config: str, repo_path: str) -> List[Dict]:
    """
    Run a single semgrep invocation and return parsed results list.
    Returns empty list on any error.
    """
    try:
        result = subprocess.run(
            [
                "semgrep",
                "scan",
                "--json",
                "--config", config,
                "--quiet",
                "--no-rewrite-rule-ids",
                str(repo_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            cwd=repo_path,
            env=_build_env(),
        )
    except FileNotFoundError:
        print(
            "  [semgrep] WARNING: 'semgrep' not found on PATH — skipping. "
            "Install with: pip install semgrep"
        )
        return []
    except subprocess.TimeoutExpired:
        print(
            f"  [semgrep] WARNING: scan with config '{config}' timed out "
            f"after {_TIMEOUT}s — skipping."
        )
        return []

    raw = result.stdout.strip()
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    return data.get("results", [])


def _parse_findings(
    semgrep_results: List[Dict],
    repo_path: str,
    in_scope: set,
) -> List[Dict]:
    """Convert raw Semgrep result dicts into pipeline issue dicts."""
    issues: List[Dict] = []
    repo_path_norm = os.path.normpath(repo_path)

    for finding in semgrep_results:
        raw_path: str = finding.get("path", "")

        # Semgrep may return absolute paths on Windows; normalise to relative.
        abs_candidate = os.path.normpath(raw_path)
        if os.path.isabs(abs_candidate):
            try:
                rel_path = os.path.relpath(abs_candidate, repo_path_norm)
            except ValueError:
                rel_path = raw_path
        else:
            rel_path = raw_path

        rel_path = rel_path.replace("\\", "/")

        # Only report findings for files in scope this run.
        if in_scope and rel_path not in in_scope:
            continue

        check_id: str = finding.get("check_id", "unknown")
        extra: Dict = finding.get("extra", {})
        message: str = extra.get("message", check_id).strip()

        semgrep_sev: str = extra.get("severity", "INFO").upper()
        severity: str = _SEVERITY_MAP.get(semgrep_sev, "info")

        start = finding.get("start", {})
        line: Optional[int] = start.get("line")

        lines_obj = extra.get("lines", "")
        evidence = lines_obj.split("\n")[0].strip() if lines_obj else ""

        # Shorten verbose rule IDs: keep only the terminal segment.
        short_id = check_id.split(".")[-1] if "." in check_id else check_id

        issues.append(
            _make_issue(
                check_id=f"semgrep/{short_id}",
                message=message,
                severity=severity,
                rel_path=rel_path,
                line=line,
                evidence=evidence,
            )
        )

    return issues


def analyze_with_semgrep(repo_path: str, files: List[Dict]) -> List[Dict]:
    """
    Run Semgrep against the repository and return a list of findings.

    Semgrep is invoked once per language group (more focused + avoids
    downloading unnecessary rule packs). Results are filtered to only
    files present in *files* (important for incremental scans).

    Args:
        repo_path:  Absolute path to the repository root.
        files:      List of enriched file-info dicts (from get_metrics_for_all).

    Returns:
        List of issue dicts in the standard schema; empty list on any error.
    """
    if not files:
        return []

    in_scope = {f.get("relative_path", "") for f in files}

    # Group in-scope files by their Semgrep ruleset.
    ruleset_to_langs: Dict[str, set] = defaultdict(set)
    for f in files:
        lang = f.get("language", "")
        ruleset = _LANG_TO_RULESET.get(lang, _DEFAULT_RULESET)
        ruleset_to_langs[ruleset].add(lang)

    all_issues: List[Dict] = []
    seen_raw_ids: set = set()  # deduplicate across ruleset runs

    for ruleset, langs in ruleset_to_langs.items():
        raw_results = _run_semgrep(ruleset, repo_path)
        findings = _parse_findings(raw_results, repo_path, in_scope)

        for issue in findings:
            # Deduplicate by (file, type, line) across ruleset runs
            key = f"{issue['file']}::{issue['type']}::{issue.get('line')}"
            if key not in seen_raw_ids:
                seen_raw_ids.add(key)
                all_issues.append(issue)

    return all_issues
