# Code Review Agent

An automated, multi-agent code review system powered by **Google Gemini 2.5 Pro** (via the **Google Agent Development Kit**) and static analysis tools. It scans a Python, JavaScript, or TypeScript repository and produces a structured **HTML + JSON audit report**.

---

## Architecture

```
Scan Repo --> File Metrics --> Heuristic Analysis --> ADK Agent Analysis --> Merge --> HTML Report
```

### Pipeline Phases

| Phase | Module | What it does |
|---|---|---|
| 1 | `tools/repo_scanner.py` | Walks repo, finds `.py`, `.js`, `.ts` files |
| 2 | `tools/file_metrics.py` | Computes `size_kb` and `line_count` per file |
| 3 | `tools/heuristic_analyzer.py` | Runs `radon` (complexity), `bandit` (security), and `ast` (bare excepts, docstrings) |
| 4 | `agent/runner.py` | ADK Runner -- dispatches file chunks to the orchestrator agent |
| 5 | `tools/merger.py` + `report/generator.py` | Deduplicates findings, renders HTML + JSON report |

### ADK Agent Hierarchy

```
agent/orchestrator/agent.py          <-- root_agent (AgentTool composition)
    agent/architecture_agent/agent.py  <-- detects SRP, god objects, coupling
    agent/security_agent/agent.py      <-- detects secrets, injection, XSS
    agent/performance_agent/agent.py   <-- detects O(n^2), N+1 queries, leaks
```

Each specialist is a standalone `google.adk.agents.Agent` that can also be run independently with `adk web` or `adk run`.

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

Get an API key from [Google AI Studio](https://aishudio.google.com/).

---

## Usage

### Full analysis (heuristic + AI)

```bash
python main.py <path/to/repo>
```

### Heuristic-only (no API key needed, fast)

```bash
python main.py <path/to/repo> --no-ai
```

### Specify output directory

```bash
python main.py <path/to/repo> --output ./results
```

### Open report in browser automatically

```bash
python main.py <path/to/repo> --open
```

### All options

```
usage: code-review-agent [-h] [--output OUTPUT] [--no-ai] [--open] [--model MODEL] repo_path

positional arguments:
  repo_path          Path to the repository to analyze

options:
  --output, -o       Directory for output files (default: current directory)
  --no-ai            Skip AI analysis (heuristic-only mode)
  --open             Open report.html in browser after generation
  --model MODEL      Gemini model name (default: gemini-2.5-pro)
```

## Output

Two files are written to the output directory:

| File | Description |
|---|---|
| `report.html` | Interactive dark-theme audit report with severity filters and collapsible issue cards |
| `report.json` | Machine-readable JSON with all findings, severity counts, and metadata |

### Report structure per issue

Each finding contains:
- **What We Observed** -- clear description of the problem
- **Likely Root Cause** -- why the issue exists
- **Recommended Fix** -- concrete actionable steps
- **Evidence** -- the relevant code snippet

---

## Running Individual Agents with ADK

Each agent folder is a valid ADK module. You can run or inspect them with:

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
├── agent/
│   ├── architecture_agent/    # ADK Agent: architecture issues
│   ├── security_agent/        # ADK Agent: security vulnerabilities
│   ├── performance_agent/     # ADK Agent: performance problems
│   ├── orchestrator/          # ADK root_agent: composes all three via AgentTool
│   └── runner.py              # ADK Runner wrapper for the pipeline
├── tools/
│   ├── repo_scanner.py        # Phase 1: file discovery
│   ├── file_metrics.py        # Phase 2: size + line count
│   ├── heuristic_analyzer.py  # Phase 3: radon + bandit + AST
│   ├── llm_analyzer.py        # File chunking + prompt building
│   └── merger.py              # Phase 5: deduplication
├── report/
│   ├── generator.py           # Jinja2 renderer -> HTML + JSON
│   └── templates/
│       └── report.html.j2     # Premium dark-theme template
├── tests/                     # pytest test suite (20 tests)
├── implementation_plan/       # Implementation plan documents
├── main.py                    # CLI entrypoint
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| AI Framework | Google Agent Development Kit (`google-adk`) |
| LLM | Gemini 2.5 Pro |
| Static Analysis | `radon` (complexity), `bandit` (security), `ast` (AST patterns) |
| Report | Jinja2 HTML + JSON |
| Config | `.env` + `python-dotenv` |
