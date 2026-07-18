# ruff: noqa: S101
import sys
import tempfile

sys.path.insert(0, "/mnt/c/AI/audit/src")
from pathlib import Path

from audit_code.graph import build_graph


def test_kotlin_graph():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        j = root / "src/main/kotlin/com/f"
        j.mkdir(parents=True)
        (j / "A.kt").write_text("package com.f\nimport com.f.B\n")
        (j / "B.kt").write_text("package com.f\n")
        g, _ = build_graph(root)
        assert any("B.kt" in k for k in g.get("src/main/kotlin/com/f/A.kt", set()))


def test_swift_graph():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "App.swift").write_text("import Utils\n")
        (root / "Utils.swift").write_text("struct Utils {}\n")
        g, _ = build_graph(root)
        assert "Utils.swift" in g.get("App.swift", set())


def test_php_graph():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "index.php").write_text("<?php require './lib/db.php';\n")
        (root / "lib").mkdir()
        (root / "lib" / "db.php").write_text("<?php\n")
        g, _ = build_graph(root)
        assert "lib/db.php" in g.get("index.php", set())
