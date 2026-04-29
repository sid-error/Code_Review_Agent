"""
tests/test_scanner.py — Unit tests for repo_scanner.py
"""

import os
import tempfile
import pytest
from tools.repo_scanner import scan_repo


@pytest.fixture
def sample_repo(tmp_path):
    """Create a temporary repo with known file structure."""
    (tmp_path / "app.py").write_text("print('hello')")
    (tmp_path / "utils.js").write_text("console.log('hi');")
    (tmp_path / "types.ts").write_text("export type Foo = string;")
    (tmp_path / "README.md").write_text("# Readme")  # should be ignored
    (tmp_path / "data.json").write_text("{}")          # should be ignored
    node_mod = tmp_path / "node_modules"
    node_mod.mkdir()
    (node_mod / "lib.js").write_text("// ignored")    # should be ignored
    return tmp_path


def test_scan_repo_finds_supported_files(sample_repo):
    files = scan_repo(str(sample_repo))
    extensions = {f["extension"] for f in files}
    assert ".py" in extensions
    assert ".js" in extensions
    assert ".ts" in extensions


def test_scan_repo_ignores_unsupported(sample_repo):
    files = scan_repo(str(sample_repo))
    extensions = {f["extension"] for f in files}
    assert ".md" not in extensions
    assert ".json" not in extensions


def test_scan_repo_ignores_node_modules(sample_repo):
    files = scan_repo(str(sample_repo))
    paths = [f["relative_path"] for f in files]
    assert not any("node_modules" in p for p in paths)


def test_scan_repo_language_detection(sample_repo):
    files = scan_repo(str(sample_repo))
    lang_map = {f["extension"]: f["language"] for f in files}
    assert lang_map[".py"] == "python"
    assert lang_map[".js"] == "javascript"
    assert lang_map[".ts"] == "typescript"


def test_scan_repo_invalid_path():
    with pytest.raises(ValueError):
        scan_repo("/nonexistent/path/that/does/not/exist")


def test_scan_repo_empty_dir(tmp_path):
    files = scan_repo(str(tmp_path))
    assert files == []


def test_scan_repo_file_info_keys(sample_repo):
    files = scan_repo(str(sample_repo))
    required_keys = {"path", "relative_path", "language", "extension", "filename"}
    for f in files:
        assert required_keys.issubset(f.keys())
