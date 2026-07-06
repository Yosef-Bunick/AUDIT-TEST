"""Tests for audit_wiring's detectors — the "is it connected?" audit.

Crafted inputs trip each check for real: a genuinely dead symbol, a subclass
that drops a parent constant, a stray print() on the agent stdout path, and a
dead config key shadowed by a hardcoded constant.
"""

import ast

from audit_code import audit_wiring as aw


def _trees(monkeypatch, tmp_path, files: dict[str, str]):
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    trees = {}
    for relname, src in files.items():
        p = tmp_path / relname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        trees[p] = ast.parse(src)
    return trees


# ── CHECK 1: dead symbols ──


def test_classify_defs_flags_unreferenced_function(monkeypatch, tmp_path):
    src = "def alive():\n    return 1\n\ndef orphan_func():\n    return 2\n\nalive()\n"
    trees = _trees(monkeypatch, tmp_path, {"m.py": src})
    defs = aw.index_defs(trees)
    refs = aw.index_refs(trees)
    dead, _test_only = aw.classify_defs(defs, refs, {}, set(trees))
    dead_names = {n for n, _sites, _amb in dead}
    assert "orphan_func" in dead_names
    assert "alive" not in dead_names  # called in-file → wired


# ── CHECK 6: override parity (fork drift) ──


def test_overrides_flags_dropped_parent_constant(monkeypatch, tmp_path):
    src = (
        "class Base:\n"
        "    def process(self):\n"
        "        return SPECIALVALUE_CONSTANT\n"
        "\n"
        "class Child(Base):\n"
        "    def process(self):\n"
        "        return 0\n"
    )
    trees = _trees(monkeypatch, tmp_path, {"m.py": src})
    defs = aw.index_defs(trees)
    findings = aw.audit_overrides(trees, defs, set(trees))
    assert any("SPECIALVALUE_CONSTANT" in lost for *_rest, lost in findings), findings


# ── CHECK 9: stdout purity on the agent process path ──


def test_stdout_purity_flags_bare_print_in_run_loop(monkeypatch, tmp_path):
    trees = _trees(
        monkeypatch, tmp_path, {"run_loop.py": "def loop():\n    print('debug')\n"}
    )
    findings, n_reach = aw.audit_stdout_purity(trees)
    assert n_reach >= 1
    assert any("print()" in msg for _f, _ln, msg in findings)


def test_stdout_purity_ignores_non_agent_module(monkeypatch, tmp_path):
    # A plain module not reachable from the agent entry points is not scanned.
    trees = _trees(monkeypatch, tmp_path, {"helper.py": "def h():\n    print('ok')\n"})
    findings, _n = aw.audit_stdout_purity(trees)
    assert findings == []


# ── CHECK 7: shadowed config (dead key ↔ hardcoded twin) ──


def test_shadowed_config_matches_dead_key_to_constant(monkeypatch, tmp_path):
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    p = tmp_path / "providers.py"
    p.write_text("MAX_KEYS_PER_PROVIDER = 5\n", encoding="utf-8")
    prod = {p: p.read_text(encoding="utf-8")}
    findings = aw.audit_shadowed_config(["MAX_KEYS_PER_PROVIDER"], prod)
    assert any(key == "MAX_KEYS_PER_PROVIDER" for key, *_rest in findings)


# ── main: end-to-end against a fixture ROOT ──


def test_main_reports_dead_symbol(monkeypatch, tmp_path, capsys):
    src = "def wired():\n    return 1\n\ndef truly_dead():\n    return 2\n\nwired()\n"
    (tmp_path / "app.py").write_text(src, encoding="utf-8")
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    monkeypatch.setattr(aw.sys, "argv", ["audit_wiring"])
    aw.main()
    out = capsys.readouterr().out
    assert "CHECK 1" in out
    assert "truly_dead" in out
    assert "HIGH-confidence findings" in out


def test_rel_relative_path(monkeypatch, tmp_path):
    """_rel() returns a POSIX path relative to ROOT."""
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    f = tmp_path / "sub" / "file.py"
    f.parent.mkdir()
    f.write_text("x = 1", encoding="utf-8")
    assert aw._rel(f) == "sub/file.py"


def test_rel_outside_root(monkeypatch, tmp_path):
    """_rel() falls back to absolute POSIX path when outside ROOT."""
    monkeypatch.setattr(aw, "ROOT", tmp_path / "nope")
    f = tmp_path / "file.py"
    f.write_text("x = 1", encoding="utf-8")
    result = aw._rel(f)
    assert "\\" not in result  # POSIX separators
    assert result.endswith("file.py")
