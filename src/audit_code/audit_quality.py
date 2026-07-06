#!/usr/bin/env python3
"""
audit_quality.py - external-tool quality gates + EXECUTION-verified coverage.

The sibling audits are deliberately stdlib-only AST analysis. This one closes
the complementary gaps by driving the standard quality toolchain (when
installed - every check degrades to an INFO skip when its tool is absent, so
the file is portable to ANY repo) and by measuring what no name-level check
can: whether each def's body actually EXECUTES under the test suite.

Division of labor vs the siblings (do not duplicate):
  audit_wiring   owns dead code, test-only symbols, config drift
  audit_phd      owns exception discipline, security AST patterns, test
                 SMELLS (T2 name-level per-def coverage, T3 happy-path,
                 T4 assertion-free, T5 patch drift), god functions
  audit_runtime  owns loops/timeouts/log hygiene/prompt contracts/deps list
  audit_suite    owns pytest execution triage (failures, pollution, skips)
  THIS FILE      owns external gates + executed-line truth:

  Q1 [MEDIUM] black --check         formatting drift (count of files)
  Q2 [MEDIUM] ruff check            lint; "I" rules = import order (isort),
     [HIGH]                         "S" rules = security (bandit subset) are
                                    split out and reported HIGH
  Q3 [MEDIUM] mypy                  type errors (non-strict + ignore-missing-
                                    imports by default; --strict to escalate)
  Q4 [HIGH]   CVE scan              safety (or pip-audit) on installed deps
  Q5 [MEDIUM] per-def EXECUTION coverage - runs the suite under coverage.py,
              maps executed lines onto every def via AST: a def whose body
              never ran is invisible to every assertion in the repo, no
              matter how many tests MENTION its name (phd T2's known limit).
  Q6 [MEDIUM] docstring coverage    module/class/public-def docstrings
              (interrogate-equivalent, stdlib AST, threshold %)
  Q7 [MEDIUM] test hygiene          time.sleep() in tests (flaky/slow),
              @pytest.mark.skip with no reason (rot with no audit trail)
  Q8 [INFO]   mutation testing      wired IF mutmut installed AND --mutation
              passed (slow); otherwise reports how to enable. The only true
              "do the tests DETECT bugs" measure - everything else is proxy.

Usage:
  python audit/audit_quality.py                # all checks, this repo
  python audit/audit_quality.py --fast         # skip the coverage run (Q5)
  python audit/audit_quality.py --strict-mypy  # mypy --strict
  python audit/audit_quality.py --path <root> --tests <dir>   # any repo
  python audit/audit_quality.py --strict       # exit 1 on any HIGH

Output format matches the siblings (SUMMARY HIGH/MEDIUM/INFO line) so
run_all_audits.py parses it unchanged.
"""

import argparse
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from audit_code.audit_config import DOC_THRESHOLD_PCT, MIN_FLAG_BODY_LINES, TOOL_TIMEOUT
from audit_code.audit_shared import EXCLUDE_DIRS, force_utf8_streams

ROOT = Path(__file__).resolve().parent.parent.parent


def _tool(name: str) -> str | None:
    """Resolve a tool runnable as `python -m name` or on PATH; None if absent.
    find_spec, NOT __import__ — importing some tools (safety) runs side effects
    that can fail and make an installed tool look absent. And a spec whose
    location is INSIDE the audited repo is the repo shadowing the pip name
    (this repo's safety/ package is sandbox code, not the CVE scanner)."""
    try:
        spec = importlib.util.find_spec(name.replace("-", "_"))
        if spec is not None:
            locs = list(getattr(spec, "submodule_search_locations", None) or [])
            origin = spec.origin or (locs[0] if locs else "")
            if origin and not Path(origin).resolve().is_relative_to(ROOT):
                return f"{sys.executable} -m {name.replace('-', '_')}"
    except (ImportError, ValueError):
        pass
    return shutil.which(name)


def _run(
    cmd: list, cwd: Path, timeout: int = TOOL_TIMEOUT, env=None
) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return proc.returncode, (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"[timed out after {timeout}s]"
    except OSError as e:
        return -2, f"[failed to launch: {e}]"


def _py_files(root: Path, tests_dir: Path) -> tuple[list[Path], list[Path]]:
    prod: list[Path] = []
    tests: list[Path] = []
    for p in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        (tests if tests_dir in p.parents or p.parent == tests_dir else prod).append(p)
    return prod, tests


def _section(code: str, sev: str, title: str, n: int | None = None):
    print("=" * 74)
    suffix = f" - {n} finding(s)" if n is not None and n >= 0 else ""
    print(f"{code} [{sev}] {title}{suffix}")
    print("=" * 74)


# ── Q0: syntax (fail-closed foundation for every AST check) ─────────────────


def q_syntax(root: Path, tests_dir: Path, counts: dict):
    """Every AST-based check in this suite SKIPS files that fail to parse —
    without this gate, a syntax error makes a file INVISIBLE to static
    analysis instead of failing loudly. Stdlib-only; no ruff required."""
    prod, tests = _py_files(root, tests_dir)
    bad = []
    for p in prod + tests:
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            bad.append(f"{p.relative_to(root)}:{e.lineno}  {e.msg}")
    _section(
        "Q0", "HIGH", "files that do not PARSE (invisible to all AST checks)", len(bad)
    )
    counts["HIGH"] += len(bad)
    for b in bad[:15]:
        print(f"  {b}")
    if not bad:
        print("  every .py file parses")
    print()


# ── Q1: black ────────────────────────────────────────────────────────────────


def q_black(root: Path, counts: dict):
    tool = _tool("black")
    _section("Q1", "MEDIUM", "black formatting drift")
    if not tool:
        print("  SKIP: black not installed (pip install black)\n")
        return
    excl = "|".join(re.escape(d) for d in sorted(EXCLUDE_DIRS))
    rc, out = _run(tool.split() + ["--check", ".", "--extend-exclude", excl], root)
    files = re.findall(r"^would reformat (.+)$", out, re.MULTILINE)
    if rc == 0:
        print("  formatted cleanly\n")
        return
    if not files and rc != 1:
        print(f"  SKIP: black errored: {out.strip()[:200]}\n")
        return
    counts["MEDIUM"] += len(files)
    print(f"  {len(files)} file(s) would be reformatted (first 15):")
    for f in files[:15]:
        print(f"    {f}")
    print()


# ── Q2: ruff (lint + import-order I + security S) ────────────────────────────


def q_ruff(root: Path, counts: dict):
    tool = _tool("ruff")
    _section("Q2", "MEDIUM/HIGH", "ruff lint (I=import-order, S=security->HIGH)")
    if not tool:
        print("  SKIP: ruff not installed (pip install ruff)\n")
        return
    # S101 (assert) excluded: asserts are the POINT of tests, and prod-side
    # assert discipline is audit_phd territory. Keeps S* signal meaningful.
    rc, out = _run(
        tool.split()
        + [
            "check",
            ".",
            "--select",
            "E,F,W,I,B,S",
            "--ignore",
            "S101",
            "--output-format",
            "json",
            "--exit-zero",
        ],
        root,
    )
    try:
        findings = json.loads(out[out.index("[") : out.rindex("]") + 1])
    except (ValueError, json.JSONDecodeError):
        print(f"  SKIP: could not parse ruff output: {out.strip()[:200]}\n")
        return
    sec = [f for f in findings if str(f.get("code", "")).startswith("S")]
    lint = [f for f in findings if not str(f.get("code", "")).startswith("S")]
    counts["HIGH"] += len(sec)
    counts["MEDIUM"] += len(lint)
    print(f"  security (S*): {len(sec)}   lint/style: {len(lint)}")
    by_code: dict = {}
    for f in findings:
        by_code.setdefault(f.get("code"), []).append(f)
    for code, items in sorted(by_code.items(), key=lambda kv: -len(kv[1]))[:12]:
        loc = items[0]
        where = f"{Path(loc['filename']).name}:{loc['location']['row']}"
        print(
            f"    {len(items):4} x {code:8} {items[0].get('message','')[:60]}  e.g. {where}"
        )
    print()


# ── Q3: mypy ─────────────────────────────────────────────────────────────────


def q_mypy(root: Path, counts: dict, strict: bool):
    tool = _tool("mypy")
    _section("Q3", "MEDIUM", f"mypy type errors ({'strict' if strict else 'default'})")
    if not tool:
        print("  SKIP: mypy not installed (pip install mypy)\n")
        return
    args = tool.split() + [
        ".",
        "--ignore-missing-imports",
        "--no-error-summary",
        "--follow-imports=silent",
        "--exclude",
        "|".join(sorted(EXCLUDE_DIRS)),
    ]
    if strict:
        args.append("--strict")
    rc, out = _run(args, root)
    errs = [line for line in out.splitlines() if ": error:" in line]
    if rc in (-1, -2):
        print(f"  SKIP: {out.strip()[:200]}\n")
        return
    counts["MEDIUM"] += len(errs)
    if not errs:
        print("  clean\n")
        return
    by_file: dict = {}
    for line in errs[:10]:
        by_file.setdefault(line.split(":", 1)[0], []).append(line)
    print(f"  {len(errs)} error(s) across {len(by_file)} file(s) (top files):")
    for f, items in sorted(by_file.items(), key=lambda kv: -len(kv[1]))[:10]:
        print(f"    {len(items):4} x {f}")
        print(f"           e.g. {items[0].split(':', 1)[1][:90]}")
    print()


# ── Q4: dependency CVEs ──────────────────────────────────────────────────────


def q_cves(root: Path, counts: dict):
    _section("Q4", "HIGH", "known CVEs in installed dependencies")
    for name, args in (
        ("pip-audit", ["--progress-spinner", "off"]),
        ("safety", ["check", "--output", "text"]),
    ):
        tool = _tool(name)
        if not tool:
            continue
        rc, out = _run(tool.split() + args, root, timeout=300)
        if rc in (-1, -2) or "error" in out.lower()[:200]:
            print(f"  {name}: could not run ({out.strip()[:120]})")
            continue
        vulns = len(
            re.findall(r"(?i)\bvulnerabilit(?:y|ies) found|-> vuln|CVE-\d{4}", out)
        )
        if rc == 0 and not vulns:
            print(f"  {name}: no known vulnerabilities\n")
            return
        counts["HIGH"] += max(vulns, 1)
        print(
            f"  {name}: {max(vulns,1)} vulnerability signal(s) — run `{name}` for detail"
        )
        for line in [
            line for line in out.splitlines() if re.search(r"(?i)CVE-|vulnerab", line)
        ][:8]:
            print(f"    {line.strip()[:100]}")
        print()
        return
    print("  SKIP: neither pip-audit nor safety installed\n")


# ── Q5: per-def EXECUTION coverage ───────────────────────────────────────────


def _def_spans(path: Path) -> list[tuple[str, int, int, int]]:
    """(qualname, def_line, body_start, body_end) for every function."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    out = []

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                body_start = child.body[0].lineno
                body_end = max(getattr(n, "end_lineno", body_start) for n in child.body)
                out.append((name, child.lineno, body_start, body_end))
                walk(child, prefix=f"{name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix=f"{prefix}{child.name}.")
            else:
                walk(child, prefix=prefix)

    walk(tree)
    return out


def q_def_coverage(
    root: Path, tests_dir: Path, counts: dict, pytest_extra: list
):  # audit: ok (Q5 orchestrator — tested via runner integration)
    _section("Q5", "MEDIUM", "defs whose body NEVER EXECUTES under the suite")
    if importlib.util.find_spec("coverage") is None:
        print("  SKIP: coverage not installed (pip install coverage)\n")
        return
    tmp = Path(tempfile.mkdtemp(prefix="audit_q5_"))
    data_file = tmp / ".coverage"
    json_file = tmp / "cov.json"
    env = dict(os.environ, COVERAGE_FILE=str(data_file))
    print("  running suite under coverage (this is a full test run)...")
    rc, out = _run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--source={root}",
            "-m",
            "pytest",
            str(tests_dir),
            "-q",
            "--tb=no",
            *pytest_extra,
        ],
        root,
        timeout=1800,
        env=env,
    )
    if not data_file.exists():
        print(f"  SKIP: coverage produced no data: {out.strip()[-200:]}\n")
        return
    rc, out = _run(
        [
            sys.executable,
            "-m",
            "coverage",
            "json",
            "-o",
            str(json_file),
            "--data-file",
            str(data_file),
        ],
        root,
        env=env,
    )
    try:
        cov = json.loads(json_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  SKIP: could not read coverage json: {e}\n")
        return
    executed = {}
    for fname, fdata in cov.get("files", {}).items():
        executed[Path(root / fname).resolve()] = set(fdata.get("executed_lines", []))

    prod, _tests = _py_files(root, tests_dir)
    total = never = 0
    flagged = []
    for p in prod:
        lines = executed.get(p.resolve())
        for qual, defline, b0, b1 in _def_spans(p):
            total += 1
            ran = bool(lines) and any(
                ln in (lines or set()) for ln in range(b0, b1 + 1)
            )
            if not ran:
                never += 1
                if (b1 - b0 + 1) >= MIN_FLAG_BODY_LINES:
                    flagged.append((p.relative_to(root), defline, qual, b1 - b0 + 1))
    pct = 100.0 * (total - never) / total if total else 100.0
    counts["MEDIUM"] += len(flagged)
    print(
        f"  {total} defs scanned; {total - never} executed under tests "
        f"({pct:.1f}%); {never} never ran ({len(flagged)} flagged, "
        f">= {MIN_FLAG_BODY_LINES} body lines)"
    )
    print(
        "  NOTE: phd T2 checks a test MENTIONS the name; this check proves "
        "the body RAN. Both green = tested for real."
    )
    for rel, line, qual, size in sorted(flagged, key=lambda x: -x[3])[:25]:
        print(f"    {str(rel):44} :{line:<5} {qual}  ({size} lines)")
    if len(flagged) > 25:
        print(f"    ... and {len(flagged) - 25} more (largest first)")
    print()


# ── Q6: docstring coverage ───────────────────────────────────────────────────


def q_docstrings(root: Path, tests_dir: Path, counts: dict):
    _section("Q6", "MEDIUM", f"docstring coverage (< {DOC_THRESHOLD_PCT}% fails)")
    prod, _ = _py_files(root, tests_dir)
    have = need = 0
    worst: dict = {}
    for p in prod:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                if name.startswith("_") and name != "__init__":
                    continue
                need += 1
                if ast.get_docstring(node):
                    have += 1
                else:
                    worst.setdefault(p.relative_to(root), 0)
                    worst[p.relative_to(root)] += 1
    pct = 100.0 * have / need if need else 100.0
    ok = pct >= DOC_THRESHOLD_PCT
    if not ok:
        counts["MEDIUM"] += need - have
    print(
        f"  {have}/{need} public defs+classes documented ({pct:.1f}%) - "
        f"{'PASS' if ok else 'FAIL'}"
    )
    if not ok:
        for rel, n in sorted(worst.items(), key=lambda kv: -kv[1])[:10]:
            print(f"    {n:4} undocumented in {rel}")
    print()


# ── Q7: test hygiene (sleep, reasonless skip) ────────────────────────────────


def q_test_hygiene(root: Path, tests_dir: Path, counts: dict):
    findings = []
    for p in sorted(tests_dir.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr == "sleep"
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "time"
                ):
                    findings.append(
                        (
                            p,
                            node.lineno,
                            "time.sleep() in a test - flaky AND slow; poll or mock the clock",
                        )
                    )
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr == "skip"
                    and not node.args
                    and not node.keywords
                ):
                    findings.append(
                        (
                            p,
                            node.lineno,
                            "skip with NO reason - rots silently with no audit trail",
                        )
                    )
    _section("Q7", "MEDIUM", "test hygiene (sleep / reasonless skip)", len(findings))
    counts["MEDIUM"] += len(findings)
    for p, line, msg in findings[:20]:
        print(f"  {p.relative_to(root)}:{line}  {msg}")
    if len(findings) > 20:
        print(f"  ... and {len(findings) - 20} more")
    print()


# ── Q8: mutation testing (opt-in) ────────────────────────────────────────────


def q_mutation(root: Path, counts: dict, enabled: bool):
    _section("Q8", "INFO", "mutation testing (the only TRUE test-of-tests)")
    tool = _tool("mutmut")
    if not tool:
        print(
            "  SKIP: mutmut not installed. This is the one measure that proves\n"
            "  tests DETECT bugs (inject small mutations, count how many any\n"
            "  test kills) rather than merely execute lines.\n"
            "  Enable: pip install mutmut && python audit/audit_quality.py --mutation\n"
        )
        return
    if not enabled:
        print("  SKIP: pass --mutation to run (slow - minutes to hours)\n")
        return
    rc, out = _run(tool.split() + ["run", "--paths-to-mutate", "."], root, timeout=3600)
    killed = len(re.findall(r"killed", out, re.IGNORECASE))
    survived = len(re.findall(r"survived", out, re.IGNORECASE))
    print(f"  killed: {killed}   survived: {survived}")
    counts["INFO"] += survived
    print()


# ── main ─────────────────────────────────────────────────────────────────────


def main():  # audit: ok (CLI entry point)
    force_utf8_streams()
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(ROOT), help="repo root to audit")
    ap.add_argument("--tests", default="tests", help="tests dir relative to root")
    ap.add_argument("--fast", action="store_true", help="skip the Q5 coverage run")
    ap.add_argument("--strict-mypy", action="store_true")
    ap.add_argument("--mutation", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any HIGH")
    ap.add_argument(
        "--pytest-extra",
        default="-p no:logfire",
        help="extra args for the Q5 pytest run",
    )
    args = ap.parse_args()

    root = Path(args.path).resolve()
    tests_dir = (root / args.tests).resolve()
    counts = {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    prod, tests = _py_files(root, tests_dir)
    print(f"scanned root: {root} ({len(prod)} prod files, {len(tests)} test files)\n")

    q_syntax(root, tests_dir, counts)
    q_black(root, counts)
    q_ruff(root, counts)
    q_mypy(root, counts, args.strict_mypy)
    q_cves(root, counts)
    if args.fast:
        _section("Q5", "MEDIUM", "defs whose body NEVER EXECUTES under the suite", 0)
        print("  SKIP: --fast\n")
    else:
        q_def_coverage(root, tests_dir, counts, args.pytest_extra.split())
    q_docstrings(root, tests_dir, counts)
    q_test_hygiene(root, tests_dir, counts)
    q_mutation(root, counts, args.mutation)

    print("=" * 74)
    print(
        f"SUMMARY  HIGH: {counts['HIGH']}   MEDIUM: {counts['MEDIUM']}   "
        f"INFO: {counts['INFO']}   suppressed: 0"
    )
    if args.strict and counts["HIGH"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
