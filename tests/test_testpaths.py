"""Tests for pytest-config-driven test discovery (audit_code.testpaths).

Regression cover for the hardcoded-`tests/` defect: in a repo whose real suite
lives elsewhere and whose pyproject declares
``testpaths = ["pkg/backend/tests", "test_data"]``, S1/S2 used to triage a
stale one-file `tests/` directory and Q5 used to report "0 executed (0.0%)".
"""

from pathlib import Path

import pytest

from audit_code import quality, suite, testpaths


def _mk(tmp_path: Path, rel: str, body: str = "def test_x():\n    assert True\n"):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ── reading each config format ───────────────────────────────────────────────


def test_pyproject_ini_options_testpaths(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nignore_missing_imports = true\n\n"
        "[tool.pytest.ini_options]\n"
        'testpaths = ["pkg/backend/tests", "test_data"]\n',
        encoding="utf-8",
    )
    _mk(tmp_path, "pkg/backend/tests/test_a.py")
    _mk(tmp_path, "test_data/test_b.py")
    assert testpaths.declared_testpaths(tmp_path) == [
        "pkg/backend/tests",
        "test_data",
    ]
    assert testpaths.pytest_targets(tmp_path) == ["pkg/backend/tests", "test_data"]


def test_pytest_ini_wins_over_pyproject(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = it\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["other"]\n', encoding="utf-8"
    )
    _mk(tmp_path, "it/test_a.py")
    _mk(tmp_path, "other/test_b.py")
    assert testpaths.pytest_targets(tmp_path) == ["it"]


def test_setup_cfg_tool_pytest_section(tmp_path):
    (tmp_path / "setup.cfg").write_text(
        "[tool:pytest]\ntestpaths =\n    suite_a\n    suite_b\n", encoding="utf-8"
    )
    _mk(tmp_path, "suite_a/test_a.py")
    _mk(tmp_path, "suite_b/test_b.py")
    assert testpaths.pytest_targets(tmp_path) == ["suite_a", "suite_b"]


def test_tox_ini_pytest_section(tmp_path):
    (tmp_path / "tox.ini").write_text(
        "[tox]\nenvlist = py312\n\n[pytest]\ntestpaths = mytests\n", encoding="utf-8"
    )
    _mk(tmp_path, "mytests/test_a.py")
    assert testpaths.pytest_targets(tmp_path) == ["mytests"]


# ── fallbacks ────────────────────────────────────────────────────────────────


def test_falls_back_to_tests_when_nothing_declared(tmp_path):
    _mk(tmp_path, "tests/test_a.py")
    assert testpaths.pytest_targets(tmp_path) == ["tests"]
    assert testpaths.discover_test_dirs(tmp_path) == [(tmp_path / "tests").resolve()]


def test_tool_config_only_pyproject_falls_back(tmp_path):
    """A pyproject with [tool.mypy] but no pytest section must not hijack."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", "utf-8")
    _mk(tmp_path, "tests/test_a.py")
    assert testpaths.declared_testpaths(tmp_path) == []
    assert testpaths.pytest_targets(tmp_path) == ["tests"]


def test_nonexistent_testpath_entries_are_dropped(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["gone", "here"]\n', encoding="utf-8"
    )
    _mk(tmp_path, "here/test_a.py")
    assert testpaths.pytest_targets(tmp_path) == ["here"]


def test_all_testpaths_missing_falls_back_to_tests(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["gone"]\n', encoding="utf-8"
    )
    assert testpaths.pytest_targets(tmp_path) == ["tests"]


def test_file_testpath_resolves_to_its_directory(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["it/test_one.py"]\n', encoding="utf-8"
    )
    _mk(tmp_path, "it/test_one.py")
    assert testpaths.discover_test_dirs(tmp_path) == [(tmp_path / "it").resolve()]


def test_explicit_override_beats_declaration(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["declared"]\n', encoding="utf-8"
    )
    _mk(tmp_path, "declared/test_a.py")
    _mk(tmp_path, "forced/test_b.py")
    assert testpaths.pytest_targets(tmp_path, "forced") == ["forced"]


def test_malformed_config_does_not_raise(tmp_path):
    (tmp_path / "pyproject.toml").write_text("this is not = valid [ toml", "utf-8")
    (tmp_path / "setup.cfg").write_text("%%% not ini %%%", encoding="utf-8")
    assert testpaths.pytest_targets(tmp_path) == ["tests"]


# ── consumers actually honour it ─────────────────────────────────────────────


def test_suite_run_targets_declared_paths(tmp_path, monkeypatch):
    """S1/S2 must invoke pytest against the DECLARED paths, not `tests/`."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["real/tests"]\n', encoding="utf-8"
    )
    _mk(tmp_path, "real/tests/test_a.py")
    _mk(tmp_path, "tests/test_stale.py", "import nope_missing_module\n")

    seen = {}

    def fake_run_pytest(target, cwd, timeout, cov_file=None, xdist=True, extra=()):
        seen["target"] = target
        return "1 passed in 0.01s\n", 0

    monkeypatch.setattr(suite, "_run_pytest", fake_run_pytest)
    result = suite.run(tmp_path, fast=True)
    assert seen["target"] == ["real/tests"]
    assert "pytest real/tests" in result.stdout
    assert result.high == 0


def test_quality_partitions_tests_by_declared_paths(tmp_path):
    """Q5/Q7's test corpus follows testpaths — the 0.0% coverage defect."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["pkg/tests", "extra_tests"]\n',
        encoding="utf-8",
    )
    _mk(tmp_path, "pkg/tests/test_a.py")
    _mk(tmp_path, "pkg/tests/test_b.py")
    _mk(tmp_path, "extra_tests/test_c.py")
    _mk(tmp_path, "pkg/app.py", "def f():\n    return 1\n")

    dirs = quality.discover_test_dirs(tmp_path)
    prod, tests = quality._py_files(tmp_path, dirs)
    assert len(dirs) == 2
    assert {p.name for p in tests} == {"test_a.py", "test_b.py", "test_c.py"}
    assert {p.name for p in prod} == {"app.py"}

    # the old single-Path call shape still works
    prod2, tests2 = quality._py_files(tmp_path, dirs[0])
    assert {p.name for p in tests2} == {"test_a.py", "test_b.py"}


# ── Q5 must not turn discovery into a surprise full test run ─────────────────


def test_q5_refuses_to_self_run_an_oversized_suite(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["big"]\n', encoding="utf-8"
    )
    for i in range(6):
        _mk(tmp_path, f"big/test_{i}.py")
    monkeypatch.setattr(quality, "_Q5_MAX_TEST_FILES", 3)
    monkeypatch.setenv("AUDIT_NO_Q5_CACHE", "1")

    def boom(*a, **k):  # any subprocess launch here is the bug
        raise AssertionError("Q5 must not run the suite when capped")

    monkeypatch.setattr(quality, "_run", boom)
    out, findings, counts = [], [], {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    quality._q5_never_executed(
        tmp_path,
        quality.discover_test_dirs(tmp_path),
        False,
        "",
        None,
        findings,
        counts,
        out,
    )
    text = "\n".join(out)
    assert "exceeds the Q5 self-run cap" in text
    assert findings == [] and counts["MEDIUM"] == 0


def test_q5_zero_percent_is_reported_as_a_non_result(tmp_path):
    """'0 of N defs executed' means the suite never ran — not N dead defs."""
    _mk(tmp_path, "app.py", "def f():\n    x = 1\n    return x\n")
    cov = {"files": {"app.py": {"executed_lines": []}}}
    out, findings, counts = [], [], {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    quality._q5_flag_never_run(
        tmp_path, [(tmp_path / "tests").resolve()], cov, findings, counts, out
    )
    text = "\n".join(out)
    assert "the suite did not run" in text
    assert findings == [] and counts["MEDIUM"] == 0


def test_q5_normal_coverage_still_reports_findings(tmp_path):
    """The 0% gate must not suppress a genuine partial-coverage result."""
    _mk(
        tmp_path,
        "app.py",
        "def ran():\n    a = 1\n    b = 2\n    return a + b\n\n\n"
        "def never():\n    a = 1\n    b = 2\n    return a - b\n",
    )
    cov = {"files": {"app.py": {"executed_lines": [2, 3, 4]}}}
    out, findings, counts = [], [], {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    quality._q5_flag_never_run(
        tmp_path, [(tmp_path / "tests").resolve()], cov, findings, counts, out
    )
    text = "\n".join(out)
    assert "2 defs scanned; 1 executed" in text
    assert "the suite did not run" not in text


@pytest.mark.parametrize(
    "raw,expected",
    [("a b", ["a", "b"]), ("a\n  b\n", ["a", "b"]), ("", [])],
)
def test_split_ini_list(raw, expected):
    assert testpaths._split_ini_list(raw) == expected
