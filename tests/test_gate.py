"""Tests for audit_gate — the per-change G0–G4 verdict.

The mutation engine (_mutation_sites / _apply_mutation) is verified for each bug
class, and g4_mutation is run END-TO-END: it injects real bugs into a fixture
module and the fixture's tests must kill them. The git-worktree orchestration
(main / _run_gates) is left to a separate integration fixture.
"""

import ast

from audit_code import audit_gate as gate

# ── mutation engine: each bug class is found and applied ──


def _first(code, kind):
    tree = ast.parse(code)
    nodes = [n for k, n in gate._mutation_sites(tree, None) if k == kind]
    return tree, nodes


def test_mutation_swaps_comparison():
    tree, nodes = _first("y = a < b\n", "cmp")
    assert nodes
    gate._apply_mutation("cmp", nodes[0])
    assert ">=" in ast.unparse(tree)


def test_mutation_swaps_boolean_operator():
    tree, nodes = _first("y = a and b\n", "bool")
    assert nodes
    gate._apply_mutation("bool", nodes[0])
    assert " or " in ast.unparse(tree)


def test_mutation_swaps_arithmetic():
    tree, nodes = _first("y = a + b\n", "arith")
    assert nodes
    gate._apply_mutation("arith", nodes[0])
    assert "a - b" in ast.unparse(tree)


def test_mutation_flips_boolean_literal():
    tree, nodes = _first("y = True\n", "flip")
    assert nodes
    gate._apply_mutation("flip", nodes[0])
    assert "False" in ast.unparse(tree)


def test_mutation_offbyone_on_int_literal():
    tree, nodes = _first("y = 5\n", "offby1")
    assert nodes
    gate._apply_mutation("offby1", nodes[0])
    assert "6" in ast.unparse(tree)


def test_mutation_sites_respects_changed_lines():
    # Only line 2 is "changed"; the compare on line 1 must be ignored.
    code = "a = x < 1\nb = y > 2\n"
    tree = ast.parse(code)
    sites = gate._mutation_sites(tree, {2})
    assert all(node.lineno == 2 for _kind, node in sites)


# ── helpers ──


def test_changed_defs_selects_touched_def(tmp_path):
    (tmp_path / "m.py").write_text(
        "def foo():\n    a = 1\n    b = 2\n    return a + b\n", encoding="utf-8"
    )
    defs = gate._changed_defs(tmp_path, {"m.py": None})
    assert any(qual.endswith("foo") for _rel, qual, *_rest in defs)


def test_tests_referencing_finds_module(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text("import m\n", encoding="utf-8")
    assert gate._tests_referencing(tmp_path, "m")


# ── G4 end-to-end: real bug injection, tests must kill it ──


def test_g4_mutation_kills_detectable_bugs(tmp_path):
    shadow = tmp_path / "shadow"
    (shadow / "tests").mkdir(parents=True)
    (shadow / "calc.py").write_text(
        "def is_big(n):\n    return n > 10\n", encoding="utf-8"
    )
    # Boundary-covering tests: kill both the comparison swap AND the 10->11
    # off-by-one. `python -m pytest` runs with cwd=shadow, so `import calc` works.
    (shadow / "tests" / "test_calc.py").write_text(
        "from calc import is_big\n"
        "def test_boundaries():\n"
        "    assert is_big(20) is True\n"
        "    assert is_big(11) is True\n"
        "    assert is_big(10) is False\n"
        "    assert is_big(5) is False\n",
        encoding="utf-8",
    )
    changed = {"calc.py": {2}}
    defs = [("calc.py", "is_big", 1, 2, 2)]  # rel, qual, defline, body_start, body_end
    res = gate.g4_mutation(shadow, changed, defs, kill_pct=60)
    assert res[0] is not None, res  # sites existed
    ok, detail, _survivors = res
    assert ok is True, detail
    assert "killed" in detail


def test_g4_mutation_neutral_when_no_sites(tmp_path):
    shadow = tmp_path / "shadow"
    (shadow / "tests").mkdir(parents=True)
    # A body with no mutable logic — only a string return.
    (shadow / "m.py").write_text("def label():\n    return 'hello'\n", encoding="utf-8")
    res = gate.g4_mutation(shadow, {"m.py": {2}}, [("m.py", "label", 1, 2, 2)], 60)
    assert res[0] is None  # nothing to prove → neutral, not a failure
