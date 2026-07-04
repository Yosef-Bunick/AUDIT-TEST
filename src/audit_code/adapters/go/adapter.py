"""Go adapter — `gofmt -l -e` parses every file (real syntax check); parse
errors land on stderr as path:line:col, unformatted files on stdout."""

import re
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which
from audit_code.models import Severity

_ERR = re.compile(r"^(.*?\.go):(\d+):(\d+):\s*(.*)$")


class GoAdapter(LanguageAdapter):
    """Language adapter for Go projects."""

    language = "go"
    extensions = (".go",)
    marker_files = ("go.mod",)
    tool_hint = "install the Go toolchain from go.dev"

    @classmethod
    def check_files(cls, root: Path, files: list):
        gofmt = which("gofmt")
        if not gofmt:
            return cls.skip("gofmt not found — cannot check syntax", True)

        rc, out, err = run_tool([gofmt, "-l", "-e", "."], root)
        if rc == -2:
            return cls.skip(f"gofmt failed to launch: {err}", True)

        findings = []
        for ln in err.splitlines():
            m = _ERR.match(ln.strip())
            if m:
                findings.append(
                    cls.finding(m.group(4), file=m.group(1), line=int(m.group(2)))
                )
        unformatted = [ln.strip() for ln in out.splitlines() if ln.strip()]
        for uf in unformatted:
            findings.append(
                cls.finding("not gofmt-formatted", file=uf, severity=Severity.MEDIUM)
            )
        notes = [f"{len(files)} Go file(s) parsed via gofmt -l -e"]
        if unformatted:
            notes.append(f"{len(unformatted)} file(s) have formatting drift")
        return cls.result(findings, notes)

    @staticmethod
    def test_command(target_root: Path) -> list | None:
        go = which("go")
        if go and (target_root / "go.mod").exists():
            return [go, "test", "./..."]
        return None
