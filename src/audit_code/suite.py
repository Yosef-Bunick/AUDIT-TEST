"""suite.py — is the TEST SUITE itself healthy? (runs pytest and diagnoses)

The other audits read code; this one RUNS the suite and applies the manual
triage playbook that keeps getting rediscovered by hand:

  S1  [HIGH]   failed/errored tests, each CLASSIFIED by a solo re-run
  S2  [HIGH]   suite verdict missing (crashed reporter or pytest died)
  S3  [MEDIUM] collection errors
  S4  [MEDIUM] import-drift skips (tests silently guarding nothing)
  S5  [INFO]   skip inventory grouped by reason
"""

import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from audit_code.models import (
    AuditResult,
    AuditStatus,
    Finding,
    Severity,
)

PYTEST_ARGS = ["-q", "--tb=no", "-p", "no:logfire", "-p", "no:deepeval"]
FULL_SUITE_TIMEOUT = 900
SOLO_TIMEOUT = 180
MAX_SOLO_RERUNS = 10
TESTS_DIR = "tests"

VERDICT_RE = re.compile(r"(\d+) passed|(\d+) failed|no tests ran", re.MULTILINE)
FAILED_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)", re.MULTILINE)
SKIP_RE = re.compile(r"^SKIPPED \[(\d+)\][^:]*:\d+: (.+)$", re.MULTILINE)
COLLECT_ERR_RE = re.compile(
    r"ERROR collecting\s+(\S+\.py)|^ERROR(?:S)?\s+(\S+\.py)\b.*$", re.MULTILINE
)
IMPORT_DRIFT_RE = re.compile(
    r"not import ?able|unavailable:|No module named|not present|"
    r"no .* entrypoint found",
    re.IGNORECASE,
)
ENV_SKIP_RE = re.compile(
    r"firejail|LIVE_ENGINE_TESTS|not installed|requires? (network|docker|gh)|"
    r"platform|windows|linux only",
    re.IGNORECASE,
)


def _run_pytest(target: str, cwd: Path, timeout: int) -> tuple[str, int]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, *PYTEST_ARGS],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
        timeout=timeout,
    )
    return (proc.stdout or "") + "\n" + (proc.stderr or ""), proc.returncode


def _parse(output: str, returncode: int) -> dict:
    passed = failed = 0
    verdict_seen = False
    for m in VERDICT_RE.finditer(output):
        verdict_seen = True
        if m.group(1):
            passed = int(m.group(1))
        if m.group(2):
            failed = int(m.group(2))
    failures = []
    for m in FAILED_RE.finditer(output):
        failures.append((m.group(1), m.group(2)))
    skips = Counter()
    for m in SKIP_RE.finditer(output):
        skips[m.group(2).strip()] += int(m.group(1))
    return {
        "passed": passed,
        "failed": failed,
        "verdict_seen": verdict_seen,
        "failures": failures,
        "skips": skips,
        "returncode": returncode,
    }


def _classify_solo(target_root: Path, nodeid: str) -> str:
    try:
        out, rc = _run_pytest(nodeid, target_root, SOLO_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "hang (timed out solo)"
    if rc == 0:
        return "POLLUTION - passes solo, fails in suite (isolation bug)"
    if rc == 5 or "no tests ran" in out:
        return "vanished solo (parametrized/collection quirk)"
    return "real - fails solo too"


def _baseline_failures(target_root: Path) -> set | None:
    tmp = Path(tempfile.mkdtemp(prefix="audit_suite_head_"))
    wt = tmp / "head_wt"
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(target_root),
            timeout=120,
        )
        if add.returncode != 0:
            return None
        out, rc = _run_pytest(TESTS_DIR, wt, FULL_SUITE_TIMEOUT)
        return {nid for _, nid in _parse(out, rc)["failures"]}
    except subprocess.TimeoutExpired:
        return None
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            capture_output=True,
            text=True,
            cwd=str(target_root),
            timeout=60,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def run(
    target_root: Path, fast: bool = False, baseline: bool = False, strict: bool = True
) -> AuditResult:
    """Run the suite audit.

    Args:
        target_root: path to the project being audited.
        fast: skip solo re-run classification.
        baseline: diff against HEAD baseline.
        strict: exit non-zero on HIGH findings.
    """
    findings: list[Finding] = []
    crashed = False
    stdout_lines: list[str] = []

    tests_dir = target_root / TESTS_DIR

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    header = f"running: pytest {TESTS_DIR} {' '.join(PYTEST_ARGS)}  (timeout {FULL_SUITE_TIMEOUT}s)"
    stdout_lines.append(header)

    try:
        out, rc = _run_pytest(TESTS_DIR, target_root, FULL_SUITE_TIMEOUT)
    except subprocess.TimeoutExpired:
        finding = Finding(
            rule_id="S2",
            severity=Severity.HIGH,
            message=f"suite exceeded {FULL_SUITE_TIMEOUT}s — hung test or deadlock",
            source="suite",
        )
        findings.append(finding)
        return AuditResult(
            audit_id="suite",
            status=AuditStatus.FAIL,
            findings=findings,
            high=1,
            stdout="\n".join(stdout_lines),
        )

    r = _parse(out, rc)
    high = med = info = 0

    # S1: failures, classified
    sec = f"S1 [HIGH] failed/errored tests (solo re-run classified) - {len(r['failures'])} finding(s)"
    stdout_lines.append("=" * 74)
    stdout_lines.append(sec)
    stdout_lines.append("=" * 74)

    head_fails = None
    if baseline and r["failures"]:
        head_fails = _baseline_failures(target_root)

    for i, (kind, nodeid) in enumerate(r["failures"]):
        high += 1
        tag = ""
        if head_fails is not None:
            tag = (
                "  << REGRESSION (passes at HEAD)"
                if nodeid not in head_fails
                else "  (pre-existing: also fails at HEAD)"
            )
        cls = ""
        if not fast:
            if i < MAX_SOLO_RERUNS:
                cls = f"  [{_classify_solo(target_root, nodeid)}]"
            else:
                cls = "  [unclassified - past solo re-run cap]"
        line = f"  {kind:6} {nodeid}{cls}{tag}"
        stdout_lines.append(line)
        findings.append(
            Finding(
                rule_id="S1",
                severity=Severity.HIGH,
                message=f"{kind}: {nodeid}",
                source="suite",
            )
        )

    if head_fails is not None:
        fixed = head_fails - {nid for _, nid in r["failures"]}
        if fixed:
            stdout_lines.append(f"  [baseline] fixed vs HEAD ({len(fixed)}):")
            for nid in sorted(fixed):
                stdout_lines.append(f"    {nid}")

    stdout_lines.append("")

    # S2: verdict integrity
    s2_findings = []
    if not r["verdict_seen"]:
        s2_findings.append(
            "no 'N passed/failed' verdict line in output - a "
            "terminal-summary plugin crashed mid-report"
        )
    if rc >= 2:
        s2_findings.append(
            f"pytest exit code {rc} (2=interrupted, 3=internal "
            f"error, 4=usage, 5=no tests)"
        )
    sec = f"S2 [HIGH] suite verdict missing / abnormal exit - {len(s2_findings)} finding(s)"
    stdout_lines.append("=" * 74)
    stdout_lines.append(sec)
    stdout_lines.append("=" * 74)
    high += len(s2_findings)
    for f in s2_findings:
        stdout_lines.append(f"  {f}")
        findings.append(
            Finding(rule_id="S2", severity=Severity.HIGH, message=f, source="suite")
        )
    stdout_lines.append("")

    # S3: collection errors
    collect_errs = sorted(
        {
            m.group(1) or m.group(2)
            for m in COLLECT_ERR_RE.finditer(out)
            if "::" not in m.group(0)
        }
    )
    sec = f"S3 [MEDIUM] collection errors - {len(collect_errs)} finding(s)"
    stdout_lines.append("=" * 74)
    stdout_lines.append(sec)
    stdout_lines.append("=" * 74)
    med += len(collect_errs)
    for f in collect_errs:
        stdout_lines.append(f"  {f}")
        findings.append(
            Finding(rule_id="S3", severity=Severity.MEDIUM, message=f, source="suite")
        )
    stdout_lines.append("")

    # S4: import-drift skips
    drift = {
        reason: n
        for reason, n in r["skips"].items()
        if IMPORT_DRIFT_RE.search(reason) and not ENV_SKIP_RE.search(reason)
    }
    sec = f"S4 [MEDIUM] import-drift skips - {len(drift)} finding(s)"
    stdout_lines.append("=" * 74)
    stdout_lines.append(sec)
    stdout_lines.append("=" * 74)
    med += len(drift)
    for reason, n in sorted(drift.items(), key=lambda kv: -kv[1]):
        stdout_lines.append(f"  {n:4} x  {reason[:100]}")
        findings.append(
            Finding(
                rule_id="S4",
                severity=Severity.MEDIUM,
                message=f"{reason} ({n}x)",
                source="suite",
            )
        )
    stdout_lines.append("")

    # S5: skip inventory
    env_skips = {rn: n for rn, n in r["skips"].items() if rn not in drift}
    sec = f"S5 [INFO] skip inventory - {len(env_skips)} finding(s)"
    stdout_lines.append("=" * 74)
    stdout_lines.append(sec)
    stdout_lines.append("=" * 74)
    info += len(env_skips)
    for reason, n in sorted(env_skips.items(), key=lambda kv: -kv[1])[:15]:
        stdout_lines.append(f"  {n:4} x  {reason[:100]}")
    stdout_lines.append("")

    # Summary
    total_skipped = sum(r["skips"].values())
    stdout_lines.append("=" * 74)
    stdout_lines.append(
        f"suite: {r['passed']} passed, {len(r['failures'])} failed, "
        f"{total_skipped} skipped (exit {rc})"
    )
    stdout_lines.append("")
    stdout_lines.append("=" * 74)
    stdout_lines.append(
        f"SUMMARY  HIGH: {high}   MEDIUM: {med}   INFO: {info}   suppressed: 0"
    )

    status = (
        AuditStatus.FAIL
        if high
        else (AuditStatus.WARN if (med or info) else AuditStatus.PASS)
    )

    return AuditResult(
        audit_id="suite",
        status=status,
        findings=findings,
        high=high,
        medium=med,
        info=info,
        stdout="\n".join(stdout_lines),
        completed=not crashed,
    )


# Standalone entry point
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None, help="project root")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    root = Path(args.path).resolve() if args.path else Path.cwd()
    result = run(root, fast=args.fast, baseline=args.baseline, strict=args.strict)
    print(result.stdout)
    if args.strict and result.high:
        sys.exit(1)
