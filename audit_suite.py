#!/usr/bin/env python3
"""
audit_suite.py - is the TEST SUITE itself healthy? (runs pytest and diagnoses)

The other audits read code; this one RUNS the suite and applies the manual
triage playbook that keeps getting rediscovered by hand:

  S1  [HIGH]   failed/errored tests, each CLASSIFIED by a solo re-run:
                 - "real"      fails alone too -> genuine engine/test bug
                 - "pollution" passes alone    -> another test leaks state into
                   it (shared DB/module global/env). Fix the ISOLATION, not
                   the test body. (This class: checkpoint._db cached
                   connection defeating _db_path monkeypatches.)
  S2  [HIGH]   suite verdict missing - pytest ran but the final
               "N passed[, M failed]" line never printed. A terminal-summary
               plugin crashed mid-report (rich MarkupError from bracketed
               paths in skip reasons - the deepeval class) or pytest died
               (exit >= 2). A green-LOOKING run with no verdict proves nothing.
  S3  [MEDIUM] collection errors - files pytest could not even import.
  S4  [MEDIUM] import-drift skips - skip reasons like "module 'checkpoint'
               unavailable" mean a test file's import path predates a
               restructure: those tests silently stopped guarding anything
               (the _imp("checkpoint") -> _imp("memory.checkpoint") class).
  S5  [INFO]   skip inventory grouped by reason (env-dependent skips like
               "firejail not installed" or LIVE_ENGINE_TESTS gates are fine -
               this is the audit trail that they ARE those and not S4s).

  --baseline   also run the suite in a THROWAWAY git worktree at HEAD and
               diff: a test failing here but passing at HEAD is a REGRESSION
               you introduced (HIGH); one failing in both is pre-existing
               (reported under S1 with that tag); one fixed here is INFO.
               Doubles the runtime - use when the working tree is dirty and
               you need to know what YOUR changes broke.

Usage:
  python audit/audit_suite.py                 # run suite + triage
  python audit/audit_suite.py --baseline      # + HEAD-worktree regression diff
  python audit/audit_suite.py --strict        # exit 1 on any HIGH
  python audit/audit_suite.py --fast          # skip solo re-run classification

Wired into run_all_audits.py. Runtime ~= one full suite run (plus one solo
re-run per failure, plus a second full run with --baseline).
"""

import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from audit_config import FULL_SUITE_TIMEOUT, MAX_SOLO_RERUNS, SOLO_TIMEOUT

ROOT = Path(__file__).parent.parent
TESTS_DIR = "tests"
PYTEST_ARGS = ["-q", "--tb=no", "-p", "no:logfire", "-p", "no:deepeval"]

VERDICT_RE = re.compile(r"(\d+) passed|(\d+) failed|no tests ran", re.MULTILINE)
FAILED_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)", re.MULTILINE)
SKIP_RE = re.compile(r"^SKIPPED \[(\d+)\][^:]*:\d+: (.+)$", re.MULTILINE)
# Two shapes across pytest versions/sections: the ERRORS-block header
# ("ERROR collecting tests/test_x.py" / "___ ERROR collecting ... ___") and
# the -ra short-summary line ("ERROR tests/test_x.py").
COLLECT_ERR_RE = re.compile(
    r"ERROR collecting\s+(\S+\.py)|^ERROR(?:S)?\s+(\S+\.py)\b.*$", re.MULTILINE
)
# Skip reasons that mean "the test file's imports drifted", not "environment"
IMPORT_DRIFT_RE = re.compile(
    r"not import ?able|unavailable:|No module named|not present|"
    r"no .* entrypoint found",
    re.IGNORECASE,
)
# Environment-dependent skip reasons we EXPECT on some machines
ENV_SKIP_RE = re.compile(
    r"firejail|LIVE_ENGINE_TESTS|not installed|requires? (network|docker|gh)|"
    r"platform|windows|linux only",
    re.IGNORECASE,
)


def _run_pytest(target: str, cwd: Path, timeout: int) -> tuple[str, int]:
    """Run pytest on target; return (combined output, returncode)."""
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
    """Extract verdict counts, failures, skips, collection errors."""
    passed = failed = 0
    verdict_seen = False
    for m in VERDICT_RE.finditer(output):
        verdict_seen = True
        if m.group(1):
            passed = int(m.group(1))
        if m.group(2):
            failed = int(m.group(2))
    failures = []  # (kind, nodeid)
    for m in FAILED_RE.finditer(output):
        failures.append((m.group(1), m.group(2)))
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


def _classify_solo(nodeid: str) -> str:
    """Re-run one failing test alone: 'pollution' if it passes solo."""
    try:
        out, rc = _run_pytest(nodeid, ROOT, SOLO_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "hang (timed out solo)"
    if rc == 0:
        return "POLLUTION - passes solo, fails in suite (isolation bug)"
    if rc == 5 or "no tests ran" in out:
        return "vanished solo (parametrized/collection quirk)"
    return "real - fails solo too"


def _baseline_failures() -> set | None:
    """Run the suite in a throwaway worktree at HEAD; return its failed nodeids.
    None means the baseline could not run (not a repo, worktree failed)."""
    tmp = Path(tempfile.mkdtemp(prefix="audit_suite_head_"))
    wt = tmp / "head_wt"
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=120,
        )
        if add.returncode != 0:
            print(f"  [baseline] worktree failed: {add.stderr.strip()[:200]}")
            return None
        out, rc = _run_pytest(TESTS_DIR, wt, FULL_SUITE_TIMEOUT)
        return {nid for _, nid in _parse(out, rc)["failures"]}
    except subprocess.TimeoutExpired:
        print("  [baseline] HEAD suite timed out")
        return None
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=60,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def _section(code: str, sev: str, title: str, n: int):
    print("=" * 74)
    print(f"{code} [{sev}] {title} - {n} finding(s)")
    print("=" * 74)


def _s3_s5_report(out: str, r: dict, med: int, info: int) -> tuple[int, int]:
    """S3 collection errors + S4 import-drift skips + S5 skip inventory."""
    # S3: collection errors
    collect_errs = sorted(
        {
            m.group(1) or m.group(2)
            for m in COLLECT_ERR_RE.finditer(out)
            if "::" not in m.group(0)
        }
    )
    _section(
        "S3", "MEDIUM", "collection errors (file could not import)", len(collect_errs)
    )
    med += len(collect_errs)
    for f in collect_errs:
        print(f"  {f}")
    print()
    # S4: import-drift skips
    drift = {
        reason: n
        for reason, n in r["skips"].items()
        if IMPORT_DRIFT_RE.search(reason) and not ENV_SKIP_RE.search(reason)
    }
    _section(
        "S4",
        "MEDIUM",
        "import-drift skips - tests silently guarding NOTHING",
        len(drift),
    )
    med += len(drift)
    for reason, n in sorted(drift.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4} x  {reason[:100]}")
    if drift:
        print(
            "  (fix the test file's import path - e.g. _imp('checkpoint') "
            "-> _imp('memory.checkpoint') - do not delete the tests)"
        )
    print()
    # S5: skip inventory
    env_skips = {rn: n for rn, n in r["skips"].items() if rn not in drift}
    _section("S5", "INFO", "skip inventory (env-dependent is expected)", len(env_skips))
    info += len(env_skips)
    for reason, n in sorted(env_skips.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {n:4} x  {reason[:100]}")
    if len(env_skips) > 15:
        print(f"  ... and {len(env_skips) - 15} more reasons")
    print()
    return med, info


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # audit: ok
        pass
    strict = "--strict" in sys.argv
    fast = "--fast" in sys.argv
    baseline = "--baseline" in sys.argv

    print(
        f"running: pytest {TESTS_DIR} {' '.join(PYTEST_ARGS)}  "
        f"(timeout {FULL_SUITE_TIMEOUT}s)"
    )
    try:
        out, rc = _run_pytest(TESTS_DIR, ROOT, FULL_SUITE_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("=" * 74)
        print("S2 [HIGH] suite verdict missing - 1 finding(s)")
        print("=" * 74)
        print(
            f"  the full suite exceeded {FULL_SUITE_TIMEOUT}s - a hung test "
            f"or deadlock; bisect with -x --timeout=60"
        )
        print(f"\n{'=' * 74}\nSUMMARY  HIGH: 1   MEDIUM: 0   INFO: 0   suppressed: 0")
        sys.exit(1 if strict else 0)

    r = _parse(out, rc)
    high = med = info = 0

    # ── S1: failures, classified ──
    _section(
        "S1",
        "HIGH",
        "failed/errored tests (solo re-run classified)",
        len(r["failures"]),
    )
    head_fails = None
    if baseline and r["failures"]:
        print("  [baseline] running suite at HEAD in a throwaway worktree...")
        head_fails = _baseline_failures()
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
                cls = f"  [{_classify_solo(nodeid)}]"
            else:
                cls = "  [unclassified - past solo re-run cap]"
        print(f"  {kind:6} {nodeid}{cls}{tag}")
    if head_fails is not None:
        fixed = head_fails - {nid for _, nid in r["failures"]}
        if fixed:
            print(f"  [baseline] fixed vs HEAD ({len(fixed)}):")
            for nid in sorted(fixed):
                print(f"    {nid}")
    print()

    # ── S2: verdict integrity ──
    findings = []
    if not r["verdict_seen"]:
        findings.append(
            "no 'N passed/failed' verdict line in output - a "
            "terminal-summary plugin crashed mid-report (grep the "
            "raw run for MarkupError/Traceback) - counts above are "
            "from partial output"
        )
    if rc >= 2:
        findings.append(
            f"pytest exit code {rc} (2=interrupted, 3=internal "
            f"error, 4=usage, 5=no tests) - the run did not "
            f"complete normally"
        )
    _section("S2", "HIGH", "suite verdict missing / abnormal exit", len(findings))
    high += len(findings)
    for f in findings:
        print(f"  {f}")
    print()

    med, info = _s3_s5_report(out, r, med, info)

    print("=" * 74)
    print(
        f"suite: {r['passed']} passed, {len(r['failures'])} failed, "
        f"{sum(r['skips'].values())} skipped (exit {rc})"
    )
    print(f"\n{'=' * 74}")
    print(f"SUMMARY  HIGH: {high}   MEDIUM: {med}   INFO: {info}   suppressed: 0")
    if strict and high:
        sys.exit(1)


if __name__ == "__main__":
    main()
