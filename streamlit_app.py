"""
streamlit_app.py — Streamlit UI for the Code Review Agent.

Features
--------
* Repository path input with Windows-path normalisation
* Persistent run history (survives page reload / close + reopen)
* Pause/Resume AI analysis via Temporal signals
* Live progress tracking with auto-refresh
* Inline audit report viewer (severity + source filtering)
* HTML and JSON report download buttons
* Re-run / Delete any past run

Usage
-----
    # 1. Start Temporal + PostgreSQL
    docker compose up -d

    # 2. Start the Temporal worker
    python temporal/worker.py

    # 3. Start Streamlit
    streamlit run streamlit_app.py
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── project path ──────────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from temporal.models import RunConfig
from temporal.client import get_client, TASK_QUEUE
from temporal.activities import PROGRESS_FILENAME
from tools.cache_manager import get_report_json_path
from tools.run_registry import (
    load_runs, add_run, update_run, delete_run, get_run,
)

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Code Review Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

.finding-card {
    background: #1e1e2e; border-radius: 10px; padding: 16px 20px;
    margin: 8px 0; border-left: 4px solid #888; transition: box-shadow 0.2s;
}
.finding-card:hover { box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
.sev-critical { border-left-color: #ff2d55 !important; }
.sev-high     { border-left-color: #ff6b35 !important; }
.sev-medium   { border-left-color: #ffd60a !important; }
.sev-low      { border-left-color: #30d158 !important; }
.sev-info     { border-left-color: #64d2ff !important; }

.badge {
    display: inline-block; padding: 2px 10px; border-radius: 20px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.5px;
    text-transform: uppercase; margin-right: 6px;
}
.badge-critical { background:#ff2d5533; color:#ff2d55; }
.badge-high     { background:#ff6b3533; color:#ff6b35; }
.badge-medium   { background:#ffd60a33; color:#c9a800; }
.badge-low      { background:#30d15833; color:#30d158; }
.badge-info     { background:#64d2ff33; color:#64d2ff; }
.badge-heuristic{ background:#6e40c933; color:#bf5af2; }
.badge-semgrep  { background:#0a84ff33; color:#0a84ff; }
.badge-ai       { background:#ff9f0a33; color:#ff9f0a; }

.metric-box {
    background: #1e1e2e; border-radius: 12px; padding: 18px;
    text-align: center; border: 1px solid #2c2c3e;
}
.metric-number { font-size: 2.2rem; font-weight: 700; line-height: 1; }
.metric-label  { font-size: 0.78rem; color: #888; margin-top: 4px;
                 letter-spacing: 0.5px; text-transform: uppercase; }
.run-card {
    background: #1e1e2e; border-radius: 10px; padding: 14px 18px;
    margin: 6px 0; border: 1px solid #2c2c3e;
}
code { font-family: 'JetBrains Mono', monospace; }
</style>
""", unsafe_allow_html=True)


# ── session state defaults ────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "active_wf_id": None,   # currently tracked workflow
        "active_repo": "",
        "active_paused": False,
        "run_active": False,
        "view_wf_id": None,     # which run's report to show
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── async helpers ─────────────────────────────────────────────────────────────
def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result(timeout=15)
        return loop.run_until_complete(coro)
    except Exception:
        return asyncio.run(coro)


async def _start_workflow(config: RunConfig) -> str:
    client = await get_client()
    wf_id = f"code-review-{uuid.uuid4().hex[:8]}"
    await client.start_workflow(
        "CodeReviewWorkflow", config, id=wf_id, task_queue=TASK_QUEUE,
    )
    return wf_id


async def _signal(wf_id: str, sig: str):
    client = await get_client()
    await client.get_workflow_handle(wf_id).signal(sig)


async def _cancel(wf_id: str):
    try:
        client = await get_client()
        await client.get_workflow_handle(wf_id).cancel()
    except Exception:
        pass


async def _temporal_status(wf_id: str) -> str:
    """Map Temporal execution status → our string."""
    try:
        from temporalio.client import WorkflowExecutionStatus as WES
        client = await get_client()
        desc = await client.get_workflow_handle(wf_id).describe()
        return {
            WES.COMPLETED: "done",
            WES.RUNNING: "running",
            WES.FAILED: "failed",
            WES.CANCELLED: "cancelled",
            WES.TIMED_OUT: "timed_out",
            WES.TERMINATED: "cancelled",
        }.get(desc.status, "unknown")
    except Exception:
        return "unknown"


# ── progress / report helpers ─────────────────────────────────────────────────
def _read_progress(repo_path: str) -> Dict:
    p = os.path.join(os.path.abspath(repo_path), PROGRESS_FILENAME)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_report(repo_path: str) -> Dict:
    jp = get_report_json_path(repo_path)
    if not os.path.isfile(jp):
        return {}
    try:
        with open(jp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── badge helpers ─────────────────────────────────────────────────────────────
SOURCE_COLORS = {"heuristic": "#bf5af2", "semgrep": "#0a84ff", "ai": "#ff9f0a"}

def _badge(label, cls): return f'<span class="badge {cls}">{label}</span>'
def _sev_badge(s): s=s.lower(); return _badge(s, f"badge-{s}")
def _src_badge(s): s=s.lower(); return _badge(s, f"badge-{s}")

STATUS_META = {
    "running":   ("🔄", "#0a84ff"),
    "paused":    ("⏸",  "#ffd60a"),
    "done":      ("✅", "#30d158"),
    "failed":    ("❌", "#ff2d55"),
    "cancelled": ("🚫", "#888"),
    "timed_out": ("⏱",  "#ff6b35"),
    "unknown":   ("❓", "#888"),
}

def _status_html(status: str) -> str:
    icon, color = STATUS_META.get(status, ("❓", "#888"))
    return (f'<span style="background:{color}22;color:{color};padding:2px 10px;'
            f'border-radius:20px;font-size:11px;font-weight:600">'
            f'{icon} {status.upper()}</span>')


# ── inline report renderer ────────────────────────────────────────────────────
def _render_report(repo_path: str, html_path: Optional[str] = None):
    report = _read_report(repo_path)
    if not report or not report.get("findings"):
        st.info("No report found. Run an analysis first.")
        return

    st.markdown(f"### 📊 `{report.get('repo', os.path.basename(repo_path))}`")
    st.caption(
        f"Generated: {report.get('generated_at', '—')}  |  "
        f"Files: {report.get('total_files_scanned', 0)}  |  "
        f"Issues: {report.get('total_issues', 0)}"
    )

    # ── download buttons ──────────────────────────────────────────────────────
    dc1, dc2, _ = st.columns([1, 1, 4])
    with dc1:
        jp = get_report_json_path(repo_path)
        if os.path.isfile(jp):
            with open(jp, encoding="utf-8") as f:
                st.download_button("⬇ report.json", f.read(),
                                   "report.json", "application/json",
                                   use_container_width=True)
    with dc2:
        hp = html_path or ""
        if os.path.isfile(hp):
            with open(hp, encoding="utf-8", errors="replace") as f:
                st.download_button("⬇ report.html", f.read(),
                                   "report.html", "text/html",
                                   use_container_width=True)

    # ── severity metrics ──────────────────────────────────────────────────────
    counts = report.get("severity_counts", {})
    sev_items = [("critical","#ff2d55"),("high","#ff6b35"),
                 ("medium","#ffd60a"),("low","#30d158"),("info","#64d2ff")]
    m_cols = st.columns(5)
    for col, (sev, color) in zip(m_cols, sev_items):
        with col:
            st.markdown(
                f'<div class="metric-box">'
                f'<div class="metric-number" style="color:{color}">{counts.get(sev,0)}</div>'
                f'<div class="metric-label">{sev.upper()}</div></div>',
                unsafe_allow_html=True)

    # ── source breakdown ──────────────────────────────────────────────────────
    findings = report.get("findings", [])
    src_counts: Dict[str, int] = {}
    for f in findings:
        s = f.get("source", "unknown")
        src_counts[s] = src_counts.get(s, 0) + 1

    st.divider()
    s_cols = st.columns(max(len(src_counts), 1))
    for col, (src, cnt) in zip(s_cols, src_counts.items()):
        color = SOURCE_COLORS.get(src.lower(), "#888")
        with col:
            st.markdown(
                f'<div class="metric-box">'
                f'<div class="metric-number" style="color:{color}">{cnt}</div>'
                f'<div class="metric-label">{src.upper()}</div></div>',
                unsafe_allow_html=True)

    # ── token usage ───────────────────────────────────────────────────────────
    tu = report.get("token_usage") or {}
    if tu.get("total_tokens"):
        st.divider()
        t1, t2, t3 = st.columns(3)
        t1.metric("Prompt Tokens",   f"{tu.get('prompt_tokens',0):,}")
        t2.metric("Response Tokens", f"{tu.get('candidates_tokens',0):,}")
        t3.metric("Total Tokens",    f"{tu.get('total_tokens',0):,}")

    # ── filters ───────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 🔎 Filter Findings")
    fc1, fc2, fc3 = st.columns(3)
    sev_filter = fc1.multiselect("Severity",
        ["critical","high","medium","low","info"],
        default=["critical","high","medium","low","info"])
    src_filter = fc2.multiselect("Source", list(src_counts.keys()),
                                  default=list(src_counts.keys()))
    search = fc3.text_input("Search message / file")

    filtered = [
        f for f in findings
        if f.get("severity","info") in sev_filter
        and f.get("source","unknown") in src_filter
        and (not search or search.lower() in (
            f.get("message","") + f.get("file","") + f.get("type","")
        ).lower())
    ]
    st.markdown(f"**Showing {len(filtered)} of {len(findings)} findings**")

    for finding in filtered:
        sev = finding.get("severity","info").lower()
        src = finding.get("source","unknown").lower()
        ftype = finding.get("type","unknown")
        ffile = finding.get("file","")
        line  = finding.get("line")
        loc   = ffile + (f":{line}" if line else "")
        label = f"{ftype} — {loc}"
        with st.expander(label, expanded=False):
            st.markdown(_sev_badge(sev) + _src_badge(src), unsafe_allow_html=True)
            st.markdown(f"**📄 File:** `{loc}`")
            st.markdown(f"**📝 Issue:** {finding.get('message','')}")
            if finding.get("root_cause"):
                st.markdown(f"**🔍 Root Cause:** {finding['root_cause']}")
            if finding.get("recommended_fix"):
                st.markdown(f"**🔧 Fix:** {finding['recommended_fix']}")
            if finding.get("evidence"):
                st.code(finding["evidence"], language="python")


# ── progress panel ────────────────────────────────────────────────────────────
def _render_progress(wf_id: str, repo_path: str, paused: bool):
    progress = _read_progress(repo_path)
    st.markdown("#### ⏳ Live Progress")
    if not progress:
        st.info("Waiting for first progress update from worker…")
        return False  # not done

    step   = progress.get("step", 1)
    total  = progress.get("total_steps", 7)
    phase  = progress.get("phase", "…")
    status = progress.get("status", "running")
    detail = progress.get("detail", "")

    pct = min(step / total, 1.0)
    if status == "done" and step == total:
        pct = 1.0

    st.progress(pct, text=f"Step {step}/{total}: {phase}")
    if detail:
        st.caption(f"ℹ️ {detail}")
    if paused:
        st.warning("⏸ Analysis is **paused**. Click Resume to continue.")

    step_labels = ["1. Scan","2. Metrics","3. Heuristics",
                   "4. Semgrep","5. AI Analysis","6. Report","7. Cache"]
    cols = st.columns(len(step_labels))
    for i, (col, lbl) in enumerate(zip(cols, step_labels)):
        n = i + 1
        with col:
            if n < step:      st.markdown(f"✅ **{lbl}**")
            elif n == step:
                if status == "running": st.markdown(f"🔄 **{lbl}**")
                elif status == "paused": st.markdown(f"⏸ **{lbl}**")
                else:                   st.markdown(f"✅ **{lbl}**")
            else:             st.markdown(f"⬜ {lbl}")

    # check if done
    wf_status = _run_async(_temporal_status(wf_id))
    return wf_status == "done" or (status == "done" and step == total)


# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ New Analysis")

    repo_input = st.text_input(
        "Repository Path",
        value="",
        placeholder=r"C:\Users\you\Projects\my-repo",
        help="Paste the full path. Windows backslashes are fine.",
        key="repo_path_input",
    )
    output_dir = st.text_input(
        "Report Output Directory",
        value=_PROJECT_ROOT,
        help="Directory for report.html. report.json goes inside the repo.",
    )
    model = st.selectbox("Gemini Model",
        ["gemini-2.5-pro","gemini-2.0-flash","gemini-1.5-pro"])
    no_ai     = st.toggle("⚡ Heuristic-only (no AI)", value=False)
    full_scan = st.toggle("🔄 Full scan (ignore cache)", value=False)

    run_clicked = st.button("▶ Run Analysis", type="primary",
                            disabled=st.session_state.run_active,
                            use_container_width=True)

    st.divider()
    st.markdown("### 🧠 Prerequisites")
    st.markdown("```\ndocker compose up -d\n```")
    st.markdown("```\npython temporal/worker.py\n```")
    st.markdown("Temporal UI → [localhost:8080](http://localhost:8080)")


# ── main header ───────────────────────────────────────────────────────────────
st.markdown("# 🔍 Code Review Agent")
st.markdown("*Powered by Gemini · Temporal · Semgrep · radon · bandit*")
st.divider()


# ── start workflow ────────────────────────────────────────────────────────────
if run_clicked:
    raw = st.session_state.get("repo_path_input", "").strip().strip('"').strip("'").strip()
    repo_path = os.path.normpath(os.path.expandvars(os.path.expanduser(raw))) if raw else ""
    if not raw:
        st.error("Please enter a repository path.")
    elif not os.path.isdir(repo_path):
        st.error(f"Directory not found: `{repo_path}`")
    else:
        config = RunConfig(repo_path=repo_path, output_dir=output_dir,
                           model=model, no_ai=no_ai, full_scan=full_scan)
        try:
            wf_id = _run_async(_start_workflow(config))
            add_run(wf_id, repo_path, output_dir, model, no_ai, full_scan)
            st.session_state.active_wf_id   = wf_id
            st.session_state.active_repo    = repo_path
            st.session_state.active_paused  = False
            st.session_state.run_active     = True
            st.session_state.view_wf_id     = wf_id
            st.success(f"Workflow started: `{wf_id}`")
            time.sleep(0.4)
            st.rerun()
        except Exception as e:
            st.error(f"Failed to start: {e}\n\nIs the Temporal worker running?")


# ── active run controls ───────────────────────────────────────────────────────
if st.session_state.run_active and st.session_state.active_wf_id:
    wf_id  = st.session_state.active_wf_id
    repo   = st.session_state.active_repo
    paused = st.session_state.active_paused

    with st.container():
        st.markdown(f"### 🏃 Active: `{os.path.basename(repo)}`")
        c1, c2, c3 = st.columns(3)
        with c1:
            if paused:
                if st.button("▶ Resume", use_container_width=True):
                    _run_async(_signal(wf_id, "resume"))
                    st.session_state.active_paused = False
                    update_run(wf_id, status="running")
                    st.rerun()
            else:
                if st.button("⏸ Pause", use_container_width=True):
                    _run_async(_signal(wf_id, "pause"))
                    st.session_state.active_paused = True
                    update_run(wf_id, status="paused")
                    st.rerun()
        with c2:
            if st.button("⏹ Stop", use_container_width=True):
                _run_async(_cancel(wf_id))
                update_run(wf_id, status="cancelled",
                           finished_at=datetime.now().isoformat())
                st.session_state.run_active    = False
                st.session_state.active_wf_id = None
                st.rerun()
        with c3:
            st.markdown(_status_html("paused" if paused else "running"),
                        unsafe_allow_html=True)

        done = _render_progress(wf_id, repo, paused)
        if done:
            update_run(wf_id, status="done",
                       finished_at=datetime.now().isoformat())
            st.session_state.run_active    = False
            st.session_state.active_wf_id = None
            st.success("✅ Analysis complete!")
            time.sleep(0.3)
            st.rerun()
        else:
            time.sleep(2)
            st.rerun()

    st.divider()


# ── run history ───────────────────────────────────────────────────────────────
st.markdown("## 🗂 Run History")

runs = load_runs()
if not runs:
    st.info("No runs yet. Enter a repository path in the sidebar and click **▶ Run Analysis**.")
else:
    # Sync Temporal status for runs that appear still active in the registry
    synced = False
    for run in runs:
        if run["status"] in ("running", "paused"):
            ts = _run_async(_temporal_status(run["workflow_id"]))
            if ts in ("done", "failed", "cancelled", "timed_out") and ts != run["status"]:
                update_run(run["workflow_id"], status=ts,
                           finished_at=datetime.now().isoformat())
                run["status"] = ts
                synced = True
    if synced:
        runs = load_runs()   # reload after updates

    for run in runs:
        wf_id   = run["workflow_id"]
        rname   = run.get("repo_name", wf_id)
        status  = run.get("status", "unknown")
        started = run.get("started_at", "")[:19].replace("T", " ")
        model_s = run.get("model", "")
        flags   = []
        if run.get("no_ai"):     flags.append("no-AI")
        if run.get("full_scan"): flags.append("full-scan")
        flag_s  = " · ".join(flags) if flags else ""

        header = (f"**{rname}** &nbsp; {_status_html(status)} &nbsp; "
                  f"`{started}` &nbsp; `{model_s}`"
                  + (f" &nbsp; _{flag_s}_" if flag_s else ""))

        is_active = (wf_id == st.session_state.active_wf_id)
        expand = is_active or (wf_id == st.session_state.view_wf_id)

        with st.expander(f"{rname}  ·  {started}  ·  {status.upper()}",
                         expanded=expand):
            st.markdown(header, unsafe_allow_html=True)
            st.caption(f"Workflow ID: `{wf_id}`  |  Repo: `{run.get('repo_path','')}`")

            # ── action buttons ────────────────────────────────────────────────
            btn_cols = st.columns(4)

            # Resume (only for paused runs not currently active in session)
            with btn_cols[0]:
                if status == "paused" and not is_active:
                    if st.button("▶ Resume", key=f"res_{wf_id}",
                                 use_container_width=True):
                        _run_async(_signal(wf_id, "resume"))
                        update_run(wf_id, status="running")
                        st.session_state.active_wf_id  = wf_id
                        st.session_state.active_repo   = run["repo_path"]
                        st.session_state.active_paused = False
                        st.session_state.run_active    = True
                        st.session_state.view_wf_id    = wf_id
                        st.rerun()
                elif status == "running" and not is_active:
                    if st.button("🔗 Attach", key=f"att_{wf_id}",
                                 use_container_width=True):
                        st.session_state.active_wf_id  = wf_id
                        st.session_state.active_repo   = run["repo_path"]
                        st.session_state.active_paused = False
                        st.session_state.run_active    = True
                        st.session_state.view_wf_id    = wf_id
                        st.rerun()

            # Re-run
            with btn_cols[1]:
                if not st.session_state.run_active:
                    if st.button("🔁 Re-run", key=f"rer_{wf_id}",
                                 use_container_width=True):
                        rp = run["repo_path"]
                        if not os.path.isdir(rp):
                            st.error(f"Repo no longer exists: {rp}")
                        else:
                            cfg = RunConfig(
                                repo_path=rp,
                                output_dir=run.get("output_dir", _PROJECT_ROOT),
                                model=run.get("model", "gemini-2.5-pro"),
                                no_ai=run.get("no_ai", False),
                                full_scan=run.get("full_scan", False),
                            )
                            try:
                                new_id = _run_async(_start_workflow(cfg))
                                add_run(new_id, rp, cfg.output_dir,
                                        cfg.model, cfg.no_ai, cfg.full_scan)
                                st.session_state.active_wf_id  = new_id
                                st.session_state.active_repo   = rp
                                st.session_state.active_paused = False
                                st.session_state.run_active    = True
                                st.session_state.view_wf_id    = new_id
                                st.rerun()
                            except Exception as exc:
                                st.error(str(exc))

            # View report toggle
            with btn_cols[2]:
                if status == "done":
                    label = "🔼 Hide" if st.session_state.view_wf_id == wf_id else "📄 View"
                    if st.button(label, key=f"view_{wf_id}",
                                 use_container_width=True):
                        st.session_state.view_wf_id = (
                            None if st.session_state.view_wf_id == wf_id else wf_id
                        )
                        st.rerun()

            # Delete
            with btn_cols[3]:
                if st.button("🗑 Delete", key=f"del_{wf_id}",
                             use_container_width=True):
                    delete_run(wf_id)
                    if st.session_state.view_wf_id == wf_id:
                        st.session_state.view_wf_id = None
                    if st.session_state.active_wf_id == wf_id:
                        st.session_state.active_wf_id = None
                        st.session_state.run_active   = False
                    st.rerun()

            # ── inline report (when "View" is active) ─────────────────────────
            if status == "done" and st.session_state.view_wf_id == wf_id:
                st.divider()
                _render_report(run["repo_path"], run.get("html_path"))
