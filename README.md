# Code Review Agent

An automated, multi-agent code review system powered by **Google Gemini 2.5 Pro** (via the **Google Agent Development Kit**), **Temporal** for workflow reliability, and **Streamlit** for an interactive UI. It scans a Python, JavaScript, or TypeScript repository and produces a structured audit report rendered directly in the browser.

---

## Architecture

```
Streamlit UI ──► Temporal Workflow ──► Activities ──► HTML + JSON Report
                       │
              ┌────────┴────────────────────────┐
              ▼                                  ▼
   Pause / Resume Signal               Crash Recovery (auto-replay)
```

### Pipeline Phases

| Phase | Module | What it does |
|---|---|---|
| 1 | `tools/repo_scanner.py` | Walks repo, finds `.py`, `.js`, `.ts` files |
| 2 | `tools/file_metrics.py` | Computes `size_kb` and `line_count` per file |
| 3 | `tools/heuristic_analyzer.py` | Runs `radon` (complexity), `bandit` (security), `ast` (bare excepts, docstrings) |
| 4 | `tools/semgrep_analyzer.py` | Multi-language static analysis via Semgrep |
| 5 | `agent/runner.py` | ADK Runner — dispatches file chunks to the orchestrator agent |
| 6 | `tools/merger.py` + `report/generator.py` | Deduplicates findings, renders HTML + JSON report |
| 7 | `tools/cache_manager.py` | Persists file-hash cache for incremental scanning |

### ADK Agent Hierarchy

```
agent/orchestrator/agent.py          <- root_agent (AgentTool composition)
    agent/architecture_agent/agent.py  <- detects SRP, god objects, coupling
    agent/security_agent/agent.py      <- detects secrets, injection, XSS
    agent/performance_agent/agent.py   <- detects O(n^2), N+1 queries, leaks
```

### Temporal Workflow

```
temporal/
├── workflows.py    # CodeReviewWorkflow — durable, pausable pipeline
├── activities.py   # One @activity.defn per pipeline phase
├── worker.py       # Worker process (register & run)
├── client.py       # Temporal client factory (localhost:7233)
└── models.py       # RunConfig, ProgressUpdate dataclasses
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd Code_Review_Agent
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and add your key:
# GOOGLE_API_KEY=your_gemini_api_key
```

### 3. Start Temporal (Docker + PostgreSQL)

Temporal uses **PostgreSQL 16** as its persistence backend, managed via Docker Compose.

```bash
# Start Temporal server + PostgreSQL + Temporal UI (detached)
docker compose up -d

# First run pulls images and initialises the DB (~30-60 s).
# Check all containers are running:
docker compose ps
```

> **Temporal UI** is available at `http://localhost:8080` — inspect workflows, history, and signals.

---

## Usage

### Streamlit UI (recommended)

```bash
# Terminal 1 — Temporal + Cassandra via Docker
docker compose up -d

# Terminal 2 — Temporal worker
python temporal/worker.py

# Terminal 3 — Streamlit UI
streamlit run streamlit_app.py
```

Then open `http://localhost:8501` in your browser:
1. Enter the **repository path** in the sidebar
2. Choose model and options
3. Click **▶ Run Analysis**
4. Watch live progress — use **⏸ Pause** / **▶ Resume** to control AI analysis
5. View the full audit report inline when done

### CLI (also works as before)

```bash
# Full analysis (heuristic + semgrep + AI)
python main.py <path/to/repo>

# Heuristic + semgrep only (no API key needed, fast)
python main.py <path/to/repo> --no-ai

# Specify output directory for report.html
python main.py <path/to/repo> --output ./results

# Open report.html in browser automatically
python main.py <path/to/repo> --open

# Ignore cache and re-scan everything
python main.py <path/to/repo> --full-scan
```

---

## Output

| File | Location | Description |
|---|---|---|
| `report.html` | `--output` dir (default: `.`) | Interactive dark-theme audit report |
| `report.json` | `<repo>/.code_review_reports/report.json` | Machine-readable JSON with all findings |
| `.code_review_cache.json` | `<repo>/` root | File-hash cache for incremental scanning |
| `.code_review_progress.json` | `<repo>/` root | Live progress (polled by Streamlit UI) |

### Report structure per issue

Each finding contains:
- **What We Observed** — clear description of the problem
- **Root Cause** — why the issue exists
- **Recommended Fix** — concrete actionable steps
- **Evidence** — the relevant code snippet
- **Source** — `heuristic` | `semgrep` | `ai`

---

## Running Individual Agents with ADK

```bash
# Inspect the full agent system in ADK Web UI
adk web agent/orchestrator

# Run a sub-agent interactively
adk run agent/security_agent
```

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Project Structure

```
Code_Review_Agent/
├── streamlit_app.py           # Streamlit UI (main entry point)
├── main.py                    # CLI entrypoint (still works)
├── docker-compose.yml         # Temporal + Cassandra via Docker
├── temporal-config/
│   └── dynamicconfig/
│       └── development-cass.yaml  # Temporal server dynamic config
├── temporal/
│   ├── workflows.py           # Durable workflow with pause/resume
│   ├── activities.py          # Per-phase activity definitions
│   ├── worker.py              # Worker process
│   ├── client.py              # Temporal client factory
│   └── models.py              # Shared dataclasses
├── agent/
│   ├── architecture_agent/    # ADK Agent: architecture issues
│   ├── security_agent/        # ADK Agent: security vulnerabilities
│   ├── performance_agent/     # ADK Agent: performance problems
│   ├── orchestrator/          # ADK root_agent
│   └── runner.py              # ADK Runner wrapper
├── tools/
│   ├── repo_scanner.py        # Phase 1: file discovery
│   ├── file_metrics.py        # Phase 2: size + line count
│   ├── heuristic_analyzer.py  # Phase 3: radon + bandit + AST
│   ├── semgrep_analyzer.py    # Phase 4: Semgrep multi-language
│   ├── llm_analyzer.py        # File chunking + prompt building
│   ├── merger.py              # Phase 6: deduplication
│   └── cache_manager.py       # Incremental cache + report path
├── report/
│   ├── generator.py           # Jinja2 renderer -> HTML + JSON
│   └── templates/
│       └── report.html.j2     # Premium dark-theme template
├── tests/                     # pytest test suite
├── implementation_plan/       # Implementation plan documents
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| UI | Streamlit |
| Workflow | Temporal (Python SDK) |
| AI Framework | Google Agent Development Kit (`google-adk`) |
| LLM | Gemini 2.5 Pro |
| Static Analysis | `radon` (complexity), `bandit` (security), `ast`, Semgrep |
| Report | Jinja2 HTML + JSON |
| Config | `.env` + `python-dotenv` |
