"""
Tool 4: llm_analyzer.py
Sends source file chunks to the multi-agent system for AI analysis.
Handles chunking to stay within token limits.
"""

import os
from typing import List, Dict

# Max characters per chunk sent to LLM (~3000 tokens ≈ 12000 chars for safety)
MAX_CHUNK_CHARS = 10_000


def _read_file_content(path: str) -> str:
    """Read file content with encoding fallback."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _chunk_content(content: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """Split content into chunks of at most max_chars characters."""
    if len(content) <= max_chars:
        return [content]

    chunks = []
    start = 0
    while start < len(content):
        end = start + max_chars
        # Try to break at a newline boundary
        if end < len(content):
            newline_pos = content.rfind("\n", start, end)
            if newline_pos > start:
                end = newline_pos + 1
        chunks.append(content[start:end])
        start = end

    return chunks


def prepare_file_chunks(file_info: Dict) -> List[Dict]:
    """
    Read a file and split it into chunks for LLM analysis.

    Args:
        file_info: Enriched file dict from get_file_metrics().

    Returns:
        List of chunk dicts with keys: path, relative_path, language, content, chunk_index, total_chunks
    """
    content = _read_file_content(file_info["path"])
    if not content.strip():
        return []

    chunks = _chunk_content(content)
    total = len(chunks)

    return [
        {
            "path": file_info["path"],
            "relative_path": file_info.get("relative_path", file_info["path"]),
            "language": file_info.get("language", "unknown"),
            "filename": file_info.get("filename", os.path.basename(file_info["path"])),
            "content": chunk,
            "chunk_index": i + 1,
            "total_chunks": total,
        }
        for i, chunk in enumerate(chunks)
    ]


def build_analysis_prompt(chunk: Dict, agent_type: str) -> str:
    """
    Build the LLM prompt for a specific agent type and file chunk.

    Args:
        chunk: A chunk dict from prepare_file_chunks().
        agent_type: One of 'architecture', 'security', 'performance'.

    Returns:
        A formatted prompt string.
    """
    focus_map = {
        "architecture": (
            "architecture issues: Single Responsibility Principle violations, tight coupling, "
            "god objects, circular dependencies, missing abstraction layers, poor modularity"
        ),
        "security": (
            "security vulnerabilities: hardcoded secrets/credentials, SQL injection risks, "
            "unsafe deserialization, command injection, XSS vectors, missing input validation, "
            "use of deprecated/unsafe functions"
        ),
        "performance": (
            "performance problems: O(n²) or worse algorithmic complexity, repeated database calls "
            "in loops, memory leaks, synchronous blocking I/O in async context, unnecessary "
            "recomputation, missing caching"
        ),
    }

    focus = focus_map.get(agent_type, "general code quality issues")
    chunk_note = (
        f" (chunk {chunk['chunk_index']} of {chunk['total_chunks']})"
        if chunk["total_chunks"] > 1
        else ""
    )

    return f"""You are an expert code reviewer specializing in {agent_type} analysis.

Analyze the following {chunk['language']} code from file: {chunk['relative_path']}{chunk_note}

Focus ONLY on {focus}.

Return your findings as a JSON array. Each finding must have these exact keys:
- "type": short snake_case category (e.g. "hardcoded_secret", "god_object", "n_squared_loop")
- "message": clear explanation of the issue (2-3 sentences)
- "severity": one of "critical", "high", "medium", "low"
- "file": the filename "{chunk['relative_path']}"
- "line": line number (integer) if identifiable, otherwise null
- "root_cause": likely root cause in 1 sentence
- "recommended_fix": specific actionable fix in 2-3 sentences
- "evidence": the relevant code snippet (max 3 lines)

If you find NO issues, return an empty array: []

Return ONLY the JSON array, no markdown fences, no explanation.

CODE TO ANALYZE:
```{chunk['language']}
{chunk['content']}
```"""
