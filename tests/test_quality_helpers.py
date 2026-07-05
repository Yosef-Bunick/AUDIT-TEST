"""Tests for quality.py's low-level helpers (_run subprocess wrapper, _def_spans)."""

import sys

from audit_code import quality as q


def test_run_success_captures_output(tmp_path):
    rc, out = q._run([sys.executable, "-c", "print('hello-run')"], tmp_path)
    assert rc == 0 and "hello-run" in out


def test_run_missing_binary_returns_minus_two(tmp_path):
    rc, out = q._run(["definitely-not-a-real-binary-xyz"], tmp_path)
    assert rc == -2 and "failed to launch" in out


def test_run_timeout_returns_minus_one(tmp_path):
    rc, out = q._run(
        [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, timeout=1
    )
    assert rc == -1 and "timed out" in out


def test_def_spans_extracts_functions_and_methods(tmp_path):
    src = tmp_path / "sample.py"
    src.write_text(
        "def top():\n"
        "    return 1\n"
        "\n"
        "class C:\n"
        "    def method(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    spans = q._def_spans(src)
    names = {s[0] for s in spans}
    assert "top" in names and "C.method" in names


def test_def_spans_syntax_error_returns_empty(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    assert q._def_spans(bad) == []
