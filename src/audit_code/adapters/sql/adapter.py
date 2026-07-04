"""SQL adapter — real parsing requires a dialect-aware parser, so this uses
sqlfluff when installed (ANSI dialect by default). Without it the check is an
honest SKIP: there is no meaningful stdlib-only SQL syntax check, and a fake
pass is worse than a skip."""

import importlib.util
import sys
from pathlib import Path

from audit_code.adapters.base import (
    MAX_PER_FILE_CHECKS,
    LanguageAdapter,
    TimeBudget,
    rel,
    run_tool,
    which,
)
from audit_code.models import Severity


def _sqlfluff_cmd() -> list | None:
    if importlib.util.find_spec("sqlfluff") is not None:
        return [sys.executable, "-m", "sqlfluff"]
    exe = which("sqlfluff")
    return [exe] if exe else None


class SqlAdapter(LanguageAdapter):
    """Language adapter for SQL files."""

    language = "sql"
    extensions = (".sql",)
    marker_files = ()
    tool_hint = "pip install sqlfluff"

    @classmethod
    def check_files(cls, root: Path, files: list):
        base = _sqlfluff_cmd()
        if not base:
            return cls.skip("sqlfluff not installed — cannot parse SQL", True)

        findings, notes = [], []
        budget = TimeBudget()
        checked = 0
        for f in files[:MAX_PER_FILE_CHECKS]:
            if budget.exhausted():
                notes.append(
                    f"time budget exhausted after {checked}/{len(files)} files"
                )
                break
            rc, out, err = run_tool(
                base + ["parse", "--dialect", "ansi", str(f)], root, timeout=60
            )
            checked += 1
            if rc == 0:
                continue
            text = out + "\n" + err
            unparsable = [
                ln.strip() for ln in text.splitlines() if "unparsable" in ln.lower()
            ]
            msg = unparsable[0] if unparsable else f"sqlfluff parse failed (rc={rc})"
            # MEDIUM: the file may simply use a non-ANSI dialect
            findings.append(
                cls.finding(msg, file=rel(f, root), severity=Severity.MEDIUM)
            )
        notes.insert(
            0,
            f"{checked}/{len(files)} SQL file(s) parsed via sqlfluff "
            "(ANSI dialect assumed — dialect-specific syntax may warn)",
        )
        return cls.result(findings, notes)
