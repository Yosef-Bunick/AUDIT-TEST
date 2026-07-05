#!/usr/bin/env python3
"""
run_all_audits.py - run the full audit suite, one clean report.

  python run_all_audits.py            # summary table
  python run_all_audits.py --full     # table + every audit's full output
  python run_all_audits.py --strict   # exit 1 if any audit has HIGH findings

The suite (each script documents its own checks):
  audit/audit_wiring.py   - is it connected? (dead/test-only symbols, config flow,
                      shadowed + transitively dead config, stdout protocol)
  audit/audit_phd.py      - does it meet the quality bar? (exceptions, security,
                      state, imports, performance, complexity, docs)
  audit/audit_runtime.py  - will it hang/crash/run with the wrong brain? (loops,
                      timeouts, paths, encoding, log hygiene, tool & prompt
                      parity, dependencies)
  audit/audit_suite.py    - is the TEST SUITE healthy? RUNS pytest and triages:
                      failures classified real-vs-pollution by solo re-run,
                      eaten verdict lines, collection errors, import-drift
                      skips. (--baseline for HEAD regression diff.)
  audit/audit_quality.py  - external quality gates + EXECUTION-verified coverage:
                      black/ruff(I,S)/mypy/CVE scan, per-def "did the body
                      actually RUN under tests" (coverage.py), docstring %,
                      test hygiene (sleep, reasonless skip). Generic - point
                      at any repo via --path/--tests; tools degrade to skip.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
AUDITS = [
    os.path.join("src", "audit_code", "audit_wiring.py"),
    os.path.join("src", "audit_code", "audit_phd.py"),
    os.path.join("src", "audit_code", "audit_runtime.py"),
    os.path.join("src", "audit_code", "audit_suite.py"),
    os.path.join("src", "audit_code", "audit_quality.py"),
]

# summary-line extractors per audit output style
WIRING_RE = re.compile(r"HIGH-confidence findings:\s*(\d+)")
SUMMARY_RE = re.compile(r"SUMMARY\s+HIGH:\s*(\d+)\s+MEDIUM:\s*(\d+)\s+INFO:\s*(\d+)")


def run_one(script: str):
    # audit_suite RUNS the pytest suite (~2 min + solo re-runs of any failures);
    # audit_quality runs it AGAIN under coverage plus mypy — both need far more
    # headroom than the static audits.
    timeout = 1800 if ("suite" in script or "quality" in script) else 300
    proc = subprocess.run(
        [sys.executable, str(ROOT / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        # force the child interpreter to UTF-8 stdio on every OS (Windows pipes
        # default to cp1252, which crashes on the audit's Unicode glyphs)
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    out = proc.stdout or ""
    high = med = info = None
    m = SUMMARY_RE.search(out)
    if m:
        high, med, info = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = WIRING_RE.search(out)
        if m:
            high = int(m.group(1))
    # FAIL-CLOSED: an audit that never printed its SUMMARY line did not finish,
    # no matter what it exited with. (An audit that printed a greeting and then
    # crashed with rc=1 used to be reported as PASS clean.)
    crashed = proc.returncode not in (0, 1) or high is None
    return out, proc.stderr or "", high, med, info, crashed


def main():
    full = "--full" in sys.argv
    strict = "--strict" in sys.argv

    rows = []
    outputs = {}
    for script in AUDITS:
        name = Path(script).stem.replace("audit_", "")
        try:
            out, err, high, med, info, crashed = run_one(script)
        except (subprocess.TimeoutExpired, OSError) as e:
            rows.append((name, "CRASH", f"{type(e).__name__}: {e}"))
            continue
        outputs[name] = out
        if crashed:
            rows.append(
                (name, "CRASH", (err.strip().splitlines() or ["no output"])[-1])
            )
        elif high:
            detail = f"{high} HIGH" + (f", {med} MEDIUM" if med else "")
            rows.append((name, "FAIL", detail))
        elif med or info:
            rows.append((name, "WARN", f"0 HIGH, {med or 0} MEDIUM, {info or 0} INFO"))
        else:
            rows.append((name, "PASS", "clean"))

    print("=" * 60)
    print("AUDIT RESULTS")
    print("=" * 60)
    for name, status, detail in rows:
        print(f"  [{status:5}] {name:10} {detail}")
    print("=" * 60)
    print("detail: python <audit>.py   |   full dump: --full")

    if full:
        for name, out in outputs.items():
            print(f"\n{'#' * 74}\n# {name}\n{'#' * 74}")
            print(out)

    if strict and any(status in ("FAIL", "CRASH") for _, status, _ in rows):
        sys.exit(1)


if __name__ == "__main__":
    main()
