"""Tests for audit_quality's external-tool gate functions (Q0–Q8).

Each q_* function is driven against a fixture; tools that may be absent
(pip-audit, mutmut) are exercised on their SKIP path. counts is pre-seeded with
the three severity buckets the functions accumulate into.
"""

from audit_code import audit_quality as q


def _counts():
    return {"HIGH": 0, "MEDIUM": 0, "INFO": 0}


# ── Q0: syntax ──


def test_q_syntax_flags_unparseable(tmp_path, capsys):
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    counts = _counts()
    q.q_syntax(tmp_path, tmp_path / "tests", counts)
    assert counts["HIGH"] >= 1


def test_q_syntax_clean(tmp_path, capsys):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    counts = _counts()
    q.q_syntax(tmp_path, tmp_path / "tests", counts)
    assert counts["HIGH"] == 0


# ── Q1/Q2/Q3: black, ruff, mypy (skip-tolerant) ──


def test_q_black_on_clean_file(tmp_path, capsys):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    counts = _counts()
    q.q_black(tmp_path, counts)  # black present → clean; absent → SKIP
    assert counts["MEDIUM"] == 0


def test_q_ruff_runs_without_crashing(tmp_path, capsys):
    (tmp_path / "app.py").write_text("import os\n", encoding="utf-8")  # unused import
    counts = _counts()
    q.q_ruff(tmp_path, counts)
    out = capsys.readouterr().out
    assert "Q2" in out


def test_q_mypy_runs_without_crashing(tmp_path, capsys):
    (tmp_path / "app.py").write_text("x: int = 1\n", encoding="utf-8")
    counts = _counts()
    q.q_mypy(tmp_path, counts, strict=False)
    assert "Q3" in capsys.readouterr().out


# ── Q6: docstrings ──


def test_q_docstrings_reports_coverage(tmp_path, capsys):
    (tmp_path / "app.py").write_text(
        "def undocumented():\n    return 1\n", encoding="utf-8"
    )
    q.q_docstrings(tmp_path, tmp_path / "tests", _counts())
    assert "documented" in capsys.readouterr().out


# ── Q7: test hygiene ──


def test_q_test_hygiene_flags_sleep_in_test(tmp_path, capsys):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text(
        "import time\ndef test_x():\n    time.sleep(1)\n", encoding="utf-8"
    )
    counts = _counts()
    q.q_test_hygiene(tmp_path, tests, counts)
    assert counts["MEDIUM"] >= 1


# ── Q4/Q8: CVE + mutation on their skip-tolerant paths ──


def test_q_cves_runs(tmp_path, capsys):
    counts = _counts()
    q.q_cves(tmp_path, counts)  # pip-audit/safety present → run; absent → SKIP
    assert "Q4" in capsys.readouterr().out


_PIP_AUDIT_JSON = (
    '{"dependencies": ['
    '{"name": "Flask", "version": "3.0.0", "vulns": [{"id": "PYSEC-2026-2151"}]},'
    '{"name": "requests", "version": "2.31.0", "vulns": [{"id": "CVE-2024-35195"}]},'
    '{"name": "numpy", "version": "1.26.0", "vulns": []}]}'
)


def test_q_cves_scopes_to_declared_deps(tmp_path, capsys, monkeypatch):
    """A vulnerable env package the project never declared must not count;
    a declared one must."""
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    monkeypatch.setattr(q, "_tool", lambda name: "pip-audit")
    monkeypatch.setattr(q, "_run", lambda *a, **k: (1, _PIP_AUDIT_JSON))
    counts = _counts()
    q.q_cves(tmp_path, counts)
    out = capsys.readouterr().out
    assert counts["HIGH"] == 1, "only the declared vulnerable package counts"
    assert "flask 3.0.0" in out and "PYSEC-2026-2151" in out
    assert "requests" in out and "not counted" in out


def test_q_cves_no_manifest_scans_environment(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(q, "_tool", lambda name: "pip-audit")
    monkeypatch.setattr(q, "_run", lambda *a, **k: (1, _PIP_AUDIT_JSON))
    counts = _counts()
    q.q_cves(tmp_path, counts)
    assert counts["HIGH"] == 2, "no manifest -> every vulnerable package counts"


def test_declared_deps_reads_pyproject_and_requirements(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["Flask>=3", "ruff"]\n'
        '[project.optional-dependencies]\nall = ["Semgrep==1.0"]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements-dev.txt").write_text(
        "# dev tools\npytest_xdist==3.5\n-r other.txt\n", encoding="utf-8"
    )
    deps = q._declared_deps(tmp_path)
    assert deps == {"flask", "ruff", "semgrep", "pytest-xdist"}


def test_q_mutation_disabled_is_skip(tmp_path, capsys):
    counts = _counts()
    q.q_mutation(tmp_path, counts, enabled=False)
    out = capsys.readouterr().out
    assert "Q8" in out
    assert counts["INFO"] == 0
