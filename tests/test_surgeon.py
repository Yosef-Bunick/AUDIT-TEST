"""Tests for the surgical file editor (surgeon.py).

T1 anchor: "surgeon" — referenced by test imports.
"""

import subprocess
import sys

import pytest

from audit_code import surgeon

# ── parse_range ──────────────────────────────────────────────────────────────


def test_parse_range_single():
    assert surgeon.parse_range("5") == (5, 5)


def test_parse_range_with_end():
    assert surgeon.parse_range("5:10") == (5, 10)


def test_parse_range_invalid():
    with pytest.raises(ValueError):
        surgeon.parse_range("abc")


# ── replace_lines ────────────────────────────────────────────────────────────


def test_replace_single_line(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = surgeon.replace_lines(f, 2, 2, "NEW")
    assert "replaced" in result
    assert f.read_text(encoding="utf-8") == "line1\nNEW\nline3\n"


def test_replace_range(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("a\nb\nc\nd\n", encoding="utf-8")
    surgeon.replace_lines(f, 2, 3, "X")
    assert f.read_text(encoding="utf-8") == "a\nX\nd\n"


def test_replace_auto_indents(tmp_path):
    """Plain text gets auto-indented to match surrounding block."""
    f = tmp_path / "test.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")
    surgeon.replace_lines(f, 2, 2, "return 1")
    assert f.read_text(encoding="utf-8") == "def foo():\n    return 1\n"


def test_replace_utf8(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("old\n", encoding="utf-8")
    surgeon.replace_lines(f, 1, 1, "résumé 🐇")
    assert "résumé 🐇" in f.read_text(encoding="utf-8")


def test_replace_out_of_range(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="out of range"):
        surgeon.replace_lines(f, 5, 5, "x")


def test_replace_end_before_start(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("a\nb\n", encoding="utf-8")
    with pytest.raises(ValueError, match="end"):
        surgeon.replace_lines(f, 2, 1, "x")


def test_replace_dry_run(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("original\n", encoding="utf-8")
    result = surgeon.replace_lines(f, 1, 1, "preview", dry_run=True)
    assert "---" in result
    assert "-original" in result
    assert "+preview" in result
    assert f.read_text(encoding="utf-8") == "original\n"  # unchanged


# ── insert_after ─────────────────────────────────────────────────────────────


def test_insert_after_line(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("a\nb\n", encoding="utf-8")
    surgeon.insert_after(f, 1, "X")
    assert f.read_text(encoding="utf-8") == "a\nX\nb\n"


def test_insert_at_top(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("a\n", encoding="utf-8")
    surgeon.insert_after(f, 0, "# header")
    assert f.read_text(encoding="utf-8") == "# header\na\n"


def test_insert_out_of_range(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("a\n", encoding="utf-8")
    with pytest.raises(ValueError):
        surgeon.insert_after(f, 5, "x")


# ── apply_fixes (batch) ──────────────────────────────────────────────────────


def test_apply_fixes_bottom_to_top(tmp_path):
    """Fixes applied from bottom to top so line numbers stay valid."""
    f = tmp_path / "test.py"
    f.write_text("a\nb\nc\nd\n", encoding="utf-8")
    fixes = [
        {"start": 1, "content": "A"},
        {"start": 4, "content": "D"},
    ]
    results = surgeon.apply_fixes(f, fixes)
    assert len(results) == 2
    assert f.read_text(encoding="utf-8") == "A\nb\nc\nD\n"


def test_apply_fixes_range(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("1\n2\n3\n4\n", encoding="utf-8")
    fixes = [
        {"start": 2, "end": 3, "content": "mid"},
    ]
    surgeon.apply_fixes(f, fixes)
    assert f.read_text(encoding="utf-8") == "1\nmid\n4\n"


# ── CLI integration ──────────────────────────────────────────────────────────


def test_cli_fix_command(tmp_path):
    """audit-test surgeon fix works through the CLI."""
    f = tmp_path / "cli_test.py"
    f.write_text("old\n", encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "audit_code.surgeon",
            "--no-format",
            "fix",
            str(f),
            "1",
            "new",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "replaced" in r.stdout
    assert f.read_text(encoding="utf-8") == "new\n"


def test_cli_dry_run(tmp_path):
    f = tmp_path / "cli_test.py"
    f.write_text("original\n", encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "audit_code.surgeon",
            "--no-format",
            "dry-run",
            str(f),
            "1",
            "preview",
        ],
        capture_output=True,
        text=True,
    )
    assert "---" in r.stdout
    assert f.read_text(encoding="utf-8") == "original\n"  # unchanged


# ── copy_lines (Surgeon v2) ──────────────────────────────────────────────────


def test_copy_lines_basic(tmp_path):
    src = tmp_path / "src.py"
    src.write_text("a\nb\nc\nd\n", encoding="utf-8")
    dest = tmp_path / "dest.py"
    dest.write_text("X\nY\n", encoding="utf-8")
    surgeon.copy_lines(src, 2, 3, dest, 1)
    assert dest.read_text(encoding="utf-8") == "X\nb\nc\nY\n"


def test_copy_lines_at_top(tmp_path):
    src = tmp_path / "src.py"
    src.write_text("head\n", encoding="utf-8")
    dest = tmp_path / "dest.py"
    dest.write_text("body\n", encoding="utf-8")
    surgeon.copy_lines(src, 1, 1, dest, 0)
    assert dest.read_text(encoding="utf-8") == "head\nbody\n"


def test_copy_lines_utf8(tmp_path):
    src = tmp_path / "src.py"
    src.write_text("café 🐇\n", encoding="utf-8")
    dest = tmp_path / "dest.py"
    dest.write_text("x\n", encoding="utf-8")
    surgeon.copy_lines(src, 1, 1, dest, 1)
    assert "café 🐇" in dest.read_text(encoding="utf-8")


def test_copy_lines_out_of_bounds(tmp_path):
    src = tmp_path / "src.py"
    src.write_text("a\n", encoding="utf-8")
    dest = tmp_path / "dest.py"
    dest.write_text("x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="out of bounds"):
        surgeon.copy_lines(src, 1, 5, dest, 0)


def test_copy_lines_dry_run(tmp_path):
    src = tmp_path / "src.py"
    src.write_text("a\nb\n", encoding="utf-8")
    dest = tmp_path / "dest.py"
    dest.write_text("x\n", encoding="utf-8")
    out = surgeon.copy_lines(src, 1, 1, dest, 1, dry_run=True)
    assert "would copy" in out
    assert dest.read_text(encoding="utf-8") == "x\n"  # unchanged


# ── replace_lines_cross ──────────────────────────────────────────────────────


def test_replace_lines_cross(tmp_path):
    src = tmp_path / "src.py"
    src.write_text("NEW1\nNEW2\n", encoding="utf-8")
    dest = tmp_path / "dest.py"
    dest.write_text("keep\nold1\nold2\nkeep2\n", encoding="utf-8")
    surgeon.replace_lines_cross(src, 1, 2, dest, 2, 3)
    assert dest.read_text(encoding="utf-8") == "keep\nNEW1\nNEW2\nkeep2\n"


def test_cli_replace_cross_command(tmp_path):
    src = tmp_path / "s.py"
    src.write_text("A\nB\n", encoding="utf-8")
    dest = tmp_path / "d.py"
    dest.write_text("keep\nx\ny\n", encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "audit_code.surgeon",
            "--no-format",
            "replace-cross",
            str(src),
            "1:2",
            str(dest),
            "2:3",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert dest.read_text(encoding="utf-8") == "keep\nA\nB\n"


# ── find_function ────────────────────────────────────────────────────────────


def test_find_function_in_file(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def a():\n    return 1\n\n\ndef target():\n    return 2\n", "utf-8")
    found = surgeon.find_function(f, "target")
    assert found is not None
    assert found["name"] == "target"
    assert found["block"].strip() == "def target():\n    return 2".strip()


def test_find_function_includes_decorators(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "import functools\n\n\n@functools.cache\ndef g():\n    return 1\n", "utf-8"
    )
    found = surgeon.find_function(f, "g")
    assert found is not None
    assert found["block"].startswith("@functools.cache")


def test_find_function_searches_project_dir(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "deep.py").write_text("def buried():\n    return 1\n", "utf-8")
    found = surgeon.find_function(tmp_path, "buried")
    assert found is not None
    assert found["file"].name == "deep.py"


def test_find_function_missing_returns_none(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def a():\n    return 1\n", "utf-8")
    assert surgeon.find_function(f, "nope") is None


# ── import-analysis helpers ──────────────────────────────────────────────────


def test_import_bindings():
    import ast

    mod = ast.parse("import numpy as np\nimport os.path\nfrom x import y as z\n")
    got = []
    for node in mod.body:
        got.extend(surgeon._import_bindings(node))
    assert got == ["np", "os", "z"]


def test_names_used():
    import ast

    fn = ast.parse("def f():\n    return np.array(os.x)\n").body[0]
    used = surgeon._names_used(fn)
    assert "np" in used and "os" in used


# ── port_feature ─────────────────────────────────────────────────────────────


def _port_src(tmp_path):
    src = tmp_path / "src.py"
    src.write_text(
        '"""src."""\n'
        "import math\n"
        "import os\n\n\n"
        "def area(r):\n"
        "    return math.pi * r * r\n",
        encoding="utf-8",
    )
    return src


def test_port_adds_only_needed_import(tmp_path):
    src = _port_src(tmp_path)
    dest = tmp_path / "dest.py"
    dest.write_text('"""dest."""\nimport os\n\n\ndef e():\n    return 1\n', "utf-8")
    result = surgeon.port_feature(src, dest, "area", fmt=False)
    assert result["imports_added"] == ["import math"]  # os already present
    text = dest.read_text(encoding="utf-8")
    assert "def area(r):" in text
    assert text.count("import os") == 1


def test_port_appends_function_after_existing(tmp_path):
    src = _port_src(tmp_path)
    dest = tmp_path / "dest.py"
    dest.write_text('"""dest."""\nimport math\n\n\ndef e():\n    return 1\n', "utf-8")
    surgeon.port_feature(src, dest, "area", fmt=False)
    text = dest.read_text(encoding="utf-8")
    assert text.index("def e(") < text.index("def area(")


def test_port_missing_function_returns_error(tmp_path):
    src = _port_src(tmp_path)
    dest = tmp_path / "dest.py"
    dest.write_text("x = 1\n", encoding="utf-8")
    result = surgeon.port_feature(src, dest, "ghost", fmt=False)
    assert "error" in result


def test_port_dry_run_does_not_write(tmp_path):
    src = _port_src(tmp_path)
    dest = tmp_path / "dest.py"
    dest.write_text("x = 1\n", encoding="utf-8")
    result = surgeon.port_feature(src, dest, "area", dry_run=True)
    assert result["dry_run"] is True
    assert dest.read_text(encoding="utf-8") == "x = 1\n"


# ── CLI: copy / port ─────────────────────────────────────────────────────────


def test_cli_copy_command(tmp_path):
    src = tmp_path / "s.py"
    src.write_text("a\nb\n", encoding="utf-8")
    dest = tmp_path / "d.py"
    dest.write_text("x\n", encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "audit_code.surgeon",
            "--no-format",
            "copy",
            str(src),
            "1:1",
            str(dest),
            "0",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert dest.read_text(encoding="utf-8") == "a\nx\n"


def test_cli_port_command(tmp_path):
    src = _port_src(tmp_path)
    dest = tmp_path / "d.py"
    dest.write_text('"""d."""\n\n\ndef keep():\n    return 0\n', "utf-8")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "audit_code.surgeon",
            "--no-format",
            "port",
            str(src),
            str(dest),
            "area",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "def area(r):" in dest.read_text(encoding="utf-8")
