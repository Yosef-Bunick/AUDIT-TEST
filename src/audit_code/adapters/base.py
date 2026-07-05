"""Shared machinery for language adapters.

Contract (fail-closed, honest):
  - detect()        marker file at the root OR any source file anywhere.
  - syntax_check()  runs a REAL check. Three honest outcomes only:
                      PASS  files were actually checked and are clean
                      FAIL/WARN  files were checked and have findings
                      SKIP  no files, or the needed tool is missing
                    A missing tool is never reported as PASS.
  - test_command()  the native test invocation for the project, or None.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

from audit_code.config import ADAPTER_EXCLUDE_DIRS, TOOL_TIMEOUT
from audit_code.models import AuditResult, AuditStatus, Finding, Severity

# Per-file checkers (one subprocess per file) stop after this many files so a
# huge repo cannot stall the audit; the result notes the truncation.
MAX_PER_FILE_CHECKS = (
    400  # needs fix (duplicated from config.py — import from there instead)
)


def which(name: str) -> str | None:
    """Locate an executable on PATH (indirection point for tests)."""
    return shutil.which(name)


def _load_project_excludes(root: Path) -> set[str]:
    """Load .audit-test-ignore patterns from a project root.

    Returns extra dir/file name patterns to exclude.  Patterns are exact
    name matches (not substrings), # for comments.
    """
    ignore_file = root / ".audit-test-ignore"
    if not ignore_file.exists():
        return set()
    extras: set[str] = set()
    try:
        for line in ignore_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            extras.add(line)
    except OSError:
        pass
    return extras


def run_tool(cmd: list, cwd: Path, timeout: int = TOOL_TIMEOUT) -> tuple[int, str, str]:
    """Run an external tool; returns (rc, stdout, stderr). Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"[timed out after {timeout}s]"
    except OSError as e:
        return -2, "", f"[failed to launch: {e}]"


def iter_source_files(
    root: Path, extensions: tuple, extra_excludes: set[str] | None = None
):
    """Yield files under root (root-level included) with pruned walk."""
    excludes = ADAPTER_EXCLUDE_DIRS
    if extra_excludes:
        excludes = excludes | extra_excludes
    exts = tuple(extensions)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for fn in filenames:
            if fn.endswith(exts):
                yield Path(dirpath) / fn


class LanguageAdapter:
    """Base adapter. Subclasses set language/extensions/markers and implement
    check_files() with a real syntax check."""

    language: str = ""
    extensions: tuple = ()
    marker_files: tuple = ()
    tool_hint: str = ""  # how to install the checker, shown on SKIP

    @classmethod
    def audit_id(cls) -> str:
        return f"{cls.language}-syntax"

    @classmethod
    def detect(cls, target_root: Path) -> bool:
        for marker in cls.marker_files:
            if (target_root / marker).exists():
                return True
        root = target_root.resolve()
        extras = _load_project_excludes(root)
        return next(iter_source_files(root, cls.extensions, extras), None) is not None

    @classmethod
    def collect_files(cls, target_root: Path) -> list:
        root = target_root.resolve()
        extras = _load_project_excludes(root)
        return sorted(iter_source_files(root, cls.extensions, extras))

    @classmethod
    def syntax_check(cls, target_root: Path) -> AuditResult:
        root = target_root.resolve()
        files = cls.collect_files(root)
        if not files:
            return cls.skip(f"no {cls.language} source files found")
        return cls.check_files(root, files)

    @classmethod
    def check_files(cls, root: Path, files: list) -> AuditResult:
        raise NotImplementedError(f"{cls.__name__} must implement check_files()")

    @staticmethod
    def test_command(target_root: Path) -> list | None:
        return None

    # ── result helpers ──

    @classmethod
    def skip(cls, reason: str, tool_missing: bool = False) -> AuditResult:
        msg = reason
        if tool_missing and cls.tool_hint:
            msg = f"{reason} ({cls.tool_hint})"
        return AuditResult(
            audit_id=cls.audit_id(),
            status=AuditStatus.SKIP,
            completed=True,
            tool_missing=tool_missing,
            stdout=msg,
        )

    @classmethod
    def finding(
        cls,
        message: str,
        file: str | None = None,
        line: int | None = None,
        severity: Severity = Severity.HIGH,
    ) -> Finding:
        return Finding(
            rule_id=cls.audit_id(),
            severity=severity,
            message=message[:300],
            file=file,
            line=line,
            language=cls.language,
            source="adapter",
        )

    @classmethod
    def result(cls, findings: list, notes: list) -> AuditResult:
        has_high = any(f.severity == Severity.HIGH for f in findings)
        has_med = any(f.severity == Severity.MEDIUM for f in findings)
        status = (
            AuditStatus.FAIL
            if has_high
            else (AuditStatus.WARN if has_med else AuditStatus.PASS)
        )
        lines = list(notes)
        for f in findings[:50]:
            loc = f"{f.file}:{f.line}" if f.file else ""
            lines.append(f"  [{f.severity.value}] {loc}  {f.message}".rstrip())
        if len(findings) > 50:
            lines.append(f"  ... {len(findings) - 50} more finding(s)")
        return AuditResult(
            audit_id=cls.audit_id(),
            status=status,
            findings=findings,
            completed=True,
            stdout="\n".join(lines),
        )


class TimeBudget:
    """Wall-clock budget for per-file checker loops."""

    def __init__(self, seconds: int = TOOL_TIMEOUT):
        self.deadline = time.monotonic() + seconds

    def exhausted(self) -> bool:
        return time.monotonic() > self.deadline


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
