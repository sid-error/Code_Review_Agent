"""
tests/test_heuristics.py — Unit tests for heuristic_analyzer.py
"""

import pytest
from tools.heuristic_analyzer import analyze_metrics, analyze_all_metrics


def make_file_info(tmp_path, name="test.py", content="", language="python"):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    size_kb = f.stat().st_size / 1024
    line_count = len(content.splitlines())
    return {
        "path": str(f),
        "relative_path": name,
        "language": language,
        "extension": f".{name.rsplit('.', 1)[-1]}",
        "filename": name,
        "size_kb": size_kb,
        "line_count": line_count,
    }


def test_large_file_issue_triggered(tmp_path):
    content = "x = 1\n" * 600  # 600 lines > threshold of 500
    info = make_file_info(tmp_path, content=content)
    issues = analyze_metrics(info)
    types = [i["type"] for i in issues]
    assert "large_file" in types


def test_small_file_no_large_file_issue(tmp_path):
    content = "x = 1\n" * 10
    info = make_file_info(tmp_path, content=content)
    issues = analyze_metrics(info)
    types = [i["type"] for i in issues]
    assert "large_file" not in types


def test_bare_except_detected(tmp_path):
    content = """
def risky():
    try:
        pass
    except:
        pass
"""
    info = make_file_info(tmp_path, content=content)
    issues = analyze_metrics(info)
    types = [i["type"] for i in issues]
    assert "bare_except" in types


def test_missing_docstring_flagged(tmp_path):
    content = """
def big_function(a, b, c, d):
    x = a + b
    y = b + c
    z = c + d
    return x + y + z
"""
    info = make_file_info(tmp_path, content=content)
    issues = analyze_metrics(info)
    types = [i["type"] for i in issues]
    assert "missing_docstring" in types


def test_docstring_present_not_flagged(tmp_path):
    content = '''
def documented_function(a, b, c, d):
    """This function is documented."""
    return a + b + c + d
'''
    info = make_file_info(tmp_path, content=content)
    issues = analyze_metrics(info)
    types = [i["type"] for i in issues]
    assert "missing_docstring" not in types


def test_js_file_skips_python_checks(tmp_path):
    content = "function foo() { try {} catch(e) {} }"
    info = make_file_info(tmp_path, name="util.js", content=content, language="javascript")
    issues = analyze_metrics(info)
    # Bare except and AST checks should NOT run for JS
    types = [i["type"] for i in issues]
    assert "bare_except" not in types
    assert "missing_docstring" not in types


def test_analyze_all_returns_flat_list(tmp_path):
    f1 = make_file_info(tmp_path, "a.py", "x = 1\n" * 600)
    f2 = make_file_info(tmp_path, "b.py", "y = 2\n" * 10)
    all_issues = analyze_all_metrics([f1, f2])
    assert isinstance(all_issues, list)
    # At least the large file issue from f1
    assert len(all_issues) >= 1


def test_issue_structure(tmp_path):
    content = "x = 1\n" * 600
    info = make_file_info(tmp_path, content=content)
    issues = analyze_metrics(info)
    required_keys = {"source", "type", "message", "severity", "file"}
    for issue in issues:
        assert required_keys.issubset(issue.keys()), f"Missing keys in issue: {issue}"
