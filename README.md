# 🔍 Code Review Agent

An automated, multi-agent code review pipeline powered by **Google Gemini AI**, **Temporal.io** durable workflows, **Semgrep**, **radon**, and **bandit**. It scans any local repository and produces structured HTML + JSON reports covering architecture, security, and performance issues — with an optional Streamlit web UI for interactive analysis.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Mode 1 – CLI (simple, no Temporal)](#mode-1--cli-simple-no-temporal)
  - [Mode 2 – Streamlit UI with Temporal](#mode-2--streamlit-ui-with-temporal)
- [Pipeline Stages](#pipeline-stages)
- [Analysis Sources](#analysis-sources)
- [Output Reports](#output-reports)
- [Incremental Scanning & Caching](#incremental-scanning--caching)
- [Supported Languages](#supported-languages)
- [Agent System](#agent-system)
- [Temporal Workflow](#temporal-workflow)
- [Running Tests](#running-tests)
- [File Reference](#file-reference)

---

## Overview

The Code Review Agent automates the tedious parts of code review. Given a path to any local Git repository, it:

1. **Scans** all source files and computes file metrics.
2. **Runs heuristic checks** (radon cyclomatic complexity + bandit security + AST analysis) on Python files.
3. **Runs Semgrep** multi-language static analysis across all supported languages.
4. **Delegates each file to three Gemini AI specialist agents** (Architecture, Security, Performance) orchestrated by a root ADK agent.
5. **Merges and deduplicates** all findings, carrying forward cached results for unchanged files.
6. **Generates HTML + JSON reports** with severity breakdowns, source attribution, and token usage stats.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Entry Points                             │
│   main.py (CLI)              streamlit_app.py (Web UI)          │
└────────────────┬────────────────────────┬───────────────────────┘
                 │                        │
                 │              ┌─────────▼────────────┐
                 │              │  Temporal Workflow   │
                 │              │  (CodeReviewWorkflow)│
                 │              └─────────┬────────────┘
                 │                        │ Activities
                 ▼                        ▼
┌───────────────────────────────────────────────────────────────────┐
│                      Pipeline Stages                              │
│                                                                   │
│  [1] repo_scanner ──► [2] file_metrics ──► [3] heuristic_analyzer │
│                                        ──► [4] semgrep_analyzer   │
│                                        ──► [5] AI (ADK Runner)    │
│                                                │                  │
│                                    ┌───────────▼──────────────┐   │
│                                    │  Orchestrator Agent (ADK)│   │
│                                    │  ┌────────────────────┐  │   │
│                                    │  │ architecture_agent │  │   │
│                                    │  │ security_agent     │  │   │
│                                    │  │ performance_agent  │  │   │
│                                    │  └────────────────────┘  │   │
│                                    └──────────────────────────┘   │
│                                                                   │
│  [6] merger ──► [7] report generator ──► [8] cache_manager        │
└───────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **Three specialist AI agents** | Separation of concerns — each agent has a focused system prompt and produces cleaner, more targeted findings |
| **Heuristic-first** | Fast, deterministic checks run before AI so obvious issues don't cost API tokens |
| **Temporal durable workflows** | Crash recovery, pause/resume, heartbeat monitoring for long-running AI analysis on large repos |
| **Incremental file-hash cache** | Only re-analyse changed files; carry forward prior findings for unchanged files |
| **Semgrep** | Free multi-language static analysis without requiring language-specific tooling per language |

---

## Features

- ✅ **Multi-source analysis** — heuristic, Semgrep, and AI findings merged with smart deduplication
- ✅ **Three AI specialists** — Architecture, Security, and Performance agents run in parallel
- ✅ **Incremental scanning** — SHA-256 file hashing skips unchanged files
- ✅ **Durable workflows** — Temporal.io enables pause/resume and crash recovery
- ✅ **Live progress tracking** — Streamlit UI polls a progress sidecar file for real-time updates
- ✅ **Persistent run history** — All past runs survive browser reload
- ✅ **Severity filtering** — Filter findings by `critical / high / medium / low / info`
- ✅ **Source filtering** — Filter by `heuristic / semgrep / ai`
- ✅ **Token usage reporting** — Prompt, response, and total token counts shown per run
- ✅ **HTML + JSON reports** — Both downloadable from the UI
- ✅ **Heuristic-only mode** — Skip AI entirely for fast, free analysis
- ✅ **Multi-language support** — Python, JavaScript, TypeScript, Java, Go, Ruby, C#, PHP, Kotlin, Rust, Shell

---

## Project Structure

```
Code_Review_Agent/
├── main.py                        # CLI entry point
├── streamlit_app.py               # Streamlit web UI
├── requirements.txt               # Python dependencies
├── docker-compose.yml             # Temporal + PostgreSQL stack
├── .env.example                   # Environment variable template
│
├── agent/                         # Google ADK multi-agent system
│   ├── runner.py                  # ADK Runner wrapper & token tracking
│   ├── orchestrator/
│   │   └── agent.py               # Root orchestrator (dispatches to 3 specialists)
│   ├── architecture_agent/
│   │   └── agent.py               # Detects SRP violations, god objects, tight coupling
│   ├── security_agent/
│   │   └── agent.py               # Detects secrets, injections, unsafe patterns
│   └── performance_agent/
│       └── agent.py               # Detects O(n²), N+1 queries, memory leaks
│
├── tools/                         # Pipeline analysis tools
│   ├── repo_scanner.py            # Walks repo, discovers source files
│   ├── file_metrics.py            # Computes size_kb + line_count
│   ├── heuristic_analyzer.py      # radon + bandit + AST checks
│   ├── semgrep_analyzer.py        # Semgrep multi-language static analysis
│   ├── llm_analyzer.py            # File chunker for LLM token limits
│   ├── merger.py                  # Deduplicates + merges all findings
│   ├── cache_manager.py           # SHA-256 incremental scan cache
│   └── run_registry.py            # Persistent run history (JSON store)
│
├── temporal/                      # Temporal.io workflow components
│   ├── workflows.py               # CodeReviewWorkflow (7-step durable workflow)
│   ├── activities.py              # One activity function per pipeline stage
│   ├── models.py                  # RunConfig + ProgressUpdate dataclasses
│   ├── client.py                  # Temporal client factory (localhost:7233)
│   └── worker.py                  # Worker process — registers workflow + activities
│
├── report/                        # Report generation
│   ├── generator.py               # Jinja2 HTML + JSON report renderer
│   └── templates/
│       └── report.html.j2         # HTML report Jinja2 template
│
└── tests/                         # Unit tests (pytest)
    ├── test_heuristics.py
    ├── test_metrics.py
    └── test_scanner.py
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | Required for `asyncio` features |
| Docker Desktop | Any | Required only for Streamlit/Temporal mode |
| Semgrep | ≥ 1.70 | Installed via `pip`; downloads rule packs on first run |
| Google API Key | — | Required for AI analysis; free tier works |

---

## Installation

### 1. Clone the repository

```bash
git clone <repo-url>
cd Code_Review_Agent
```

### 2. Create a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `semgrep` downloads language rule packs from the internet on its first invocation. Ensure you have network access during the first run.

### 4. Set up your API key

```bash
# Copy the example file
cp .env.example .env

# Edit .env and add your key
GOOGLE_API_KEY=your_google_api_key_here
```

You can obtain a free Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).

---

## Configuration

All runtime options are controlled via CLI flags (for `main.py`) or the Streamlit sidebar. The only required environment variable is:

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` | Yes (for AI mode) | Gemini API key for AI analysis |

### `RunConfig` parameters (shared by CLI and Streamlit)

| Parameter | Default | Description |
|---|---|---|
| `repo_path` | — | Absolute path to the repository to analyse |
| `output_dir` | `.` | Directory where `report.html` is written |
| `model` | `gemini-2.5-pro` | Gemini model name |
| `no_ai` | `false` | Heuristic-only mode; skips AI, no API key needed |
| `full_scan` | `false` | Ignore file-hash cache; re-scan every file |

---

## Usage

### Mode 1 – CLI (simple, no Temporal)

The CLI (`main.py`) runs the full pipeline in a single process — no Docker or Temporal needed.

#### Basic scan (AI enabled)

```bash
python main.py /path/to/your/repo
```

#### Heuristic-only (no API key required, fast)

```bash
python main.py /path/to/your/repo --no-ai
```

#### Specify output directory

```bash
python main.py /path/to/your/repo --output ./results
```

#### Auto-open report in browser after completion

```bash
python main.py /path/to/your/repo --open
```

#### Force full re-scan (ignore incremental cache)

```bash
python main.py /path/to/your/repo --full-scan
```

#### Use a different Gemini model

```bash
python main.py /path/to/your/repo --model gemini-2.0-flash
```

#### All options

```
python main.py <repo_path> [--output DIR] [--no-ai] [--open] [--full-scan] [--model MODEL]

positional arguments:
  repo_path            Path to the repository to analyse

optional arguments:
  --output, -o DIR     Output directory for report.html (default: current dir)
  --no-ai              Skip AI analysis (heuristic + semgrep only)
  --open               Auto-open report.html in browser after generation
  --model MODEL        Gemini model name (default: gemini-2.5-pro)
  --full-scan          Ignore cache and re-scan every file
```

#### Example output

```
+==========================================+
|        CODE REVIEW AGENT  v1.0           |
|  Architecture | Security | Performance   |
+==========================================+

  [1/6] Scanning: /path/to/repo
  OK 42 source files found
  Cache: 38 file(s) unchanged (skipped),  4 file(s) changed/new (will scan)

  [2/6] Computing file metrics...
  OK Metrics computed

  [3/6] Running heuristic analysis (radon + bandit + AST)...
  OK 12 heuristic findings  3 medium  7 low  2 info

  [4/6] Running Semgrep static analysis (multi-language)...
  OK 5 Semgrep findings  1 high  4 medium

  [5/6] Running AI analysis (gemini-2.5-pro) via ADK...
  [##############################] 4/4  my_module.py

  OK 8 AI findings  1 critical  3 high  4 medium

  Token Usage (this run):
    Prompt tokens     :     45,210
    Response tokens   :      3,890
    ──────────────────────────────
    Total tokens      :     49,100

  [6/6] Merging and deduplicating findings...
  OK 22 unique findings (17 new + 5 from cache)

  Report generated!
  HTML: /path/to/repo/report.html
  JSON: /path/to/repo/.code_review_reports/report.json

  Severity Breakdown:
    CRITICAL    1
    HIGH        4
    MEDIUM      11
    LOW         6
```

---

### Mode 2 – Streamlit UI with Temporal

The Streamlit UI runs the same pipeline as a **durable Temporal workflow**, enabling pause/resume, live progress tracking, persistent run history, and downloadable reports.

#### Step 1 — Start Temporal + PostgreSQL via Docker

```bash
docker compose up -d
```

This starts three containers:
- `temporal-postgresql` — PostgreSQL persistence backend (port `5432`)
- `temporal-server` — Temporal server (port `7233`)
- `temporal-ui` — Temporal Web UI (port `8080`)

Wait ~60–90 seconds for the containers to become healthy on first run.

#### Step 2 — Start the Temporal worker

In a **separate terminal**:

```bash
python temporal/worker.py
```

Keep this running. It registers the `CodeReviewWorkflow` and all 7 activities with the `code-review-queue` task queue.

#### Step 3 — Start Streamlit

In **another terminal**:

```bash
streamlit run streamlit_app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

#### Using the Streamlit UI

1. **Sidebar** — Enter the full path to the repository you want to analyse.
2. Choose a Gemini model, toggle heuristic-only mode or full-scan as needed.
3. Click **▶ Run Analysis** to start a Temporal workflow.
4. Watch **live progress** update in the main panel (auto-refreshes every 2 seconds).
5. Use **⏸ Pause** to pause before the AI step and **▶ Resume** to continue.
6. Use **⏹ Stop** to cancel the run.
7. Once done, click **📄 View** on any completed run to see the inline report.
8. Download **report.json** or **report.html** using the download buttons.
9. Use **🔁 Re-run** to repeat a past run with the same configuration.

> **Temporal UI** — Browse workflow execution history, replay failed runs, and inspect activity inputs/outputs at [http://localhost:8080](http://localhost:8080).

---

## Pipeline Stages

The pipeline runs the same 7 stages in both CLI and Streamlit/Temporal modes:

| Step | Name | Tool | Description |
|---|---|---|---|
| 1 | **Scan** | `repo_scanner.py` | Walk the repo; discover all supported source files; apply file-hash cache filter |
| 2 | **Metrics** | `file_metrics.py` | Compute `size_kb` and `line_count` for each changed file |
| 3 | **Heuristics** | `heuristic_analyzer.py` | Run **radon** (cyclomatic complexity), **bandit** (Python security), and Python AST checks (bare `except`, missing docstrings) |
| 4 | **Semgrep** | `semgrep_analyzer.py` | Run Semgrep with language-specific rule packs (`p/python`, `p/javascript`, `p/java`, `p/golang`, `p/default`) |
| 5 | **AI Analysis** | `agent/runner.py` | Send each file (in chunks) to the Gemini orchestrator agent, which delegates to three specialist sub-agents |
| 6 | **Merge & Report** | `merger.py` + `report/generator.py` | Deduplicate all findings; carry forward cached findings; generate `report.html` + `report.json` |
| 7 | **Cache** | `cache_manager.py` | Save updated SHA-256 hash map to `.code_review_cache.json` inside the repo |

---

## Analysis Sources

Every finding in the report carries a `source` field indicating which analysis engine produced it:

### `heuristic` — Rule-based static analysis (Python only)

Runs without any API key. Fast and deterministic.

| Check | Severity | Description |
|---|---|---|
| `large_file` | high | Files > 500 lines |
| `large_size` | medium | Files > 200 KB |
| `high_complexity` | medium/high | Functions with McCabe cyclomatic complexity > 10 |
| `security_risk` | low–high | Bandit findings (hardcoded passwords, subprocess misuse, etc.) |
| `bare_except` | medium | `except:` clauses that catch all exceptions |
| `missing_docstring` | low | Non-trivial functions/classes without docstrings |

### `semgrep` — Multi-language static analysis

Uses free Semgrep registry rule packs. No login required.

| Ruleset | Languages |
|---|---|
| `p/python` | `.py` |
| `p/javascript` | `.js`, `.ts` |
| `p/java` | `.java` |
| `p/golang` | `.go` |
| `p/default` | Ruby, C#, PHP, Kotlin, Rust, Shell |

### `ai` — Gemini multi-agent analysis

Three specialist agents each review every file:

| Agent | Focus Areas |
|---|---|
| **Architecture** | SRP violations, god objects, tight coupling, circular dependencies, poor module organization |
| **Security** | Hardcoded secrets, SQL injection, command injection, unsafe deserialization, XSS, path traversal, missing auth |
| **Performance** | O(n²) loops, N+1 DB queries, memory leaks, blocking I/O in async contexts, missing caching |

Each AI finding includes:
- `type` — snake_case label (e.g., `god_object`, `sql_injection`)
- `message` — 2–3 sentence explanation
- `severity` — `critical | high | medium | low`
- `root_cause` — one-sentence root cause
- `recommended_fix` — concrete actionable fix
- `evidence` — relevant code snippet (max 3 lines)

---

## Output Reports

### `report.html`

A self-contained HTML report with:
- Severity breakdown dashboard
- Source breakdown (heuristic / semgrep / ai counts)
- Token usage panel
- Full findings list with collapsible cards

Written to the `--output` directory (default: current directory).

### `report.json`

Machine-readable JSON at `<repo>/.code_review_reports/report.json`:

```json
{
  "repo": "my-project",
  "repo_path": "/absolute/path/to/my-project",
  "generated_at": "2026-05-06 10:00:00",
  "total_files_scanned": 42,
  "total_issues": 25,
  "severity_counts": { "critical": 1, "high": 4, "medium": 12, "low": 7, "info": 1 },
  "token_usage": { "prompt_tokens": 45210, "candidates_tokens": 3890, "total_tokens": 49100 },
  "incremental_scan": {
    "full_scan": false,
    "total_files_in_repo": 42,
    "files_scanned_this_run": 4,
    "files_skipped_from_cache": 38,
    "findings_carried_from_cache": 18
  },
  "findings": [
    {
      "source": "ai",
      "type": "god_object",
      "message": "The class handles database access, business logic, and HTTP serialization.",
      "severity": "high",
      "file": "src/models/user.py",
      "line": 45,
      "root_cause": "No layered architecture was applied during initial development.",
      "recommended_fix": "Split into a data access object, a service layer, and a serializer.",
      "evidence": "class User:\n    def save(self): ...\n    def send_email(self): ..."
    }
  ]
}
```

### Progress sidecar file

While a Temporal workflow is running, each activity writes progress to `<repo>/.code_review_progress.json`. The Streamlit UI polls this file every 2 seconds to display live step-by-step progress.

---

## Incremental Scanning & Caching

On every run, the agent computes a **SHA-256 hash** of each source file and compares it against the cache stored in `<repo>/.code_review_cache.json`.

- **Unchanged files** — skipped; their previous findings are carried forward from `report.json`
- **Changed/new files** — fully re-analysed through all pipeline stages
- **`--full-scan` flag** — bypass the cache; re-hash and re-analyse every file

This makes repeated runs on large repositories much faster (e.g., a 200-file repo where only 3 files changed will only run AI analysis on those 3 files).

---

## Supported Languages

| Extension | Language | Heuristics | Semgrep | AI |
|---|---|---|---|---|
| `.py` | Python | ✅ radon + bandit + AST | ✅ p/python | ✅ |
| `.js` | JavaScript | ❌ | ✅ p/javascript | ✅ |
| `.ts` | TypeScript | ❌ | ✅ p/javascript | ✅ |
| `.java` | Java | ❌ | ✅ p/java | ✅ |
| `.go` | Go | ❌ | ✅ p/golang | ✅ |
| `.rb` | Ruby | ❌ | ✅ p/default | ✅ |
| `.cs` | C# | ❌ | ✅ p/default | ✅ |
| `.php` | PHP | ❌ | ✅ p/default | ✅ |
| `.kt` | Kotlin | ❌ | ✅ p/default | ✅ |
| `.rs` | Rust | ❌ | ✅ p/default | ✅ |
| `.sh` | Shell | ❌ | ✅ p/default | ✅ |

---

## Agent System

The AI analysis layer uses **Google ADK (Agent Development Kit)** with three specialist sub-agents wrapped inside a root orchestrator.

### Orchestrator (`agent/orchestrator/agent.py`)

The orchestrator receives a file's source code and calls **all three specialists** in sequence using `AgentTool` wrappers. It merges the three JSON arrays and returns a single flat findings list.

```
Orchestrator
 ├── calls → architecture_agent (via AgentTool)
 ├── calls → security_agent     (via AgentTool)
 └── calls → performance_agent  (via AgentTool)
```

### File Chunking (`tools/llm_analyzer.py`)

Large files (>10,000 characters ≈ 3,000 tokens) are split into overlapping chunks at newline boundaries. Each chunk is sent as a separate message to the orchestrator to stay within the model's context window.

### Token Tracking (`agent/runner.py`)

The `CodeReviewRunner` accumulates `usage_metadata` from every ADK event across all files and chunks, returning cumulative `prompt_tokens`, `candidates_tokens`, and `total_tokens` at the end of each run.

---

## Temporal Workflow

The `CodeReviewWorkflow` (`temporal/workflows.py`) is a durable, 7-activity workflow with pause/resume support.

### Signals

| Signal | Effect |
|---|---|
| `pause()` | Sets `_paused = True`; workflow waits before / after the AI step |
| `resume()` | Clears the pause flag; workflow continues |

### Queries

| Query | Returns |
|---|---|
| `get_status()` | Current status string: `starting | running | paused | done` |

### Timeouts

| Activity | Timeout |
|---|---|
| Scan, Metrics, Heuristics, Semgrep, Merge, Cache | 10 minutes |
| AI Analysis | 2 hours (with 5-minute heartbeat) |

### Crash Recovery

If the worker process is killed mid-run, Temporal replays the workflow from the last successfully completed activity when the worker restarts. Combined with the file-hash cache, this means already-analysed files are not re-sent to the AI.

---

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_heuristics.py -v

# Run with coverage
pytest tests/ --cov=tools --cov-report=term-missing
```

### Test files

| File | What it tests |
|---|---|
| `tests/test_heuristics.py` | `analyze_metrics()` — large file, bare except, missing docstring, JS language guard |
| `tests/test_metrics.py` | `get_file_metrics()` — size_kb and line_count computation |
| `tests/test_scanner.py` | `scan_repo()` — directory walk, extension filtering, skip-dir logic |

---

## File Reference

### Hidden files created inside the scanned repository

| File | Description |
|---|---|
| `.code_review_cache.json` | SHA-256 hash map for incremental scanning |
| `.code_review_reports/report.json` | Last generated JSON report |
| `.code_review_progress.json` | Live progress sidecar (Temporal mode only) |

### Hidden files in the project root

| File | Description |
|---|---|
| `.code_review_runs.json` | Persistent Streamlit run history registry |
| `.env` | Local environment variables (not committed) |

---

## Troubleshooting

### `GOOGLE_API_KEY not set`
Create a `.env` file in the project root with `GOOGLE_API_KEY=your_key`. Use `--no-ai` flag to skip AI entirely.

### `semgrep: command not found`
Run `pip install semgrep`. On the first run, Semgrep downloads rule packs from the internet (~200 MB). Ensure network access.

### `Failed to start: Is the Temporal worker running?`
Start the Temporal stack with `docker compose up -d`, wait ~90 seconds, then run `python temporal/worker.py` before using the Streamlit UI.

### Windows encoding errors
The CLI and activities both set `PYTHONUTF8=1` and force UTF-8 stdout. If you see encoding errors in subprocess output, ensure you are running Python 3.11+ on Windows.

### Temporal workflow stays in `running` state after worker restart
This is expected — Temporal replays the workflow. Once the worker is back up, the workflow continues from where it left off.

---

## License

This project was developed as part of an internship at Shopalyst. All rights reserved.
