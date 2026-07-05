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
