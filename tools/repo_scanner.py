"""
Tool 1: repo_scanner.py
Walks a repository directory and collects all supported source files.
Supports: .py, .js, .ts files.
"""

import os
from typing import List, Dict


SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
}


def scan_repo(repo_path: str) -> List[Dict]:
    """
    Walk a repository directory and return metadata for each supported source file.

    Args:
        repo_path: Absolute or relative path to the repository root.

    Returns:
        A list of dicts: [{path, language, extension}]
    """
    if not os.path.isdir(repo_path):
        raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")

    files = []
    # Directories to skip
    skip_dirs = {
        ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv",
        "env", "dist", "build", ".next", ".tox", ".eggs", "*.egg-info",
    }

    for root, dirs, filenames in os.walk(repo_path):
        # Prune skip directories in-place so os.walk doesn't recurse into them
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, repo_path)
                files.append({
                    "path": full_path,
                    "relative_path": rel_path.replace("\\", "/"),
                    "language": SUPPORTED_EXTENSIONS[ext],
                    "extension": ext,
                    "filename": filename,
                })

    return files
