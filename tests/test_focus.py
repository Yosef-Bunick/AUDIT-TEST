"""Tests for the focus-group search mechanism (audit_shared + quality wiring).

These lock in the fix for the import-timing trap: the CLI sets AUDIT_FOCUS_GROUP
inside _focus_run(), which runs long after audit_shared is imported (cli -> runner
-> quality pull it in at startup).  _active_paths() therefore MUST read the env var
at call time, not cache it at import — otherwise focus is a silent no-op for every
in-process consumer (quality's Q0-Q8).
"""

from pathlib import Path

from audit_code import audit_shared as sh
from audit_code import quality as q
from audit_code.audit_shared import Group


def _register(monkeypatch, name, files):
    """Install a focus group and activate it, without touching real config."""
    groups = dict(sh.GROUPS)
    groups[name] = Group(files=tuple(files), path="", description="")
    monkeypatch.setattr(sh, "GROUPS", groups)
    monkeypatch.setenv("AUDIT_FOCUS_GROUP", name)


def test_no_focus_is_a_noop(monkeypatch):
    monkeypatch.delenv("AUDIT_FOCUS_GROUP", raising=False)
    assert sh._active_paths() is None
    assert sh.should_audit(Path("anything.py")) is True


def test_active_paths_read_at_call_time(monkeypatch):
    """Regression: env var set AFTER import must still take effect."""
    assert sh._active_paths() is None  # import-time state: no focus
    _register(monkeypatch, "probe", ["quality.py"])
    assert sh._active_paths() == {"quality.py"}


def test_should_audit_honours_focus(monkeypatch):
    _register(monkeypatch, "probe", ["quality.py", "cli.py"])
    assert sh.should_audit(Path("src/audit_code/quality.py")) is True
    assert sh.should_audit(Path("src/audit_code/cli.py")) is True
    assert sh.should_audit(Path("src/audit_code/runtime.py")) is False


def test_unknown_group_is_a_noop(monkeypatch):
    monkeypatch.setenv("AUDIT_FOCUS_GROUP", "does-not-exist")
    assert sh._active_paths() is None


def test_skip_parts_win_over_focus(monkeypatch):
    """A skipped dir is rejected even if its name is in the focus group."""
    _register(monkeypatch, "probe", ["quality.py"])
    assert sh.should_audit(Path("__pycache__/quality.py")) is False


def test_parse_group_valid_forms():
    name, g = sh._parse_group("fast=[a.py, b.py]", "def_path")
    assert name == "fast" and g.files == ("a.py", "b.py") and g.path == "def_path"

    name, g = sh._parse_group("slow=[x.py] /somewhere", "def_path")
    assert name == "slow" and g.path == "/somewhere"  # leading slash preserved

    name, g = sh._parse_group("win=[x.py] C:\\proj", "")
    assert g.path == "C:\\proj"  # Windows drive path preserved

    name, g = sh._parse_group("d=[x.py] /p | full sweep", "")
    assert g.path == "/p" and g.description == "full sweep"

    # description WITHOUT a path must still parse (regression: used to be dropped)
    name, g = sh._parse_group("e=[x.py]  | just desc", "")
    assert name == "e" and g.files == ("x.py",) and g.description == "just desc"


def test_parse_group_rejects_malformed():
    assert sh._parse_group("not a group line", "") is None
    assert sh._parse_group("bad=[x.py", "") is None  # no closing bracket


def test_quality_py_files_respects_focus(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (root / "drop.py").write_text("y = 2\n", encoding="utf-8")

    # no focus: both production files are collected
    monkeypatch.delenv("AUDIT_FOCUS_GROUP", raising=False)
    prod, _ = q._py_files(root, root / "tests")
    assert {p.name for p in prod} == {"keep.py", "drop.py"}

    # focus on keep.py: Q0-Q8 now see only that file
    _register(monkeypatch, "probe", ["keep.py"])
    prod, _ = q._py_files(root, root / "tests")
    assert {p.name for p in prod} == {"keep.py"}
