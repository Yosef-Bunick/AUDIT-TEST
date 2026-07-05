"""Tests for audit_runtime's detectors — the operational-failure audit.

Each detector is driven with crafted source that trips (or deliberately does
not trip) the specific rule, so these verify the checks actually fire, not just
that the code imports. `main` is run end-to-end against a tmp fixture ROOT.
"""

import ast

from audit_code import audit_runtime as art


def _trees(monkeypatch, tmp_path, files: dict[str, str]):
    """Point the module ROOT at tmp_path and return {Path: AST} for `files`."""
    monkeypatch.setattr(art, "ROOT", tmp_path)
    trees = {}
    for relname, src in files.items():
        p = tmp_path / relname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        trees[p] = ast.parse(src)
    return trees


# ── R9: tool registry parity ──


def test_tool_parity_detects_ghost_and_unknown(monkeypatch, tmp_path):
    registry = (
        "TOOL_DEFINITIONS = [\n"
        '    {"name": "wired"},\n'
        '    {"name": "only_defined"},\n'
        "]\n"
        "def run_tool(name):\n"
        '    if name == "wired":\n'
        "        return 1\n"
        '    if name == "ghost":\n'
        "        return 2\n"
    )
    trees = _trees(monkeypatch, tmp_path, {"tools/registry.py": registry})
    ghost, unknown, reg_found = art.audit_tool_parity(trees)
    assert reg_found is True
    assert "ghost" in ghost  # dispatched but not defined
    assert "only_defined" in unknown  # defined but never dispatched
    assert "wired" not in ghost and "wired" not in unknown


def test_tool_parity_reports_absent_registry(monkeypatch, tmp_path):
    trees = _trees(monkeypatch, tmp_path, {"main.py": "x = 1\n"})
    ghost, unknown, reg_found = art.audit_tool_parity(trees)
    assert reg_found is False
    assert ghost == [] and unknown == []


# ── R13: advisory gates ──


def test_gates_flags_discarded_verdict(monkeypatch, tmp_path):
    src = (
        "def check_budget(n):\n"
        "    return n < 10\n"
        "def run():\n"
        "    check_budget(5)\n"  # result discarded
    )
    _trees(monkeypatch, tmp_path, {"m.py": src})
    findings = art.audit_gates({tmp_path / "m.py": ast.parse(src)})
    assert any("check_budget" in msg for _, _, msg in findings)


def test_gates_exempts_a_gate_that_raises(monkeypatch, tmp_path):
    # A gate that raises enforces itself — calling it bare is correct.
    src = (
        "def check_hard(n):\n"
        "    if n < 0:\n"
        "        raise ValueError('bad')\n"
        "    return True\n"
        "def run():\n"
        "    check_hard(5)\n"
    )
    _trees(monkeypatch, tmp_path, {"m.py": src})
    findings = art.audit_gates({tmp_path / "m.py": ast.parse(src)})
    assert not any("check_hard" in msg for _, _, msg in findings)


# ── R10: prompt & hook parity ──


def test_prompts_flags_missing_file_and_orphan(monkeypatch, tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "orphan.md").write_text("stale", encoding="utf-8")
    code = 'def build():\n    return prompt("ghost_prompt")\n'
    trees = _trees(monkeypatch, tmp_path, {"caller.py": code})
    missing, orphans, wrong_brain, orphan_raw, unfillable = art.audit_prompts(trees)
    assert "ghost_prompt" in missing  # referenced, but prompts/ghost_prompt.md absent
    assert "orphan" in orphans  # on disk, referenced by nothing


# ── R11: dependency inventory ──


def test_dependencies_split_missing_and_unused(monkeypatch, tmp_path):
    (tmp_path / "requirements.txt").write_text("click\n", encoding="utf-8")
    trees = _trees(monkeypatch, tmp_path, {"app.py": "import requests\n"})
    third, declared, has_req = art.audit_dependencies(trees)
    assert has_req is True
    assert "requests" in third  # imported
    assert "click" in declared  # declared


# ── R1 helper: unbounded loop detection ──


def test_loop_has_exit_true_and_false():
    with_break = ast.parse("while True:\n    if x: break\n").body[0]
    forever = ast.parse("while True:\n    pass\n").body[0]
    assert art.loop_has_exit(with_break) is True
    assert art.loop_has_exit(forever) is False


# ── main: end-to-end against a fixture ROOT ──


def test_main_reports_r1_and_r2(monkeypatch, tmp_path, capsys):
    fixture = (
        "import subprocess\n"
        "def go():\n"
        "    subprocess.run(['x'])\n"  # R2: no timeout=
        "    while True:\n"  # R1: no break/return/raise
        "        pass\n"
    )
    (tmp_path / "hang.py").write_text(fixture, encoding="utf-8")
    monkeypatch.setattr(art, "ROOT", tmp_path)
    monkeypatch.setattr(art.sys, "argv", ["audit_runtime"])
    art.main()  # no --strict → prints, does not sys.exit
    out = capsys.readouterr().out
    assert "SUMMARY" in out
    assert "R1" in out and "R2" in out
    assert "while True" in out
