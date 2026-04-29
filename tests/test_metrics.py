"""
tests/test_metrics.py — Unit tests for file_metrics.py
"""

import pytest
from tools.file_metrics import get_file_metrics, get_metrics_for_all


@pytest.fixture
def py_file(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("line1\nline2\nline3\n")
    return {
        "path": str(f),
        "relative_path": "test.py",
        "language": "python",
        "extension": ".py",
        "filename": "test.py",
    }


def test_get_file_metrics_line_count(py_file):
    result = get_file_metrics(py_file)
    assert result["line_count"] == 3


def test_get_file_metrics_size_positive(py_file):
    result = get_file_metrics(py_file)
    assert result["size_kb"] > 0


def test_get_file_metrics_preserves_original_keys(py_file):
    result = get_file_metrics(py_file)
    assert result["language"] == "python"
    assert result["extension"] == ".py"


def test_get_file_metrics_missing_file():
    bad_info = {
        "path": "/does/not/exist.py",
        "relative_path": "exist.py",
        "language": "python",
        "extension": ".py",
        "filename": "exist.py",
    }
    result = get_file_metrics(bad_info)
    assert result["size_kb"] == 0.0
    assert result["line_count"] == 0


def test_get_metrics_for_all(tmp_path):
    files_info = []
    for i in range(3):
        f = tmp_path / f"file{i}.py"
        f.write_text(f"# file {i}\n" * (i + 1))
        files_info.append({
            "path": str(f),
            "relative_path": f"file{i}.py",
            "language": "python",
            "extension": ".py",
            "filename": f"file{i}.py",
        })

    results = get_metrics_for_all(files_info)
    assert len(results) == 3
    for r in results:
        assert "size_kb" in r
        assert "line_count" in r
        assert r["line_count"] > 0
