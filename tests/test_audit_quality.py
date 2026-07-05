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


def test_q_mutation_disabled_is_skip(tmp_path, capsys):
    counts = _counts()
    q.q_mutation(tmp_path, counts, enabled=False)
    out = capsys.readouterr().out
    assert "Q8" in out
    assert counts["INFO"] == 0
