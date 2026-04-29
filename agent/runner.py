"""
agent/runner.py -- ADK Runner wrapper for the Code Review pipeline.

Provides CodeReviewRunner, which:
  - Creates a fresh ADK InMemorySession per analysis run
  - Dispatches each file chunk to the orchestrator agent via the ADK Runner
  - Streams and accumulates the final model response text
  - Parses the JSON findings array from the response
  - Tags each finding with source metadata
  - Returns a flat list of validated issue dicts
"""

import asyncio
import json
from typing import List, Dict, Optional, Callable

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from agent.orchestrator.agent import orchestrator
from tools.llm_analyzer import prepare_file_chunks

load_dotenv()

APP_NAME = "code_review_agent"
USER_ID = "pipeline"

# Fields every valid issue dict must have
REQUIRED_FIELDS = {"type", "message", "severity", "file"}
VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:] if len(lines) > 1 else []
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    return text


def _extract_json_array(text: str) -> list:
    """
    Extract and parse the outermost JSON array from model output.
    Returns [] on any failure.
    """
    text = _strip_fences(text)

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []

    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _validate_and_tag(findings: list, relative_path: str) -> List[Dict]:
    """
    Validate each finding dict and tag it with source=ai.
    Drops malformed entries.
    """
    valid = []
    for f in findings:
        if not isinstance(f, dict):
            continue

        # Ensure required fields are present
        if not REQUIRED_FIELDS.issubset(f.keys()):
            continue

        # Normalise severity
        sev = str(f.get("severity", "low")).lower()
        if sev not in VALID_SEVERITIES:
            f["severity"] = "low"
        else:
            f["severity"] = sev

        # Ensure file path is set
        if not f.get("file"):
            f["file"] = relative_path

        # Tag source as ai
        f["source"] = "ai"

        valid.append(f)

    return valid


# ---------------------------------------------------------------------------
# Main runner class
# ---------------------------------------------------------------------------

class CodeReviewRunner:
    """
    ADK-based runner that dispatches file chunks through the orchestrator
    agent and collects structured findings.
    """

    def __init__(self):
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            agent=orchestrator,
            app_name=APP_NAME,
            session_service=self._session_service,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_files(
        self,
        files: List[Dict],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Dict]:
        """
        Synchronous entry point -- wraps the async implementation.

        Args:
            files: Enriched file dicts from get_metrics_for_all().
            progress_callback: Optional callable(current, total, filename).

        Returns:
            Flat list of all AI issue dicts.
        """
        return asyncio.run(self._analyze_files_async(files, progress_callback))

    # ------------------------------------------------------------------
    # Internal async implementation
    # ------------------------------------------------------------------

    async def _analyze_files_async(
        self,
        files: List[Dict],
        progress_callback: Optional[Callable] = None,
    ) -> List[Dict]:
        all_findings: List[Dict] = []
        total = len(files)

        session = await self._session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
        )
        session_id = session.id

        for idx, file_info in enumerate(files):
            filename = file_info.get("relative_path", file_info.get("path", ""))

            if progress_callback:
                progress_callback(idx + 1, total, filename)

            # Skip trivially small files (< 5 lines) -- nothing meaningful to review
            if file_info.get("line_count", 0) < 5:
                continue

            chunks = prepare_file_chunks(file_info)
            if not chunks:
                continue

            for chunk in chunks:
                findings = await self._analyze_chunk(session_id, chunk)
                all_findings.extend(findings)

        return all_findings

    async def _analyze_chunk(self, session_id: str, chunk: Dict) -> List[Dict]:
        """
        Send one file chunk to the orchestrator via ADK Runner and parse findings.
        """
        chunk_note = (
            f" (chunk {chunk['chunk_index']} of {chunk['total_chunks']})"
            if chunk["total_chunks"] > 1
            else ""
        )
        user_message = (
            f"Review this {chunk['language']} code from file: "
            f"{chunk['relative_path']}{chunk_note}\n\n"
            f"```{chunk['language']}\n{chunk['content']}\n```"
        )

        content_msg = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=user_message)],
        )

        # Accumulate all text parts from all events
        collected_text = ""
        try:
            async for event in self._runner.run_async(
                session_id=session_id,
                user_id=USER_ID,
                new_message=content_msg,
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            collected_text += part.text
        except Exception:
            return []

        if not collected_text.strip():
            return []

        raw_findings = _extract_json_array(collected_text)
        return _validate_and_tag(raw_findings, chunk["relative_path"])
