"""Adapter-base helper tests — the plumbing every adapter stands on.

run_tool must never raise (timeout → -1, unlaunchable → -2), the file walk
must prune build dirs while including root-level files, and quality's _tool
resolution must prefer `python -m` for installed packages.
"""

import sys
import time
from pathlib import Path

from audit_code import quality
from audit_code.adapters.base import TimeBudget, iter_source_files, rel, run_tool
from audit_code.quality import _tool

# ── run_tool never raises ──


def test_run_tool_success(tmp_path):
    rc, out, err = run_tool([sys.executable, "-c", "print('hello')"], tmp_path)
    assert rc == 0
    assert "hello" in out


def test_run_tool_timeout_returns_minus_one(tmp_path):
    start = time.monotonic()
    rc, out, err = run_tool(
        [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=1
    )
    assert rc == -1
    assert "timed out" in err
    assert time.monotonic() - start < 25  # actually killed, not waited out


def test_run_tool_unlaunchable_returns_minus_two(tmp_path):
    rc, out, err = run_tool(["no-such-binary-xyz-123"], tmp_path)
    assert rc == -2
    assert "failed to launch" in err


# ── source-file walk: pruning + root-level files ──


def test_iter_source_files_includes_root_and_nested(tmp_path):
    (tmp_path / "root.go").write_text("package main\n", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "nested.go").write_text("package b\n", encoding="utf-8")

    found = {p.name for p in iter_source_files(tmp_path, (".go",))}
    assert found == {"root.go", "nested.go"}


def test_iter_source_files_prunes_build_dirs(tmp_path):
    for d in ("node_modules", "target", "bin", "obj", ".git", "vendor"):
        sub = tmp_path / d / "x"
        sub.mkdir(parents=True)
        (sub / "junk.go").write_text("package junk\n", encoding="utf-8")
    (tmp_path / "real.go").write_text("package main\n", encoding="utf-8")

    found = list(iter_source_files(tmp_path, (".go",)))
    assert [p.name for p in found] == ["real.go"]


# ── TimeBudget / rel ──


def test_time_budget():
    assert TimeBudget(-1).exhausted()  # deadline already past
    assert not TimeBudget(3600).exhausted()


def test_rel_inside_and_outside_root(tmp_path):
    inside = tmp_path / "src" / "a.py"
    assert rel(inside, tmp_path) == str(Path("src") / "a.py")
    outside = Path("C:/somewhere/else/b.py")
    assert rel(outside, tmp_path) == str(outside)  # no raise, absolute kept


# ── quality._tool resolution ──


def test_tool_prefers_python_module_for_installed_package(tmp_path):
    """pytest is installed in this interpreter and lives outside the target
    root, so _tool must return `<python> -m pytest`, immune to PATH."""
    resolved = _tool("pytest", tmp_path)
    assert resolved is not None
    assert resolved.startswith(sys.executable)
    assert resolved.endswith("-m pytest")


def test_tool_missing_everywhere_returns_none(tmp_path):
    assert _tool("definitely-not-a-real-tool-xyz", tmp_path) is None


# ── quality audit on a project WITH a tests/ dir ──
# Regression guard: Q7's hygiene walk only executes when tests/ exists, so a
# crash there is invisible to projects without one (that is how a NameError
# in the loop slipped past the e2e tests once).


def test_quality_q7_flags_hygiene_and_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(quality, "_tool", lambda name, root: None)
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_x.py").write_text(
        "import time\n"
        "import pytest\n"
        "\n"
        "def test_slow():\n"
        "    time.sleep(2)\n"
        "    pytest.skip()\n",
        encoding="utf-8",
    )

    result = quality.run(tmp_path, fast=True)

    assert result.completed
    messages = [f.message for f in result.findings]
    assert any("time.sleep" in m for m in messages)
    assert any("skip with NO reason" in m for m in messages)
