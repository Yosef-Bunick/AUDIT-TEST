"""Swift adapter — `swiftc -typecheck` syntax check."""

import re
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which

_ERR = re.compile(r"^(.+\.swift):(\d+):(\d+):\s*(?:error|warning):\s*(.+)$")


class SwiftAdapter(LanguageAdapter):
    language = "swift"
    extensions = (".swift",)
    marker_files = ("Package.swift", "*.xcodeproj", "*.xcworkspace")
    tool_hint = "install Swift from swift.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        swiftc = which("swiftc")
        if not swiftc:
            return cls.skip("swiftc not found", True)
        paths = [str(f) for f in files[:200]]
        rc, out, err = run_tool([swiftc, "-typecheck"] + paths, root, timeout=120)
        if rc == -2:
            return cls.skip(f"swiftc failed: {err}", True)
        findings = []
        for ln in (out + "\n" + err).splitlines():
            m = _ERR.match(ln.strip())
            if m:
                findings.append(
                    cls.finding(m.group(4), file=m.group(1), line=int(m.group(2)))
                )
        return cls.result(
            findings, [f"{len(files)} Swift file(s) checked via swiftc -typecheck"]
        )

    @staticmethod
    def test_command(root: Path) -> list | None:
        swift = which("swift")
        if swift and (root / "Package.swift").exists():
            return [swift, "test"]
        return None
