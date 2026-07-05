"""Shared tool runner for the language-linter integrations.

Every non-Python linter/formatter (ESLint, Prettier, Checkstyle, PMD, go vet,
golangci-lint, clippy, rustfmt, dotnet format, clang-tidy, cppcheck, HTMLHint,
Stylelint) dispatches through `_run_tool`.

Two things this function gets right that a naive wrapper does not:

  * Encoding. Tool output is decoded as UTF-8 with `errors="replace"`. Bare
    `text=True` uses the locale codec (cp1252 on Windows) and raises
    UnicodeDecodeError on the first non-ASCII byte — the exact trap the
    runtime audit's R6 flags.
  * Exit-code semantics. A linter's "I found issues" code differs per tool
    (PMD=4, cppcheck needs --error-exitcode, dotnet format=2). Callers pass
    `violation_codes` so a real issue reads as FAIL/WARN and a bad invocation
    (missing config, wrong args) reads as CRASH instead of a silent pass or a
    fake finding.
"""

import shutil
import subprocess

from audit_code.models import AuditResult, AuditStatus, Finding, Severity


def _run_tool(
    exe_name: str,
    cmd: list[str],
    audit_id: str,
    target_root,
    timeout: int = 300,
    *,
    severity: Severity = Severity.HIGH,
    violation_codes: set[int] | None = None,
) -> AuditResult:
    """Run a CLI linter/formatter and map its exit code to an AuditResult.

    severity: HIGH for linters (a finding blocks the gate), MEDIUM for
        formatters (cosmetic drift must not read as a security-grade HIGH).
    violation_codes: exit codes that mean "issues found". Any OTHER non-zero
        code is a tool CRASH (bad args, missing config), not a finding.
        None means "any non-zero exit is a violation" — for tools whose exit
        code does not separate issues from internal errors.
    """
    exe = shutil.which(exe_name)
    if not exe:
        return AuditResult(
            audit_id=audit_id,
            status=AuditStatus.SKIP,
            tool_missing=True,
            stdout=f"SKIP: {exe_name} not installed",
        )

    cmd = [exe, *cmd[1:]]  # replace the bare name with the resolved path
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(target_root),
        )
    except subprocess.TimeoutExpired:
        return AuditResult(
            audit_id=audit_id,
            status=AuditStatus.CRASH,
            stderr=f"timed out after {timeout}s",
        )
    except OSError as exc:
        return AuditResult(
            audit_id=audit_id,
            status=AuditStatus.CRASH,
            stderr=f"could not run {exe_name}: {exc}",
        )

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    full = f"{out}\n{err}".strip()
    rc = proc.returncode

    if rc == 0:
        return AuditResult(
            audit_id=audit_id,
            status=AuditStatus.PASS,
            stdout=full[:5000] if full else "no output",
        )

    is_violation = rc != 0 if violation_codes is None else rc in violation_codes
    if not is_violation:
        return AuditResult(
            audit_id=audit_id,
            status=AuditStatus.CRASH,
            stdout=full[:5000] if full else "",
            stderr=f"{exe_name} exited {rc} (not a lint-violation code)",
        )

    # Real issues found. Linters fail the gate; formatters only warn.
    status = AuditStatus.FAIL if severity == Severity.HIGH else AuditStatus.WARN
    finding = Finding(
        rule_id=audit_id,
        severity=severity,
        message=f"{exe_name} reported issues (exit {rc})",
        source=audit_id,
    )
    return AuditResult(
        audit_id=audit_id,
        status=status,
        findings=[finding],  # __post_init__ tallies high/medium/info
        stdout=full[:5000] if full else "no output",
    )
