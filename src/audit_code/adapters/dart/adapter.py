"""Dart adapter — `dart analyze` syntax check."""

from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which


class DartAdapter(LanguageAdapter):
    language = "dart"
    extensions = (".dart",)
    marker_files = ("pubspec.yaml",)
    tool_hint = "install the Dart SDK from dart.dev"

    @classmethod
    def check_files(cls, root: Path, files: list):
        dart = which("dart")
        if not dart:
            return cls.skip("dart not found", True)
        rc, out, err = run_tool([dart, "analyze", "."], root, timeout=120)
        if rc == -2:
            return cls.skip(f"dart analyze failed: {err}", True)
        findings = []
        for ln in (out + "\n" + err).splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("Analyzing"):
                findings.append(cls.finding(ln[:200]))
        return cls.result(
            findings, [f"{len(files)} Dart file(s) analyzed via dart analyze"]
        )

    @staticmethod
    def test_command(root: Path) -> list | None:
        dart = which("dart")
        if dart and (root / "pubspec.yaml").exists():
            return [dart, "test"]
        return None
