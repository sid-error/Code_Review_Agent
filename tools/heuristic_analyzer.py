"""
Tool 3: heuristic_analyzer.py
Rule-based static analysis using radon (complexity) and bandit (security).
No LLM calls — fast, deterministic, always runs first.
"""

import ast
import subprocess
import json
import os
from typing import List, Dict

# Thresholds
LARGE_FILE_LINES = 500
LARGE_FILE_KB = 200
HIGH_COMPLEXITY_THRESHOLD = 10  # McCabe cyclomatic complexity


def _make_issue(
    issue_type: str,
    message: str,
    severity: str,
    file_info: Dict,
    line: int = None,
    evidence: str = None,
) -> Dict:
    return {
        "source": "heuristic",
        "type": issue_type,
        "message": message,
        "severity": severity,
        "file": file_info.get("relative_path", file_info.get("path")),
        "line": line,
        "evidence": evidence or "",
    }


def _check_size_metrics(file_info: Dict) -> List[Dict]:
    """Flag files that are too large."""
    issues = []
    lc = file_info.get("line_count", 0)
    kb = file_info.get("size_kb", 0)

    if lc > LARGE_FILE_LINES:
        issues.append(_make_issue(
            "large_file",
            f"File has {lc} lines (threshold: {LARGE_FILE_LINES}). "
            "Consider splitting into smaller modules.",
            "high",
            file_info,
            evidence=f"{lc} lines",
        ))

    if kb > LARGE_FILE_KB:
        issues.append(_make_issue(
            "large_size",
            f"File is {kb} KB (threshold: {LARGE_FILE_KB} KB). "
            "Large files increase load times and reduce maintainability.",
            "medium",
            file_info,
            evidence=f"{kb} KB",
        ))

    return issues


def _check_python_complexity(file_info: Dict) -> List[Dict]:
    """Use radon to check cyclomatic complexity for Python files."""
    if file_info.get("language") != "python":
        return []

    issues = []
    path = file_info["path"]

    try:
        result = subprocess.run(
            ["python", "-m", "radon", "cc", path, "--json", "-a"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []

        data = json.loads(result.stdout)
        for fpath, blocks in data.items():
            for block in blocks:
                complexity = block.get("complexity", 0)
                if complexity > HIGH_COMPLEXITY_THRESHOLD:
                    name = block.get("name", "unknown")
                    lineno = block.get("lineno", None)
                    issues.append(_make_issue(
                        "high_complexity",
                        f"Function/method '{name}' has cyclomatic complexity {complexity} "
                        f"(threshold: {HIGH_COMPLEXITY_THRESHOLD}). Refactor to reduce branching.",
                        "high" if complexity > 15 else "medium",
                        file_info,
                        line=lineno,
                        evidence=f"complexity={complexity}, name={name}",
                    ))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    return issues


def _check_python_security(file_info: Dict) -> List[Dict]:
    """Use bandit to find security issues in Python files."""
    if file_info.get("language") != "python":
        return []

    issues = []
    path = file_info["path"]

    try:
        result = subprocess.run(
            ["python", "-m", "bandit", "-f", "json", "-q", path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        output = result.stdout.strip()
        if not output:
            return []

        data = json.loads(output)
        for issue in data.get("results", []):
            severity_map = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}
            sev = severity_map.get(issue.get("issue_severity", "LOW"), "low")
            lineno = issue.get("line_number")
            test_name = issue.get("test_name", "unknown")
            issue_text = issue.get("issue_text", "")

            issues.append(_make_issue(
                "security_risk",
                f"[bandit/{test_name}] {issue_text}",
                sev,
                file_info,
                line=lineno,
                evidence=issue.get("code", ""),
            ))

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    return issues


def _check_python_ast(file_info: Dict) -> List[Dict]:
    """Use Python AST to detect missing docstrings and bare excepts."""
    if file_info.get("language") != "python":
        return []

    issues = []
    path = file_info["path"]

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()

        tree = ast.parse(source, filename=path)
    except (SyntaxError, OSError):
        return []

    for node in ast.walk(tree):
        # Check for bare except clauses
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(_make_issue(
                "bare_except",
                "Bare 'except:' clause catches all exceptions including SystemExit and "
                "KeyboardInterrupt. Specify the exception type(s).",
                "medium",
                file_info,
                line=node.lineno,
                evidence=f"Line {node.lineno}: bare except clause",
            ))

        # Check functions/classes for missing docstrings
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not (node.body and isinstance(node.body[0], ast.Expr) and
                    isinstance(node.body[0].value, ast.Constant) and
                    isinstance(node.body[0].value.value, str)):
                if len(node.body) > 3:  # Only flag non-trivial functions
                    issues.append(_make_issue(
                        "missing_docstring",
                        f"'{node.name}' lacks a docstring. Add documentation for maintainability.",
                        "low",
                        file_info,
                        line=node.lineno,
                        evidence=f"Line {node.lineno}: def/class {node.name}",
                    ))

    return issues


def analyze_metrics(file_info: Dict) -> List[Dict]:
    """
    Run all heuristic checks on a single file.

    Args:
        file_info: Enriched dict from get_file_metrics().

    Returns:
        List of issue dicts.
    """
    issues = []
    issues.extend(_check_size_metrics(file_info))
    issues.extend(_check_python_complexity(file_info))
    issues.extend(_check_python_security(file_info))
    issues.extend(_check_python_ast(file_info))
    return issues


def analyze_all_metrics(files: List[Dict]) -> List[Dict]:
    """
    Run heuristic analysis on all files.

    Args:
        files: List of enriched file dicts from get_metrics_for_all().

    Returns:
        Flat list of all issue dicts.
    """
    all_issues = []
    for f in files:
        all_issues.extend(analyze_metrics(f))
    return all_issues
