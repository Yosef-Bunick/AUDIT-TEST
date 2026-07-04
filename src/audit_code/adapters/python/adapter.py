"""Python adapter — real per-file parse via ast (no external tool needed)."""

import ast
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, rel


class PythonAdapter(LanguageAdapter):
    """Language adapter for Python projects."""

    language = "python"
    extensions = (".py",)
    marker_files = ("pyproject.toml", "setup.py", "setup.cfg")

    @classmethod
    def check_files(cls, root: Path, files: list):
        findings = []
        for f in files:
            try:
                ast.parse(
                    f.read_text(encoding="utf-8", errors="replace"),
                    filename=str(f),
                )
            except SyntaxError as e:
                findings.append(
                    cls.finding(
                        e.msg or "syntax error",
                        file=rel(f, root),
                        line=e.lineno,
                    )
                )
        notes = [f"{len(files)} Python file(s) parsed with ast.parse"]
        return cls.result(findings, notes)

    @staticmethod
    def test_command(target_root: Path) -> list | None:
        if (target_root / "tests").is_dir():
            return ["pytest", "tests", "-q", "--tb=no"]
        return None
