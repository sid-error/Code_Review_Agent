"""
Report generator — renders findings into HTML and JSON output files.
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape


TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
TEMPLATE_NAME = "report.html.j2"


def generate_report(
    findings: List[Dict],
    repo_path: str,
    total_files: int,
    output_dir: str = ".",
    token_usage: Optional[Dict] = None,
    incremental_info: Optional[Dict] = None,
    json_path_override: Optional[str] = None,
) -> Dict[str, str]:
    """
    Render findings into HTML and JSON report files.

    Args:
        findings: Merged, deduplicated list of issue dicts.
        repo_path: Path to the scanned repository (used for display name).
        total_files: Number of files scanned.
        output_dir: Directory to write output files into.

    Returns:
        Dict with 'html' and 'json' keys pointing to output file paths.
    """
    repo_name = os.path.basename(os.path.abspath(repo_path))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Compute severity counts
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for issue in findings:
        sev = issue.get("severity", "info").lower()
        counts[sev] = counts.get(sev, 0) + 1

    # Jinja2 rendering
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(TEMPLATE_NAME)

    html_content = template.render(
        repo_name=repo_name,
        generated_at=generated_at,
        total_files=total_files,
        total_issues=len(findings),
        counts=counts,
        issues=findings,
    )

    # Write HTML
    html_path = os.path.join(output_dir, "report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Write JSON
    json_report = {
        "repo": repo_name,
        "generated_at": generated_at,
        "total_files_scanned": total_files,
        "total_issues": len(findings),
        "severity_counts": counts,
        "findings": findings,
    }
    if token_usage:
        json_report["token_usage"] = token_usage
    if incremental_info:
        json_report["incremental_scan"] = incremental_info

    json_path = json_path_override if json_path_override else os.path.join(output_dir, "report.json")
    # Ensure the parent directory exists (important when json_path_override points to a new dir)
    os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_report, f, indent=2, ensure_ascii=False)

    return {"html": os.path.abspath(html_path), "json": os.path.abspath(json_path)}
