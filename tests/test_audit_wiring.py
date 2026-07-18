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


# ── CHECK 10: dead / test-only modules ──


def _parse(tmp_path, files):
    trees = {}
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        trees[p] = ast.parse(src)
    return trees


def test_find_dead_modules_labels(monkeypatch, tmp_path):
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    prod = _parse(
        tmp_path,
        {
            "app.py": "from mymod import thing\nthing()\n\n"
            "if __name__ == '__main__':\n    thing()\n",
            "mymod.py": "def thing():\n    return 1\n",
            "orphan.py": "def go():\n    return 1\n",
            "helper.py": "def h():\n    return 1\n",
        },
    )
    test = _parse(tmp_path, {"test_helper.py": "from helper import h\n"})
    res = {p.name: lb for p, lb in aw.find_dead_modules(list(prod), prod, test)}
    assert "mymod.py" not in res  # imported by production → wired
    assert "app.py" not in res  # __main__ entry point
    assert res["orphan.py"] == "dead"  # imported by nothing
    assert res["helper.py"] == "test-only"  # imported only by tests


def test_find_dead_modules_dynamic_mention_alive(monkeypatch, tmp_path):
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    prod = _parse(
        tmp_path,
        {
            "loader.py": "import importlib\nimportlib.import_module('plugin')\n",
            "plugin.py": "def go():\n    return 1\n",
        },
    )
    res = {p.name for p, _ in aw.find_dead_modules(list(prod), prod, {})}
    assert "plugin.py" not in res  # name appears as a string in prod → alive


def test_find_dead_modules_relative_import_alive(monkeypatch, tmp_path):
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    prod = _parse(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/util.py": "def u():\n    return 1\n",
            "pkg/user.py": "from .util import u\n\nu()\n\n"
            "if __name__ == '__main__':\n    u()\n",
        },
    )
    res = {p.name for p, _ in aw.find_dead_modules(list(prod), prod, {})}
    assert "util.py" not in res  # relative import keeps it alive
    assert "__init__.py" not in res  # package markers never flagged


def test_find_dead_modules_excludes_alembic_migration(monkeypatch, tmp_path):
    # Alembic loads migration files by scanning the versions/ directory and
    # calling upgrade()/downgrade() by name — never a Python import — so a
    # migration must not be reported as an unwired module.
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    prod = _parse(
        tmp_path,
        {
            "migrations/versions/abc123_init.py": (
                "revision = 'abc123'\n"
                "down_revision = None\n\n"
                "def upgrade():\n    pass\n\n"
                "def downgrade():\n    pass\n"
            ),
        },
    )
    res = {p.name: lb for p, lb in aw.find_dead_modules(list(prod), prod, {})}
    assert "abc123_init.py" not in res


def test_find_dead_modules_excludes_annotated_alembic_migration(monkeypatch, tmp_path):
    # Newer Alembic templates emit `revision: str = "..."` (ast.AnnAssign),
    # not `revision = "..."` (ast.Assign) — both must be recognized.
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    prod = _parse(
        tmp_path,
        {
            "migrations/versions/abc123_init.py": (
                "from typing import Union, Sequence\n"
                "revision: str = 'abc123'\n"
                "down_revision: Union[str, Sequence[str], None] = None\n\n"
                "def upgrade():\n    pass\n\n"
                "def downgrade():\n    pass\n"
            ),
        },
    )
    res = {p.name: lb for p, lb in aw.find_dead_modules(list(prod), prod, {})}
    assert "abc123_init.py" not in res


# ── decorator-wired functions (FastAPI routes/middleware, Alembic) ──


def test_decorator_wired_matches_app_object_not_just_router(monkeypatch, tmp_path):
    src = (
        "@app.get('/health')\n"
        "def health():\n    return 'ok'\n\n"
        "@app.middleware('http')\n"
        "async def access_log(request, call_next):\n"
        "    return await call_next(request)\n"
    )
    trees = _trees(monkeypatch, tmp_path, {"main.py": src})
    defs = aw.index_defs(trees)
    wired = aw._collect_decorator_wired(defs)
    assert "health" in wired
    assert "access_log" in wired


def test_decorator_wired_recognizes_alembic_upgrade_downgrade(monkeypatch, tmp_path):
    src = (
        "revision = 'abc123'\n"
        "down_revision = None\n\n"
        "def upgrade():\n    pass\n\n"
        "def downgrade():\n    pass\n"
    )
    trees = _trees(monkeypatch, tmp_path, {"migrations/versions/abc123_init.py": src})
    defs = aw.index_defs(trees)
    wired = aw._collect_decorator_wired(defs)
    assert "upgrade" in wired
    assert "downgrade" in wired


def test_decorator_wired_recognizes_annotated_alembic_revision(monkeypatch, tmp_path):
    # `revision: str = "..."` (ast.AnnAssign) — the newer Alembic template.
    src = (
        "revision: str = 'abc123'\n"
        "down_revision = None\n\n"
        "def upgrade():\n    pass\n\n"
        "def downgrade():\n    pass\n"
    )
    trees = _trees(monkeypatch, tmp_path, {"migrations/versions/abc123_init.py": src})
    defs = aw.index_defs(trees)
    wired = aw._collect_decorator_wired(defs)
    assert "upgrade" in wired
    assert "downgrade" in wired


def test_decorator_wired_ignores_plain_upgrade_outside_migration(monkeypatch, tmp_path):
    # A function merely named `upgrade` in a non-Alembic file has no
    # `revision = ...` marker, so it must not be auto-marked wired.
    src = "def upgrade():\n    pass\n"
    trees = _trees(monkeypatch, tmp_path, {"tools/version.py": src})
    defs = aw.index_defs(trees)
    wired = aw._collect_decorator_wired(defs)
    assert "upgrade" not in wired


# ── inline `# audit: ok` suppression ──


def test_is_suppressed_matches_single_line_def(monkeypatch, tmp_path):
    p = tmp_path / "m.py"
    p.write_text("def helper():  # audit: ok (internal)\n    return 1\n")
    assert aw._is_suppressed(p, 1) is True


def test_is_suppressed_matches_multiline_signature_close(monkeypatch, tmp_path):
    # The `# audit: ok` comment naturally lands on the line that closes a
    # multi-line signature, not the `def` line the finding is anchored to.
    p = tmp_path / "m.py"
    p.write_text(
        "def helper(\n    a,\n    b,\n):  # audit: ok (internal helper)\n"
        "    return a + b\n"
    )
    assert aw._is_suppressed(p, 1) is True


def test_is_suppressed_false_when_absent(monkeypatch, tmp_path):
    p = tmp_path / "m.py"
    p.write_text("def helper():\n    return 1\n")
    assert aw._is_suppressed(p, 1) is False


def test_main_honors_audit_ok_suppression(monkeypatch, tmp_path, capsys):
    src = "def truly_dead():  # audit: ok (kept for future use)\n" "    return 1\n"
    (tmp_path / "app.py").write_text(src, encoding="utf-8")
    monkeypatch.setattr(aw, "ROOT", tmp_path)
    monkeypatch.setattr(aw.sys, "argv", ["audit_wiring"])
    aw.main()
    out = capsys.readouterr().out
    assert "truly_dead" not in out
    assert "suppressed via" in out
