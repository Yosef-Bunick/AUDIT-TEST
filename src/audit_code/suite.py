"""suite.py — is the TEST SUITE itself healthy? (runs pytest and diagnoses)"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from audit_code.config import (
    FULL_SUITE_TIMEOUT,
    MAX_SOLO_RERUNS,
    SOLO_TIMEOUT,
)
from audit_code.models import AuditResult, AuditStatus, Finding, Severity

PYTEST_ARGS = ["-q", "--tb=no", "-p", "no:logfire", "-p", "no:deepeval"]
TESTS_DIR = "tests"

VERDICT_RE = re.compile(r"(\d+) passed|(\d+) failed|no tests ran", re.MULTILINE)
FAILED_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)", re.MULTILINE)
SKIP_RE = re.compile(r"^SKIPPED \[(\d+)\][^:]*:\d+: (.+)$", re.MULTILINE)
COLLECT_ERR_RE = re.compile(
    r"ERROR collecting\s+(\S+\.py)|^ERROR(?:S)?\s+(\S+\.py)\b.*$",
    re.MULTILINE,
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


def _run_pytest(
    target: str, cwd: Path, timeout: int, cov_file: Path | None = None
) -> tuple[str, int]:
    """Run pytest. When cov_file is given, run under coverage and write the
    data there — so the quality audit can reuse this one run for its Q5
    execution-proof instead of running the whole suite a second time."""
    if cov_file is not None:
        cmd = [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--source={cwd}",
            "-m",
            "pytest",
            target,
            *PYTEST_ARGS,
        ]
        env = dict(os.environ, COVERAGE_FILE=str(cov_file))
    else:
        cmd = [sys.executable, "-m", "pytest", target, *PYTEST_ARGS]
        env = None
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
        timeout=timeout,
        env=env,
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
    failures = [(m.group(1), m.group(2)) for m in FAILED_RE.finditer(output)]
    skips: "Counter[str]" = Counter()
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


# ── Report builders (each <120 lines to avoid DG1) ────────────────────────


def _s1_failures(
    target_root: Path,
    r: dict,
    findings: list[Finding],
    stdout_lines: list[str],
    fast: bool,
    baseline: bool,
) -> int:
    high = 0
    sec = f"S1 [HIGH] failed/errored tests (solo re-run classified) - {len(r['failures'])} finding(s)"
    stdout_lines.extend(("=" * 74, sec, "=" * 74))
    head_fails = (
        _baseline_failures(target_root) if (baseline and r["failures"]) else None
    )
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
            cls = (
                f"  [{_classify_solo(target_root, nodeid)}]"
                if i < MAX_SOLO_RERUNS
                else "  [unclassified - past solo re-run cap]"
            )
        stdout_lines.append(f"  {kind:6} {nodeid}{cls}{tag}")
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
    return high


def _s2_verdict(
    r: dict,
    rc: int,
    findings: list[Finding],
    stdout_lines: list[str],
) -> int:
    s2 = []
    if not r["verdict_seen"]:
        s2.append(
            "no 'N passed/failed' verdict line in output - a "
            "terminal-summary plugin crashed mid-report"
        )
    if rc >= 2:
        s2.append(
            f"pytest exit code {rc} (2=interrupted, 3=internal error, "
            f"4=usage, 5=no tests)"
        )
    sec = f"S2 [HIGH] suite verdict missing / abnormal exit - {len(s2)} finding(s)"
    stdout_lines.extend(("=" * 74, sec, "=" * 74))
    for f in s2:
        stdout_lines.append(f"  {f}")
        findings.append(
            Finding(rule_id="S2", severity=Severity.HIGH, message=f, source="suite")
        )
    stdout_lines.append("")
    return len(s2)


def _s3_s5_report(
    out: str,
    r: dict,
    findings: list[Finding],
    stdout_lines: list[str],
) -> tuple[int, int]:
    med = info = 0
    # S3: collection errors
    collect_errs = sorted(
        {
            m.group(1) or m.group(2)
            for m in COLLECT_ERR_RE.finditer(out)
            if "::" not in m.group(0)
        }
    )
    sec = f"S3 [MEDIUM] collection errors - {len(collect_errs)} finding(s)"
    stdout_lines.extend(("=" * 74, sec, "=" * 74))
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
    stdout_lines.extend(("=" * 74, sec, "=" * 74))
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
    stdout_lines.extend(("=" * 74, sec, "=" * 74))
    info += len(env_skips)
    for reason, n in sorted(env_skips.items(), key=lambda kv: -kv[1])[:15]:
        stdout_lines.append(f"  {n:4} x  {reason[:100]}")
    stdout_lines.append("")
    return med, info


# ── Main entry point (<120 lines) ────────────────────────────────────────


def run(
    target_root: Path,
    fast: bool = False,
    baseline: bool = False,
    strict: bool = True,
    cov_file: Path | None = None,
) -> AuditResult:
    """Run the suite audit.

    cov_file: if given, the main suite run is instrumented with coverage and
    its data written there, so the quality audit can reuse it (one test run
    for both audits instead of two)."""
    findings: list[Finding] = []
    stdout_lines: list[str] = []
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # needs fix (broad except — use AttributeError, OSError)
        pass

    header = f"running: pytest {TESTS_DIR} {' '.join(PYTEST_ARGS)}  (timeout {FULL_SUITE_TIMEOUT}s)"
    stdout_lines.append(header)

    try:
        out, rc = _run_pytest(TESTS_DIR, target_root, FULL_SUITE_TIMEOUT, cov_file)
    except subprocess.TimeoutExpired:
        findings.append(
            Finding(
                rule_id="S2",
                severity=Severity.HIGH,
                message=f"suite exceeded {FULL_SUITE_TIMEOUT}s",
                source="suite",
            )
        )
        return AuditResult(
            audit_id="suite",
            status=AuditStatus.FAIL,
            findings=findings,
            high=1,
            stdout="\n".join(stdout_lines),
        )

    r = _parse(out, rc)
    high = _s1_failures(target_root, r, findings, stdout_lines, fast, baseline)
    high += _s2_verdict(r, rc, findings, stdout_lines)
    med, info = _s3_s5_report(out, r, findings, stdout_lines)

    total_skipped = sum(r["skips"].values())
    stdout_lines.extend(
        (
            "=" * 74,
            f"suite: {r['passed']} passed, {len(r['failures'])} failed, "
            f"{total_skipped} skipped (exit {rc})",
            "",
            "=" * 74,
            f"SUMMARY  HIGH: {high}   MEDIUM: {med}   INFO: {info}   suppressed: 0",
        )
    )

    status = (
        AuditStatus.FAIL
        if high
        else AuditStatus.WARN if (med or info) else AuditStatus.PASS
    )
    return AuditResult(
        audit_id="suite",
        status=status,
        findings=findings,
        high=high,
        medium=med,
        info=info,
        stdout="\n".join(stdout_lines),
        completed=True,
    )


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
