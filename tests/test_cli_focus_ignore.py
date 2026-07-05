"""Tests for the focus/ignore CLI surface and gate wiring in cli.py.

These drive the entry-point functions directly (sys.argv + a redirected
.audit-test-ignore) so the focus/ignore feature is actually exercised under the
suite rather than only in production subprocesses.
"""

import pytest

from audit_code import cli


@pytest.fixture
def ig(tmp_path, monkeypatch):
    """Redirect the module-level ignore-file path to a throwaway tmp file."""
    path = tmp_path / ".audit-test-ignore"
    monkeypatch.setattr(cli, "_IGNORE_PATH", path)
    return path


def _run(monkeypatch, argv, inputs=None):
    """Set argv, feed canned input(), run the matching handler, return exit code."""
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", *argv])
    if inputs is not None:
        it = iter(inputs)
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))
    fn = cli._handle_focus if argv and argv[0] == "focus" else cli._handle_ignore
    with pytest.raises(SystemExit) as exc:
        fn()
    code = exc.value.code
    return code if code is not None else 0


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_build_gate_parser_defaults_and_flags():
    p = cli.build_gate_parser()
    ns = p.parse_args(["--path", "x", "--fast", "--kill", "80", "--medium"])
    assert ns.path == "x" and ns.fast is True and ns.kill == 80 and ns.medium is True
    assert p.parse_args([]).kill == 60  # default


def test_ig_read_missing_returns_empty(ig):
    assert cli._ig_read() == []


def test_ig_write_then_read_roundtrips(ig):
    cli._ig_write(["node_modules", "dist"])
    assert cli._ig_read() == ["node_modules", "dist"]


def test_parse_groups_reads_only_block():
    lines = [
        "#path /base",
        "#only",
        "fast=[main.py, cli.py] /proj  | quick sweep",
        "# a comment inside",
        "#only",
    ]
    groups, default = cli._parse_groups(lines)
    assert default == "/base"
    assert set(groups) == {"fast"}
    g = groups["fast"]
    assert (
        g.files == ("main.py", "cli.py")
        and g.path == "/proj"  # leading slash preserved
        and g.desc == "quick sweep"
    )


def test_rebuild_file_replaces_only_block():
    original = ["node_modules", "#only", "old=[a.py]", "#only", "trailing"]
    groups = {"new": cli._Grp(("b.py",), "", "")}
    out = cli._rebuild_file(original, groups, "")
    assert "node_modules" in out and "trailing" in out
    assert "#only" in out and any("new=[b.py]" in ln for ln in out)
    assert not any("old=[a.py]" in ln for ln in out)


def test_confirm_yes_no_and_twice(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    assert cli._confirm("go?") is True
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    assert cli._confirm("go?") is False
    answers = iter(["y", "q"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    assert cli._confirm("go?", twice=True) is True
    answers = iter(["y", "nope"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    assert cli._confirm("go?", twice=True) is False


def test_focus_help_and_mode_flags(capsys, monkeypatch):
    cli._focus_help()
    assert "focus" in capsys.readouterr().out
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "focus", "fast"])
    assert cli._is_focus_mode() is True and cli._is_ignore_mode() is False
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "ignore", "add", "x"])
    assert cli._is_ignore_mode() is True and cli._is_focus_mode() is False


# ── focus command actions ────────────────────────────────────────────────────


def test_focus_add_del_path_desc(ig, monkeypatch, capsys):
    assert _run(monkeypatch, ["focus", "add", "grp", "a.py", "b.py"]) == 0
    assert "grp" in ig.read_text(encoding="utf-8")
    assert _run(monkeypatch, ["focus", "del", "grp", "a.py"]) == 0
    assert _run(monkeypatch, ["focus", "path", "grp", "/somewhere"]) == 0
    assert _run(monkeypatch, ["focus", "desc", "grp", "my", "desc"]) == 0
    text = ig.read_text(encoding="utf-8")
    assert "b.py" in text and "/somewhere" in text and "my desc" in text


def test_focus_desc_survives_reread(ig, monkeypatch):
    """Regression: setting a description used to silently delete the group."""
    _run(monkeypatch, ["focus", "add", "grp", "a.py"])
    _run(monkeypatch, ["focus", "desc", "grp", "my", "description"])
    groups, _ = cli._parse_groups(cli._ig_read())
    assert "grp" in groups and groups["grp"].desc == "my description"
    assert groups["grp"].files == ("a.py",)


def test_focus_absolute_path_roundtrips(ig, monkeypatch):
    """Regression: absolute group paths used to lose their leading slash."""
    _run(monkeypatch, ["focus", "add", "grp", "a.py"])
    _run(monkeypatch, ["focus", "path", "grp", "/mnt/c/other"])
    groups, _ = cli._parse_groups(cli._ig_read())
    assert groups["grp"].path == "/mnt/c/other"


def test_focus_info_and_missing_group(ig, monkeypatch, capsys):
    _run(monkeypatch, ["focus", "add", "grp", "a.py"])
    assert _run(monkeypatch, ["focus", "info"]) == 0
    assert "grp" in capsys.readouterr().out
    assert _run(monkeypatch, ["focus", "info", "nope"]) == 2


def test_focus_clear_confirmed(ig, monkeypatch):
    _run(monkeypatch, ["focus", "add", "grp", "a.py"])
    assert _run(monkeypatch, ["focus", "clear", "grp"], inputs=["y"]) == 0
    groups, _ = cli._parse_groups(cli._ig_read())
    assert "grp" not in groups


def test_focus_help_action(monkeypatch):
    assert _run(monkeypatch, ["focus", "help"]) == 0


def test_focus_run_missing_group_exits_2(ig, monkeypatch):
    assert _run(monkeypatch, ["focus", "ghost"]) == 2


def test_focus_run_invokes_audit(ig, monkeypatch):
    _run(monkeypatch, ["focus", "add", "grp", "a.py"])
    called = {}

    def fake_run_audit(ns):
        called["path"] = ns.path
        return 0

    monkeypatch.setattr(cli, "run_audit", fake_run_audit)
    assert _run(monkeypatch, ["focus", "grp", "high"]) == 0
    assert called  # run_audit was reached through _focus_run


# ── ignore command actions ───────────────────────────────────────────────────


def test_ignore_add_del_info_clear(ig, monkeypatch, capsys):
    assert _run(monkeypatch, ["ignore", "add", "mydir"]) == 0
    assert "mydir" in ig.read_text(encoding="utf-8")
    assert _run(monkeypatch, ["ignore", "info"]) == 0
    assert "mydir" in capsys.readouterr().out
    assert _run(monkeypatch, ["ignore", "del", "mydir"]) == 0
    assert _run(monkeypatch, ["ignore", "del", "ghost"]) == 2
    _run(monkeypatch, ["ignore", "add", "again"])
    assert _run(monkeypatch, ["ignore", "clear"], inputs=["y"]) == 0
    assert "again" not in ig.read_text(encoding="utf-8")


def test_ignore_help_and_unknown(monkeypatch):
    assert _run(monkeypatch, ["ignore", "help"]) == 0
    assert _run(monkeypatch, ["ignore", "bogus"]) == 2


# ── main() dispatch branches ─────────────────────────────────────────────────


def test_main_routes_to_focus(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "focus", "help"])
    monkeypatch.setattr(cli, "_force_utf8_output", lambda: None)
    with pytest.raises(SystemExit):
        cli.main()


def test_main_routes_to_gate(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "gate", "--fast"])
    monkeypatch.setattr(cli, "_force_utf8_output", lambda: None)
    monkeypatch.setattr(cli, "run_gate_cmd", lambda args: 0)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert (exc.value.code or 0) == 0


def test_run_gate_cmd_delegates(monkeypatch):
    monkeypatch.setattr(cli, "find_target_root", lambda p: "root")
    monkeypatch.setattr(cli, "gate_main", lambda root, **kw: 7)

    class NS:
        path = None
        fast = True
        no_static = False
        kill = 60

    assert cli.run_gate_cmd(NS()) == 7
