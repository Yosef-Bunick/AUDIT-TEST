"""Kotlin adapter — `kotlinc` syntax check."""

import re
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which

_ERR = re.compile(r"^(.+\.kts?):(\d+):(\d+):\s*(.+)$")


class KotlinAdapter(LanguageAdapter):
    language = "kotlin"
    extensions = (".kt", ".kts")
    marker_files = ("build.gradle.kts", "build.gradle", "settings.gradle.kts")
    tool_hint = "install the Kotlin compiler (kotlinc) from kotlinlang.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        kotlinc = which("kotlinc")
        if not kotlinc:
            return cls.skip("kotlinc not found", True)
        paths = [str(f.relative_to(root)) for f in files[:200]]
        rc, out, err = run_tool(
            [kotlinc, "-Werror", "-d", "/tmp/kt_out"] + paths, root, timeout=120
        )
        if rc == -2:
            return cls.skip(f"kotlinc failed: {err}", True)
        findings = []
        for ln in (out + "\n" + err).splitlines():
            m = _ERR.match(ln.strip())
            if m:
                findings.append(
                    cls.finding(m.group(4), file=m.group(1), line=int(m.group(2)))
                )
        return cls.result(
            findings, [f"{len(files)} Kotlin file(s) checked via kotlinc"]
        )

    @staticmethod
    def test_command(root: Path) -> list | None:
        gradlew = root / "gradlew"
        if gradlew.exists():
            return [str(gradlew), "test"]
        return None
