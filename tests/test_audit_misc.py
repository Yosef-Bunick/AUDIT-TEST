"""Tests for the remaining audit internals: audit_deps import scanning and
audit_suite's failure/skip classification."""

from audit_code import audit_deps, audit_suite

# ── audit_deps: third-party import inventory ──


def test_collect_imports_finds_external_package(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_deps, "ROOT", tmp_path)
    (tmp_path / "app.py").write_text(
        "import requests\nfrom collections import OrderedDict\n", encoding="utf-8"
    )
    found = audit_deps._collect_imports()
    assert "requests" in found  # external
    assert "collections" not in found  # stdlib, excluded
    assert found["requests"] == ["app.py"]


# ── audit_suite: pytest output parsing / classification ──


def test_parse_reads_pass_fail_counts():
    output = "tests/test_x.py .F\n1 passed, 1 failed\nFAILED tests/test_x.py::test_b\n"
    r = audit_suite._parse(output, returncode=1)
    assert r["passed"] == 1
    assert r["failed"] == 1
    assert ("FAILED", "tests/test_x.py::test_b") in r["failures"]


def test_s3_s5_report_counts_collection_error(capsys):
    # Standalone _s3_s5_report takes running (med, info) ints and prints.
    out = "ERROR collecting tests/test_broken.py\n"
    r = audit_suite._parse(out, returncode=2)
    med, info = audit_suite._s3_s5_report(out, r, 0, 0)
    assert med >= 1


def test_audit_suite_main_on_passing_project(monkeypatch, tmp_path, capsys):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    monkeypatch.setattr(audit_suite, "ROOT", tmp_path)
    monkeypatch.setattr(audit_suite.sys, "argv", ["audit_suite", "--fast"])
    try:
        audit_suite.main()
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "SUMMARY" in out
    assert "1 passed" in out or "passed" in out
