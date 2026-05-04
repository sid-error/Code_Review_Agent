"""
streamlit_app.py — Streamlit UI for the Code Review Agent.

Start with:
    streamlit run streamlit_app.py

Requires the Temporal worker to be running:
    python temporal/worker.py

And the Temporal dev server:
    temporal server start-dev
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Project path setup ────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from temporal.models import RunConfig
from temporal.client import get_client, TASK_QUEUE
from temporal.activities import PROGRESS_FILENAME
from tools.cache_manager import get_report_json_path

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Code Review Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* Dark card for findings */
.finding-card {
    background: #1e1e2e;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 8px 0;
    border-left: 4px solid #888;
    transition: box-shadow 0.2s;
}
.finding-card:hover { box-shadow: 0 4px 20px rgba(0,0,0,0.3); }

.sev-critical { border-left-color: #ff2d55 !important; }
.sev-high     { border-left-color: #ff6b35 !important; }
.sev-medium   { border-left-color: #ffd60a !important; }
.sev-low      { border-left-color: #30d158 !important; }
.sev-info     { border-left-color: #64d2ff !important; }

.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    margin-right: 6px;
}
.badge-critical { background:#ff2d5533; color:#ff2d55; }
.badge-high     { background:#ff6b3533; color:#ff6b35; }
.badge-medium   { background:#ffd60a33; color:#c9a800; }
.badge-low      { background:#30d15833; color:#30d158; }
.badge-info     { background:#64d2ff33; color:#64d2ff; }

.badge-heuristic { background:#6e40c933; color:#bf5af2; }
.badge-semgrep   { background:#0a84ff33; color:#0a84ff; }
.badge-ai        { background:#ff9f0a33; color:#ff9f0a; }

.metric-box {
    background: #1e1e2e;
    border-radius: 12px;
    padding: 18px;
    text-align: center;
    border: 1px solid #2c2c3e;
}
.metric-number { font-size: 2.2rem; font-weight: 700; line-height: 1; }
.metric-label  { font-size: 0.78rem; color: #888; margin-top: 4px; letter-spacing: 0.5px; text-transform: uppercase; }

code { font-family: 'JetBrains Mono', monospace; }
</style>
""", unsafe_allow_html=True)


# ── Session state defaults ────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "workflow_id": None,
        "run_active": False,
        "paused": False,
        "repo_path": "",
        "last_progress": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── Temporal helpers (sync wrappers) ──────────────────────────────────────────
def _run_async(coro):
    """Run an async coroutine from sync Streamlit code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=15)
        else:
            return loop.run_until_complete(coro)
    except Exception:
        return asyncio.run(coro)


async def _start_workflow(config: RunConfig) -> str:
    client = await get_client()
    wf_id = f"code-review-{uuid.uuid4().hex[:8]}"
    await client.start_workflow(
        "CodeReviewWorkflow",
        config,
        id=wf_id,
        task_queue=TASK_QUEUE,
    )
    return wf_id


async def _send_signal(workflow_id: str, signal: str):
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(signal)


async def _query_status(workflow_id: str) -> str:
    try:
        client = await get_client()
        handle = client.get_workflow_handle(workflow_id)
        return await handle.query("get_status")
    except Exception:
        return "unknown"


async def _cancel_workflow(workflow_id: str):
    try:
        client = await get_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.cancel()
    except Exception:
        pass


# ── Progress helpers ──────────────────────────────────────────────────────────
def _read_progress(repo_path: str) -> dict:
    path = os.path.join(os.path.abspath(repo_path), PROGRESS_FILENAME)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _read_report(repo_path: str) -> dict:
    json_path = get_report_json_path(repo_path)
    if not os.path.isfile(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


# ── Severity helpers ──────────────────────────────────────────────────────────
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEV_COLORS = {
    "critical": "#ff2d55",
    "high": "#ff6b35",
    "medium": "#ffd60a",
    "low": "#30d158",
    "info": "#64d2ff",
}
SOURCE_COLORS = {"heuristic": "#bf5af2", "semgrep": "#0a84ff", "ai": "#ff9f0a"}


def _badge(label: str, css_class: str) -> str:
    return f'<span class="badge {css_class}">{label}</span>'


def _sev_badge(sev: str) -> str:
    s = sev.lower()
    return _badge(s, f"badge-{s}")


def _source_badge(src: str) -> str:
    s = src.lower()
    return _badge(s, f"badge-{s}")


# ── Sidebar: configuration ────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Configuration")

    repo_path = st.text_input(
        "Repository Path",
        value=st.session_state.repo_path,
        placeholder=r"C:\Users\you\Projects\my-repo",
        help="Paste the full path to your repository. Windows backslashes are fine.",
        key="repo_path_input",
    )

    output_dir = st.text_input(
        "Report Output Directory",
        value=".",
        help="Directory where report.html will be written. report.json goes inside the repo.",
    )

    model = st.selectbox(
        "Gemini Model",
        ["gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro"],
        index=0,
    )

    no_ai = st.toggle("⚡ Heuristic-only mode (no AI)", value=False,
                      help="Skip Gemini analysis — faster, no API key needed.")
    full_scan = st.toggle("🔄 Full scan (ignore cache)", value=False,
                          help="Re-analyse all files even if unchanged.")

    st.divider()
    st.markdown("### 🧠 Temporal Worker")
    st.markdown(
        "Ensure the worker is running before starting:\n"
        "```\npython temporal/worker.py\n```"
    )
    st.markdown("And the Temporal server (Docker + Cassandra):\n```\ndocker compose up -d\n```")
    st.markdown("Temporal UI: [localhost:8080](http://localhost:8080)")


# ── Main header ───────────────────────────────────────────────────────────────
st.markdown("# 🔍 Code Review Agent")
st.markdown("*Powered by Google Gemini 2.5 Pro · Temporal · Semgrep · radon · bandit*")
st.divider()


# ── Run controls ──────────────────────────────────────────────────────────────
col_run, col_pause, col_stop = st.columns([2, 1, 1])

with col_run:
    run_clicked = st.button(
        "▶ Run Analysis",
        type="primary",
        disabled=st.session_state.run_active,
        use_container_width=True,
    )

with col_pause:
    if st.session_state.run_active:
        if st.session_state.paused:
            if st.button("▶ Resume", use_container_width=True, type="secondary"):
                _run_async(_send_signal(st.session_state.workflow_id, "resume"))
                st.session_state.paused = False
                st.rerun()
        else:
            if st.button("⏸ Pause", use_container_width=True, type="secondary"):
                _run_async(_send_signal(st.session_state.workflow_id, "pause"))
                st.session_state.paused = True
                st.rerun()
    else:
        st.button("⏸ Pause", use_container_width=True, disabled=True)

with col_stop:
    if st.session_state.run_active:
        if st.button("⏹ Stop", use_container_width=True):
            _run_async(_cancel_workflow(st.session_state.workflow_id))
            st.session_state.run_active = False
            st.session_state.workflow_id = None
            st.session_state.paused = False
            st.rerun()
    else:
        st.button("⏹ Stop", use_container_width=True, disabled=True)


# ── Start workflow ────────────────────────────────────────────────────────────
if run_clicked:
    # Read directly from session state key to avoid sidebar-scope issues
    raw_path = st.session_state.get("repo_path_input", "").strip()
    # Strip surrounding quotes (common when copy-pasting Windows paths)
    raw_path = raw_path.strip('"').strip("'").strip()
    # Expand env vars and ~ then normalise separators
    repo_path = os.path.normpath(os.path.expandvars(os.path.expanduser(raw_path)))

    if not raw_path:
        st.error("Please enter a repository path.")
    elif not os.path.isdir(repo_path):
        st.error(
            f"Directory not found: `{repo_path}`\n\n"
            f"Please check the path exists and is accessible. "
            f"You can copy the full path from File Explorer's address bar."
        )
    else:
        st.session_state.repo_path = repo_path
        config = RunConfig(
            repo_path=repo_path,
            output_dir=output_dir,
            model=model,
            no_ai=no_ai,
            full_scan=full_scan,
        )
        try:
            wf_id = _run_async(_start_workflow(config))
            st.session_state.workflow_id = wf_id
            st.session_state.run_active = True
            st.session_state.paused = False
            st.success(f"Workflow started: `{wf_id}`")
            time.sleep(0.5)
            st.rerun()
        except Exception as e:
            st.error(f"Failed to start workflow: {e}\n\nIs the Temporal worker running?")



# ── Live progress panel ───────────────────────────────────────────────────────
if st.session_state.run_active and st.session_state.repo_path:
    repo = st.session_state.repo_path
    progress = _read_progress(repo)

    st.divider()
    st.markdown("### ⏳ Live Progress")

    if progress:
        step = progress.get("step", 1)
        total = progress.get("total_steps", 7)
        phase = progress.get("phase", "…")
        status = progress.get("status", "running")
        detail = progress.get("detail", "")

        pct = min(step / total, 1.0)
        if status == "done" and step == total:
            pct = 1.0

        st.progress(pct, text=f"Step {step}/{total}: {phase}")

        if detail:
            st.caption(f"ℹ️ {detail}")

        if st.session_state.paused:
            st.warning("⏸ Analysis is **paused**. Click Resume to continue.")

        # Step indicators
        step_labels = [
            "1. Scan Repo",
            "2. File Metrics",
            "3. Heuristic Analysis",
            "4. Semgrep",
            "5. AI Analysis",
            "6. Merge & Report",
            "7. Save Cache",
        ]
        cols = st.columns(len(step_labels))
        for i, (col, label) in enumerate(zip(cols, step_labels)):
            s_num = i + 1
            with col:
                if s_num < step:
                    st.markdown(f"✅ **{label}**")
                elif s_num == step:
                    if status == "running":
                        st.markdown(f"🔄 **{label}**")
                    elif status == "paused":
                        st.markdown(f"⏸ **{label}**")
                    else:
                        st.markdown(f"✅ **{label}**")
                else:
                    st.markdown(f"⬜ {label}")

        # Check if done
        wf_status = _run_async(_query_status(st.session_state.workflow_id)) if st.session_state.workflow_id else "unknown"
        if wf_status == "done" or (status == "done" and step == total):
            st.session_state.run_active = False
            st.session_state.paused = False
            st.success("✅ Analysis complete! Report is ready below.")
            time.sleep(0.3)
            st.rerun()
        else:
            # Auto-refresh every 2 seconds while running
            time.sleep(2)
            st.rerun()
    else:
        st.info("Waiting for first progress update from worker…")
        time.sleep(2)
        st.rerun()


# ── Inline report ─────────────────────────────────────────────────────────────
repo_for_report = st.session_state.repo_path
if repo_for_report and not st.session_state.run_active:
    report = _read_report(repo_for_report)
    if report and report.get("findings"):
        st.divider()
        st.markdown(f"## 📊 Audit Report — `{report.get('repo', '')}`")
        st.caption(f"Generated: {report.get('generated_at', '')}  |  "
                   f"Files scanned: {report.get('total_files_scanned', 0)}  |  "
                   f"Total issues: {report.get('total_issues', 0)}")

        # ── Summary metrics ───────────────────────────────────────────────────
        counts = report.get("severity_counts", {})
        m_cols = st.columns(5)
        for col, (sev, color) in zip(m_cols, [
            ("critical", "#ff2d55"), ("high", "#ff6b35"),
            ("medium", "#ffd60a"), ("low", "#30d158"), ("info", "#64d2ff"),
        ]):
            with col:
                st.markdown(
                    f'<div class="metric-box">'
                    f'<div class="metric-number" style="color:{color}">{counts.get(sev, 0)}</div>'
                    f'<div class="metric-label">{sev.upper()}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ── Source breakdown ──────────────────────────────────────────────────
        findings = report.get("findings", [])
        src_counts = {}
        for f in findings:
            s = f.get("source", "unknown")
            src_counts[s] = src_counts.get(s, 0) + 1

        st.divider()
        st.markdown("#### Source Breakdown")
        s_cols = st.columns(len(src_counts) or 1)
        for col, (src, cnt) in zip(s_cols, src_counts.items()):
            color = SOURCE_COLORS.get(src.lower(), "#888")
            with col:
                st.markdown(
                    f'<div class="metric-box">'
                    f'<div class="metric-number" style="color:{color}">{cnt}</div>'
                    f'<div class="metric-label">{src.upper()}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ── Token usage ───────────────────────────────────────────────────────
        token_usage = report.get("token_usage")
        if token_usage and token_usage.get("total_tokens"):
            st.divider()
            t_cols = st.columns(3)
            with t_cols[0]:
                st.metric("Prompt Tokens", f"{token_usage.get('prompt_tokens', 0):,}")
            with t_cols[1]:
                st.metric("Response Tokens", f"{token_usage.get('candidates_tokens', 0):,}")
            with t_cols[2]:
                st.metric("Total Tokens", f"{token_usage.get('total_tokens', 0):,}")

        # ── Filters ───────────────────────────────────────────────────────────
        st.divider()
        st.markdown("#### 🔎 Filter Findings")
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            sev_filter = st.multiselect(
                "Severity",
                ["critical", "high", "medium", "low", "info"],
                default=["critical", "high", "medium", "low", "info"],
            )
        with f_col2:
            src_filter = st.multiselect(
                "Source",
                list(src_counts.keys()),
                default=list(src_counts.keys()),
            )
        with f_col3:
            search_term = st.text_input("Search in message/file", placeholder="e.g. sql_injection")

        # ── Findings list ─────────────────────────────────────────────────────
        filtered = [
            f for f in findings
            if f.get("severity", "info") in sev_filter
            and f.get("source", "unknown") in src_filter
            and (not search_term or search_term.lower() in (
                f.get("message", "") + f.get("file", "") + f.get("type", "")
            ).lower())
        ]

        st.markdown(f"**Showing {len(filtered)} of {len(findings)} findings**")

        for idx, finding in enumerate(filtered):
            sev = finding.get("severity", "info").lower()
            src = finding.get("source", "unknown").lower()
            ftype = finding.get("type", "unknown")
            ffile = finding.get("file", "")
            line = finding.get("line")
            msg = finding.get("message", "")
            root_cause = finding.get("root_cause", "")
            fix = finding.get("recommended_fix", "")
            evidence = finding.get("evidence", "")

            loc = f"{ffile}" + (f":{line}" if line else "")
            label = f"{ftype} — {loc}"

            with st.expander(label, expanded=False):
                st.markdown(
                    _sev_badge(sev) + _source_badge(src),
                    unsafe_allow_html=True,
                )
                st.markdown(f"**📄 File:** `{loc}`")
                st.markdown(f"**📝 Issue:** {msg}")
                if root_cause:
                    st.markdown(f"**🔍 Root Cause:** {root_cause}")
                if fix:
                    st.markdown(f"**🔧 Recommended Fix:** {fix}")
                if evidence:
                    st.code(evidence, language="python")

        # ── Download button ───────────────────────────────────────────────────
        st.divider()
        json_path = get_report_json_path(repo_for_report)
        if os.path.isfile(json_path):
            with open(json_path, "r", encoding="utf-8") as fh:
                raw_json = fh.read()
            st.download_button(
                label="⬇ Download report.json",
                data=raw_json,
                file_name="report.json",
                mime="application/json",
            )

    elif repo_for_report and not st.session_state.run_active:
        st.info("No report found yet. Run an analysis to generate one.")
