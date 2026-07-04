"""CLI-level tests — exit codes, report-flag wiring, target resolution.

These encode the checks run by hand after every change: `audit-code --min
--path <broken>` must exit 1, a clean project must exit 0, `--report-only`
must force 0, and report files must land where the flag/config says.
"""

import argparse
import json
import sys

import pytest

from audit_code import cli, quality
from audit_code.models import EXIT_FAIL, EXIT_PASS
from audit_code.project import find_target_root


def _args(**overrides) -> argparse.Namespace:
    """A full audit-parser namespace with defaults, like argparse produces."""
    base = {
        "path": None,
        "min": True,
        "full": False,
        "strict": True,
        "report_only": False,
        "fix": False,
        "json": "",
        "sarif": "",
        "junit": "",
        "profile": "",
        "config": "",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _no_external_tools(monkeypatch):
    monkeypatch.setattr(quality, "_tool", lambda name, root: None)


# ── exit codes ──


def test_run_audit_broken_project_exits_fail(tmp_path, monkeypatch, capsys):
    _no_external_tools(monkeypatch)
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    assert cli.run_audit(_args(path=str(tmp_path))) == EXIT_FAIL


def test_run_audit_clean_project_exits_pass(tmp_path, capsys):
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    assert cli.run_audit(_args(path=str(tmp_path))) == EXIT_PASS


def test_report_only_forces_exit_zero_on_failure(tmp_path, monkeypatch, capsys):
    _no_external_tools(monkeypatch)
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    assert cli.run_audit(_args(path=str(tmp_path), report_only=True)) == EXIT_PASS


# ── report writing ──


def test_json_flag_writes_report(tmp_path, capsys):
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    out = tmp_path / "out" / "report.json"
    out.parent.mkdir()
    cli.run_audit(_args(path=str(tmp_path), json=str(out)))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {a["id"] for a in data["audits"]} >= {"html-syntax"}


def test_reporting_config_default_used_when_flag_absent(tmp_path, capsys):
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    cfg_target = tmp_path / "from-config.json"
    (tmp_path / "audit-code.toml").write_text(
        f'[reporting]\njson = "{str(cfg_target).replace(chr(92), "/")}"\n',
        encoding="utf-8",
    )
    cli.run_audit(_args(path=str(tmp_path)))
    assert cfg_target.exists()


def test_json_flag_wins_over_reporting_config(tmp_path, capsys):
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    cfg_target = tmp_path / "from-config.json"
    flag_target = tmp_path / "from-flag.json"
    (tmp_path / "audit-code.toml").write_text(
        f'[reporting]\njson = "{str(cfg_target).replace(chr(92), "/")}"\n',
        encoding="utf-8",
    )
    cli.run_audit(_args(path=str(tmp_path), json=str(flag_target)))
    assert flag_target.exists()
    assert not cfg_target.exists()


def test_all_three_report_formats_written(tmp_path, capsys):
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    j, s, x = tmp_path / "r.json", tmp_path / "r.sarif", tmp_path / "r.xml"
    cli.run_audit(_args(path=str(tmp_path), json=str(j), sarif=str(s), junit=str(x)))
    assert j.exists() and s.exists() and x.exists()


# ── gate-mode argv detection ──


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["audit-code", "gate"], True),
        (["audit-code", "gate", "--fast"], True),
        (["audit-code", "--min", "gate"], True),  # first positional is 'gate'
        (["audit-code"], False),
        (["audit-code", "--min"], False),
        # Known quirk: a flag VALUE named 'gate' (--path gate) looks
        # positional to _is_gate_mode. Use --path=./gate to disambiguate.
        pytest.param(
            ["audit-code", "--path", "gate"],
            False,
            marks=pytest.mark.xfail(
                reason="flag values are indistinguishable from positionals",
                strict=True,
            ),
        ),
    ],
)
def test_is_gate_mode(monkeypatch, argv, expected):
    monkeypatch.setattr(sys, "argv", argv)
    assert cli._is_gate_mode() is expected


# ── target resolution (project.py) ──


def test_find_target_root_missing_path_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        find_target_root(str(tmp_path / "does-not-exist"))
    assert exc.value.code == 2


def test_find_target_root_file_not_dir_exits_2(tmp_path, capsys):
    f = tmp_path / "a-file.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        find_target_root(str(f))
    assert exc.value.code == 2


def test_find_target_root_resolves_valid_dir(tmp_path):
    assert find_target_root(str(tmp_path)) == tmp_path.resolve()
