"""Test for audit_phd — the PhD-standard static review.

audit_phd is one ~1000-line main() over a battery of helpers; a single run over
a fixture that deliberately trips several HIGH checks exercises the bulk of it
and asserts the checks actually fire.
"""

import re

from audit_code import audit_phd as phd


def test_main_flags_multiple_high_checks(monkeypatch, tmp_path, capsys):
    prod = (
        "import subprocess\n"
        "\n"
        "CACHE = {}\n"
        "\n"
        "def handler(x=[]):\n"  # B1: mutable default
        "    try:\n"
        "        return eval(x)\n"  # SEC2: dynamic exec
        "    except:\n"  # C1: bare except
        "        pass\n"  # C2: swallowed
        "\n"
        "def run(cmd):\n"
        "    subprocess.run(cmd, shell=True)\n"  # SEC1: shell=True
        "    CACHE['k'] = 1\n"  # G2: module mutable mutated
    )
    (tmp_path / "app.py").write_text(prod, encoding="utf-8")
    # a test dir so the test-analysis paths (T-series, fn_asserts) also run
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text(
        "def test_nothing():\n    handler('1')\n", encoding="utf-8"
    )

    monkeypatch.setattr(phd, "ROOT", tmp_path)
    monkeypatch.setattr(phd.sys, "argv", ["audit_phd"])
    phd.main()  # no --strict → prints, does not exit
    out = capsys.readouterr().out

    assert "SEC1" in out and "SEC2" in out  # HIGH sections always print
    m = re.search(r"SUMMARY\s+HIGH:\s*(\d+)", out)
    assert m and int(m.group(1)) >= 3, out[-400:]


def test_main_clean_fixture_reports_zero_high(monkeypatch, tmp_path, capsys):
    (tmp_path / "ok.py").write_text(
        'def add(a, b):\n    """Add two numbers."""\n    return a + b\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(phd, "ROOT", tmp_path)
    monkeypatch.setattr(phd.sys, "argv", ["audit_phd"])
    phd.main()
    out = capsys.readouterr().out
    m = re.search(r"SUMMARY\s+HIGH:\s*(\d+)", out)
    assert m and int(m.group(1)) == 0
