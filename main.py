"""
main.py -- CLI entrypoint for the Code Review Agent.

Usage:
    python main.py <repo_path> [--output <dir>] [--no-ai] [--open] [--full-scan]

Examples:
    python main.py .
    python main.py . --output ./results
    python main.py . --no-ai      # heuristic-only (fast, no API key needed)
    python main.py . --open       # auto-open report.html in browser
    python main.py . --full-scan  # ignore cache and re-scan everything
"""

import argparse
import json
import os
import sys
import webbrowser
import io
from dotenv import load_dotenv

# Force UTF-8 output on Windows (cp1252 default breaks ASCII art)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv()

from tools.repo_scanner import scan_repo
from tools.file_metrics import get_metrics_for_all
from tools.heuristic_analyzer import analyze_all_metrics
from tools.semgrep_analyzer import analyze_with_semgrep
from tools.merger import merge_findings
from tools.cache_manager import (
    load_cache,
    save_cache,
    filter_changed_files,
    build_updated_cache,
)
from report.generator import generate_report

# ANSI colour codes
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
DIM    = "\033[2m"

DEFAULT_MODEL = "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def print_banner():
    print(f"""
{CYAN}{BOLD}+==========================================+
|        CODE REVIEW AGENT  v1.0           |
|  Architecture | Security | Performance   |
+==========================================+{RESET}
""")


def print_step(n: int, total: int, text: str):
    print(f"  {CYAN}[{n}/{total}]{RESET} {text}")


def print_progress(current: int, total: int, filename: str):
    bar_len = 30
    filled = int(bar_len * current / total) if total else bar_len
    bar = "#" * filled + "-" * (bar_len - filled)
    print(f"\r  [{bar}] {current}/{total}  {filename[:50]}", end="", flush=True)


def sev_color(sev: str) -> str:
    return {
        "critical": RED + BOLD,
        "high": RED,
        "medium": YELLOW,
        "low": GREEN,
        "info": CYAN,
    }.get(sev.lower(), RESET)


def count_severities(issues: list) -> dict:
    counts: dict = {}
    for iss in issues:
        s = iss.get("severity", "info").lower()
        counts[s] = counts.get(s, 0) + 1
    return counts


def print_token_usage(usage: dict):
    """Print a formatted token-consumption panel to the terminal."""
    prompt     = usage.get("prompt_tokens", 0)
    candidates = usage.get("candidates_tokens", 0)
    total      = usage.get("total_tokens", 0)

    # If total_tokens was not separately tracked, derive it
    if total == 0 and (prompt or candidates):
        total = prompt + candidates

    print(f"\n  {CYAN}{BOLD}Token Usage (this run):{RESET}")
    print(f"    {DIM}Prompt tokens     :{RESET}  {BOLD}{prompt:>10,}{RESET}")
    print(f"    {DIM}Response tokens   :{RESET}  {BOLD}{candidates:>10,}{RESET}")
    print(f"    {DIM}{'─' * 30}{RESET}")
    print(f"    {DIM}Total tokens      :{RESET}  {BOLD}{total:>10,}{RESET}\n")


def load_previous_findings(output_dir: str) -> list:
    """Load findings from an existing report.json in *output_dir*, if present."""
    json_path = os.path.join(output_dir, "report.json")
    if not os.path.isfile(json_path):
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("findings", [])
    except (OSError, json.JSONDecodeError):
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="code-review-agent",
        description="Automated multi-agent code review -- generates HTML + JSON reports.",
    )
    parser.add_argument("repo_path", help="Path to the repository to analyze")
    parser.add_argument(
        "--output", "-o",
        default=".",
        help="Directory for output files (default: current directory)",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip AI analysis -- heuristic-only mode, no API key needed",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open report.html in the default browser after generation",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="Ignore the file-hash cache and re-scan every file",
    )

    args = parser.parse_args()

    if not os.path.isdir(args.repo_path):
        print(f"{RED}Error:{RESET} Not a directory: {args.repo_path}")
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)
    print_banner()

    total_steps = 6 if not args.no_ai else 5
    step = 0

    # -- Step 1: Scan ---------------------------------------------------------
    step += 1
    print_step(step, total_steps, f"Scanning: {BOLD}{args.repo_path}{RESET}")
    all_files = scan_repo(args.repo_path)
    if not all_files:
        print(f"  {YELLOW}No supported files (.py, .js, .ts) found.{RESET}")
        sys.exit(0)
    print(f"  {GREEN}OK{RESET} {BOLD}{len(all_files)}{RESET} source files found")

    # -- Incremental file filtering (cache) -----------------------------------
    # Always call filter_changed_files so that current_hash is computed and
    # attached to every file dict. This is required for build_updated_cache to
    # persist entries correctly — even on the very first run where cache == {}.
    cache = {}
    changed_files = all_files
    unchanged_files = []

    if not args.full_scan:
        cache = load_cache(args.repo_path)
        # filter_changed_files ALWAYS runs (even when cache is empty) so that
        # current_hash is attached to each file dict for later cache saving.
        changed_files, unchanged_files = filter_changed_files(all_files, cache)
        skipped = len(unchanged_files)
        new_or_modified = len(changed_files)

        if not cache:
            print(f"  {DIM}No cache found — performing full scan and creating cache.{RESET}")
        elif skipped > 0:
            print(
                f"  {CYAN}Cache:{RESET} "
                f"{BOLD}{skipped}{RESET} file(s) unchanged {DIM}(skipped){RESET},  "
                f"{BOLD}{new_or_modified}{RESET} file(s) changed/new {DIM}(will scan){RESET}"
            )
        else:
            print(f"  {CYAN}Cache:{RESET} All files changed — full scan.")
    else:
        # Even on --full-scan we still hash all files so the cache is refreshed.
        changed_files, unchanged_files = filter_changed_files(all_files, {})
        print(f"  {YELLOW}--full-scan flag set: ignoring cache, re-hashing all files.{RESET}")

    # Collect previous findings for unchanged files from the existing report.json
    previous_findings = []
    if unchanged_files:
        unchanged_rel_paths = {
            f.get("relative_path", f.get("path", "")) for f in unchanged_files
        }
        all_prev = load_previous_findings(args.output)
        previous_findings = [
            f for f in all_prev
            if f.get("file", "") in unchanged_rel_paths
        ]
        print(
            f"  {DIM}Carrying forward {len(previous_findings)} finding(s) "
            f"from {len(unchanged_files)} unchanged file(s).{RESET}"
        )

    print()

    # -- Step 2: Metrics (only for changed files) -----------------------------
    step += 1
    print_step(step, total_steps, "Computing file metrics...")
    files_with_metrics = get_metrics_for_all(changed_files) if changed_files else []
    print(f"  {GREEN}OK{RESET} Metrics computed\n")

    # -- Step 3: Heuristic Analysis (only for changed files) ------------------
    step += 1
    print_step(step, total_steps, "Running heuristic analysis (radon + bandit + AST)...")
    heuristic_issues = analyze_all_metrics(files_with_metrics) if files_with_metrics else []
    sev_counts = count_severities(heuristic_issues)
    summary = "  ".join(f"{sev_color(s)}{c} {s}{RESET}" for s, c in sev_counts.items())
    print(f"  {GREEN}OK{RESET} {len(heuristic_issues)} heuristic findings  {summary}\n")

    # -- Step 4: Semgrep Analysis (multi-language, repo-wide) -----------------
    step += 1
    print_step(step, total_steps, "Running Semgrep static analysis (multi-language)...")
    semgrep_issues: list = []
    if changed_files:
        semgrep_issues = analyze_with_semgrep(args.repo_path, files_with_metrics)
    sg_counts = count_severities(semgrep_issues)
    sg_summary = "  ".join(f"{sev_color(s)}{c} {s}{RESET}" for s, c in sg_counts.items())
    print(f"  {GREEN}OK{RESET} {len(semgrep_issues)} Semgrep findings  {sg_summary}\n")

    # -- Step 5: AI Analysis (ADK, only for changed files) --------------------
    ai_issues: list = []
    token_usage: dict = {}

    if not args.no_ai:
        step += 1
        print_step(step, total_steps, f"Running AI analysis ({args.model}) via ADK...")

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print(f"  {YELLOW}WARNING: GOOGLE_API_KEY not set -- skipping AI analysis.{RESET}")
            print(f"  {DIM}Create a .env file with: GOOGLE_API_KEY=your_key{RESET}\n")
        elif not files_with_metrics:
            print(f"  {DIM}No changed files to analyse -- skipping AI step.{RESET}\n")
        else:
            from agent.runner import CodeReviewRunner
            runner = CodeReviewRunner()
            print()
            ai_issues, token_usage = runner.analyze_files(
                files_with_metrics,
                progress_callback=print_progress,
            )
            print()

            ai_counts = count_severities(ai_issues)
            ai_summary = "  ".join(f"{sev_color(s)}{c} {s}{RESET}" for s, c in ai_counts.items())
            print(f"  {GREEN}OK{RESET} {len(ai_issues)} AI findings  {ai_summary}")

            # Print token usage panel
            if token_usage:
                print_token_usage(token_usage)

    # -- Step 5: Merge & Deduplicate (new findings + carried-forward findings) -
    step += 1
    print_step(step, total_steps, "Merging and deduplicating findings...")

    # Merge: heuristic + semgrep first, then AI on top, then carry-forward
    new_findings = merge_findings(merge_findings(heuristic_issues, semgrep_issues), ai_issues)
    merged = merge_findings(new_findings, previous_findings)

    carry_note = (
        f" ({len(new_findings)} new + {len(previous_findings)} from cache)"
        if previous_findings
        else ""
    )
    print(f"  {GREEN}OK{RESET} {len(merged)} unique findings{carry_note}\n")

    # -- Build incremental info dict for the report ---------------------------
    incremental_info = {
        "full_scan": args.full_scan or not cache,
        "total_files_in_repo": len(all_files),
        "files_scanned_this_run": len(changed_files),
        "files_skipped_from_cache": len(unchanged_files),
        "findings_carried_from_cache": len(previous_findings),
    }

    # -- Generate Report -------------------------------------------------------
    print(f"  {CYAN}Generating report...{RESET}")
    paths = generate_report(
        findings=merged,
        repo_path=args.repo_path,
        total_files=len(all_files),
        output_dir=args.output,
        token_usage=token_usage if token_usage else None,
        incremental_info=incremental_info,
    )

    # -- Save updated cache ---------------------------------------------------
    updated_cache = build_updated_cache(cache, changed_files, unchanged_files)
    save_cache(updated_cache, args.repo_path)
    print(f"  {DIM}Cache updated ({len(updated_cache)} entries).{RESET}\n")

    print(f"\n{GREEN}{BOLD}  Report generated!{RESET}")
    print(f"  {BOLD}HTML:{RESET} {paths['html']}")
    print(f"  {BOLD}JSON:{RESET} {paths['json']}\n")

    # Severity summary
    final_counts = count_severities(merged)
    print(f"  {BOLD}Severity Breakdown:{RESET}")
    for s in ["critical", "high", "medium", "low", "info"]:
        c = final_counts.get(s, 0)
        if c:
            print(f"    {sev_color(s)}{s.upper():10}{RESET}  {c}")

    if args.open:
        webbrowser.open(f"file://{paths['html']}")


if __name__ == "__main__":
    main()
