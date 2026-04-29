"""
main.py -- CLI entrypoint for the Code Review Agent.

Usage:
    python main.py <repo_path> [--output <dir>] [--no-ai] [--open]

Examples:
    python main.py .
    python main.py . --output ./results
    python main.py . --no-ai      # heuristic-only (fast, no API key needed)
    python main.py . --open       # auto-open report.html in browser
"""

import argparse
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
from tools.merger import merge_findings
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

    args = parser.parse_args()

    if not os.path.isdir(args.repo_path):
        print(f"{RED}Error:{RESET} Not a directory: {args.repo_path}")
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)
    print_banner()

    total_steps = 5 if not args.no_ai else 4
    step = 0

    # -- Step 1: Scan ---------------------------------------------------------
    step += 1
    print_step(step, total_steps, f"Scanning: {BOLD}{args.repo_path}{RESET}")
    files = scan_repo(args.repo_path)
    if not files:
        print(f"  {YELLOW}No supported files (.py, .js, .ts) found.{RESET}")
        sys.exit(0)
    print(f"  {GREEN}OK{RESET} {BOLD}{len(files)}{RESET} source files found\n")

    # -- Step 2: Metrics ------------------------------------------------------
    step += 1
    print_step(step, total_steps, "Computing file metrics...")
    files_with_metrics = get_metrics_for_all(files)
    print(f"  {GREEN}OK{RESET} Metrics computed\n")

    # -- Step 3: Heuristic Analysis -------------------------------------------
    step += 1
    print_step(step, total_steps, "Running heuristic analysis (radon + bandit + AST)...")
    heuristic_issues = analyze_all_metrics(files_with_metrics)
    sev_counts = count_severities(heuristic_issues)
    summary = "  ".join(f"{sev_color(s)}{c} {s}{RESET}" for s, c in sev_counts.items())
    print(f"  {GREEN}OK{RESET} {len(heuristic_issues)} heuristic findings  {summary}\n")

    # -- Step 4: AI Analysis (ADK) --------------------------------------------
    ai_issues: list = []
    if not args.no_ai:
        step += 1
        print_step(step, total_steps, f"Running AI analysis ({args.model}) via ADK...")

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print(f"  {YELLOW}WARNING: GOOGLE_API_KEY not set -- skipping AI analysis.{RESET}")
            print(f"  {DIM}Create a .env file with: GOOGLE_API_KEY=your_key{RESET}\n")
        else:
            from agent.runner import CodeReviewRunner
            runner = CodeReviewRunner()
            print()
            ai_issues = runner.analyze_files(
                files_with_metrics,
                progress_callback=print_progress,
            )
            print()

            ai_counts = count_severities(ai_issues)
            ai_summary = "  ".join(f"{sev_color(s)}{c} {s}{RESET}" for s, c in ai_counts.items())
            print(f"  {GREEN}OK{RESET} {len(ai_issues)} AI findings  {ai_summary}\n")

    # -- Step 5: Merge & Deduplicate ------------------------------------------
    step += 1
    print_step(step, total_steps, "Merging and deduplicating findings...")
    merged = merge_findings(heuristic_issues, ai_issues)
    print(f"  {GREEN}OK{RESET} {len(merged)} unique findings\n")

    # -- Generate Report -------------------------------------------------------
    print(f"  {CYAN}Generating report...{RESET}")
    paths = generate_report(
        findings=merged,
        repo_path=args.repo_path,
        total_files=len(files),
        output_dir=args.output,
    )

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
