#!/usr/bin/env python3
"""
audit_gate.py - the per-CHANGE verdict: "is the code I just wrote proven?"

The sibling audits measure the whole repo; a repo with years of accepted
backlog can never answer "did MY change break something?". This gate judges
ONLY the working-tree diff vs HEAD, in an isolated shadow worktree, and
passes only when four independent lines of evidence agree:

  G1  STATIC REGRESSION   the AST audits (wiring/phd/runtime) run at HEAD
                          and again with your changes - NEW HIGH findings
                          fail the gate; pre-existing backlog is ignored.
  G2  SUITE GREEN         the full pytest suite passes with your changes.
  G3  EXECUTION PROOF     every changed/added def (>= 2 body lines) actually
                          EXECUTES under the suite (coverage.py) - a green
                          suite that never runs your code proves nothing.
  G4  MUTATION KILL       small bugs are injected into YOUR CHANGED LINES
                          (flipped comparisons, and<->or, +/-, off-by-one,
                          True<->False) and the tests must FAIL for most of
                          them. Execution without detection is theater; this
                          is the check that the tests would CATCH a bug.

Honest limits (why "99%", not 100%): the gate cannot see environment and
integration faults - the sandbox-env, native-hang and migrated-DB bugs of
2026-07-03 were all invisible to every static+mocked check and found only by
a real run. Rice's theorem guarantees no static gate can promise semantic
correctness. G1-G4 green + a real e2e run is the full ladder.

Usage:
  python audit/audit_gate.py                # full gate on working-tree diff
  python audit/audit_gate.py --fast         # skip G4 (mutation)
  python audit/audit_gate.py --no-static    # skip G1 (static baseline diff)
  python audit/audit_gate.py --kill 70      # required mutant kill %, default 60

Exit code: 0 = PASS, 1 = FAIL (any gate), 2 = nothing to judge / setup error.
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_config import (
    MAX_MUTANTS,
    MIN_BODY_LINES,
    MUTANT_TEST_TIMEOUT,
    SUITE_TIMEOUT,
)
from audit_quality import _def_spans  # noqa: E402
from audit_shared import EXCLUDE_DIRS

ROOT = Path(__file__).resolve().parent.parent
# Allow --path override for audit-code wrapper
for _i, _a in enumerate(sys.argv):
    if _a == "--path" and _i + 1 < len(sys.argv):
        ROOT = Path(sys.argv[_i + 1]).resolve()
        break
TESTS_DIR = "tests"
PYTEST_BASE = ["-q", "--tb=no", "-p", "no:logfire"]
STATIC_AUDITS = [
    "audit/audit_wiring.py",
    "audit/audit_phd.py",
    "audit/audit_runtime.py",
]

HIGH_RE = re.compile(r"HIGH-confidence findings:\s*(\d+)|SUMMARY\s+HIGH:\s*(\d+)")


def _run(cmd, cwd, timeout=300, env=None):
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"[timed out after {timeout}s]"


def _changed_files() -> tuple[dict, list]:
    """{repo-relative path -> set(changed new-side line numbers) | None=all},
    plus deleted paths. Untracked files map to None (every line is new)."""
    rc, out = _run(["git", "status", "--porcelain"], ROOT)
    changed, deleted = {}, []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        st, path = line[:2], line[3:].strip().strip('"').rstrip("/")
        p = Path(path)
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if "D" in st:
            deleted.append(path)
        elif st.strip().startswith("??"):
            # porcelain lists an untracked DIRECTORY as one entry - expand it
            src = ROOT / path
            if src.is_dir():
                for f in src.rglob("*"):
                    if f.is_file() and not any(
                        part in EXCLUDE_DIRS for part in f.parts
                    ):
                        changed[str(f.relative_to(ROOT)).replace("\\", "/")] = None
            else:
                changed[path] = None
        else:
            changed[path] = set()
    # hunk line ranges for tracked modifications
    for path in [p for p, v in changed.items() if v == set()]:
        rc, out = _run(["git", "diff", "-U0", "HEAD", "--", path], ROOT)
        lines = set()
        for m in re.finditer(
            r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", out, re.MULTILINE
        ):
            start, n = int(m.group(1)), int(m.group(2) or 1)
            lines.update(range(start, start + max(n, 1)))
        changed[path] = lines
    return changed, deleted


def _make_shadow(changed: dict, deleted: list) -> Path:
    """HEAD worktree + working-tree versions of every changed file overlaid."""
    tmp = Path(tempfile.mkdtemp(prefix="audit_gate_"))
    shadow = tmp / "shadow"
    rc, out = _run(
        ["git", "worktree", "add", "--detach", str(shadow), "HEAD"], ROOT, timeout=120
    )
    if rc != 0:
        raise RuntimeError(f"worktree failed: {out.strip()[:200]}")
    for rel in changed:
        src, dst = ROOT / rel, shadow / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for rel in deleted:
        (shadow / rel).unlink(missing_ok=True)
    return shadow


def _drop_shadow(shadow: Path):
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(shadow)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    shutil.rmtree(shadow.parent, ignore_errors=True)


def _static_high(cwd: Path) -> dict:
    """{audit: (high_count, completed)}. FAIL-CLOSED: an audit that crashed or
    never printed a summary is (0, False) — the gate must treat that as a
    failure, never as zero findings."""
    counts = {}
    for script in STATIC_AUDITS:
        rc, out = _run([sys.executable, str(cwd / script)], cwd, timeout=300)
        matches = list(HIGH_RE.finditer(out))
        total = sum(int(m.group(1) or m.group(2) or 0) for m in matches)
        completed = rc in (0, 1) and bool(matches)
        counts[Path(script).stem] = (total, completed)
    return counts


def _changed_defs(shadow: Path, changed: dict) -> list:
    """(rel, qualname, defline, body_start, body_end) for defs touching the diff."""
    out = []
    for rel, lines in changed.items():
        if not rel.endswith(".py") or rel.startswith(TESTS_DIR):
            continue
        f = shadow / rel
        if not f.exists():
            continue
        for qual, defline, b0, b1 in _def_spans(f):
            if b1 - b0 + 1 < MIN_BODY_LINES:
                continue
            if lines is None or any(ln in lines for ln in range(defline, b1 + 1)):
                out.append((rel, qual, defline, b0, b1))
    return out


# ── G4: micro-mutation ───────────────────────────────────────────────────────

_CMP_SWAP = {
    ast.Lt: ast.GtE,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
    ast.GtE: ast.Lt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}


def _mutation_sites(tree: ast.AST, lines) -> list:
    """Nodes on changed lines where a classic bug can be injected."""
    sites = []
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", None)
        if ln is None or (lines is not None and ln not in lines):
            continue
        if isinstance(node, ast.Compare) and type(node.ops[0]) in _CMP_SWAP:
            sites.append(("cmp", node))
        elif isinstance(node, ast.BoolOp):
            sites.append(("bool", node))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            sites.append(("arith", node))
        elif (isinstance(node, ast.Constant) and node.value is True) or (
            isinstance(node, ast.Constant) and node.value is False
        ):
            sites.append(("flip", node))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
            and 0 < abs(node.value) < 1000
        ):
            sites.append(("offby1", node))
    return sites


def _apply_mutation(kind, node):
    if kind == "cmp":
        node.ops[0] = _CMP_SWAP[type(node.ops[0])]()
    elif kind == "bool":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == "arith":
        node.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
    elif kind == "flip":
        node.value = not node.value
    elif kind == "offby1":
        node.value = node.value + 1


def _tests_referencing(shadow: Path, module_stem: str) -> list:
    hits = []
    for tf in (shadow / TESTS_DIR).rglob("test_*.py"):
        try:
            if re.search(
                rf"\b{re.escape(module_stem)}\b",
                tf.read_text(encoding="utf-8", errors="replace"),
            ):
                hits.append(str(tf.relative_to(shadow)))
        except OSError:
            pass
    return hits


def g4_mutation(shadow: Path, changed: dict, defs: list, kill_pct: int):
    per_file = {}
    for rel, qual, defline, b0, b1 in defs:
        per_file.setdefault(rel, set()).update(range(b0, b1 + 1))
    mutants = []
    for rel, lines in per_file.items():
        src = (shadow / rel).read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        diff_lines = changed.get(rel)
        scope = lines if diff_lines is None else (lines & diff_lines or lines)
        for kind, node in _mutation_sites(tree, scope):
            mutants.append((rel, kind, node.lineno, getattr(node, "col_offset", 0)))
    if not mutants:
        # Legitimately neutral: the changed lines hold no mutable logic
        # (docstrings, log strings, config values) — nothing to prove.
        return None, "no mutation sites on changed lines"
    # spread the cap across files
    mutants = mutants[:MAX_MUTANTS]
    killed = survived = unassessed = infra = 0
    survivors = []
    for rel, kind, lineno, col in mutants:
        f = shadow / rel
        original = f.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(original)
        target = None
        for k, node in _mutation_sites(tree, None):
            if (k, node.lineno, getattr(node, "col_offset", 0)) == (kind, lineno, col):
                target = (k, node)
                break
        if target is None:
            unassessed += 1
            continue
        _apply_mutation(*target)
        tests = _tests_referencing(shadow, Path(rel).stem)
        if not tests:
            unassessed += 1
            survivors.append(f"{rel}:{lineno} [{kind}] NO TEST references module")
            continue
        try:
            f.write_text(ast.unparse(tree), encoding="utf-8")
            # No pytest-timeout flag here — the plugin may be absent on a
            # foreign repo and its usage error (rc=4) would masquerade as a
            # kill. The subprocess deadline bounds a hanging mutant instead.
            rc, _out = _run(
                [sys.executable, "-m", "pytest", *tests, "-x", *PYTEST_BASE],
                shadow,
                timeout=MUTANT_TEST_TIMEOUT,
            )
        finally:
            f.write_text(original, encoding="utf-8")
        # rc taxonomy — only GENUINE test failures count as kills. pytest:
        # 0=pass, 1=test failures, 2=interrupted, 3=internal error, 4=usage
        # error, 5=no tests collected; -1=our subprocess deadline (hung mutant
        # = detected). Anything else is INFRASTRUCTURE, not evidence.
        if rc == 0:
            survived += 1
            survivors.append(
                f"{rel}:{lineno} [{kind}] mutant SURVIVED "
                f"({len(tests)} test file(s) ran, none failed)"
            )
        elif rc in (1, -1) or rc == 2:
            killed += 1
        else:
            infra += 1
            survivors.append(
                f"{rel}:{lineno} [{kind}] INFRA ERROR "
                f"(pytest exit {rc}) — not evidence either way"
            )
    assessed = killed + survived
    rate = 100.0 * killed / assessed if assessed else 0.0
    detail = (
        f"{killed}/{assessed} mutants killed ({rate:.0f}%), "
        f"{unassessed} unassessed, {infra} infra error(s)"
    )
    # FAIL-CLOSED: mutants existed but none could be assessed, or the test
    # harness itself errored — that is absence of proof, not proof.
    if infra:
        return (False, detail + " — infrastructure errors void the gate", survivors)
    if assessed == 0:
        return (
            False,
            detail + " — mutants existed but NONE were assessed "
            "(no referencing tests?)",
            survivors,
        )
    return (rate >= kill_pct, detail, survivors)


def _run_gates(shadow: Path, changed: dict, args) -> dict:
    """G1 static regression + G2 suite + G3 execution proof + G4 mutation."""
    verdicts = {}
    try:
        # G1: static regression vs HEAD
        if args.no_static:
            verdicts["G1 static-regression"] = (True, "skipped (--no-static)")
        else:
            print("G1: static audits at HEAD vs with-changes ...")
            head_shadow = shadow.parent / "head"
            _run(
                ["git", "worktree", "add", "--detach", str(head_shadow), "HEAD"],
                ROOT,
                timeout=120,
            )
            base = _static_high(head_shadow)
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(head_shadow)],
                cwd=str(ROOT),
                capture_output=True,
                timeout=60,
            )
            now = _static_high(shadow)
            crashed = [k for k, (_, done) in now.items() if not done]
            regressions = {
                k: (base.get(k, (0, True))[0], v)
                for k, (v, done) in now.items()
                if done and v > base.get(k, (0, True))[0]
            }
            if crashed:
                verdicts["G1 static-regression"] = (
                    False,
                    f"audit(s) CRASHED with changes applied: "
                    f"{', '.join(crashed)} — fail-closed",
                )
            else:
                verdicts["G1 static-regression"] = (
                    not regressions,
                    (
                        "no new HIGH findings"
                        if not regressions
                        else ", ".join(
                            f"{k}: {b}->{n} HIGH" for k, (b, n) in regressions.items()
                        )
                    ),
                )
        # G2 + G3: suite under coverage
        print("G2+G3: full suite under BRANCH coverage in shadow worktree ...")
        env = dict(os.environ, COVERAGE_FILE=str(shadow / ".gate_cov"))
        rc, out = _run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--branch",
                f"--source={shadow}",
                "-m",
                "pytest",
                TESTS_DIR,
                *PYTEST_BASE,
            ],
            shadow,
            timeout=SUITE_TIMEOUT,
            env=env,
        )
        m = re.search(r"(\d+) failed", out)
        failed = int(m.group(1)) if m else (0 if rc == 0 else -1)
        verdicts["G2 suite-green"] = (
            rc == 0,
            (
                "suite passed"
                if rc == 0
                else f"{failed if failed >= 0 else '?'} test(s) failed - run pytest for detail"
            ),
        )
        defs = _changed_defs(shadow, changed)
        if not defs:
            verdicts["G3 execution-proof"] = (True, "no changed prod defs to prove")
        else:
            _run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "json",
                    "-o",
                    str(shadow / ".gate_cov.json"),
                    "--data-file",
                    str(shadow / ".gate_cov"),
                ],
                shadow,
                env=env,
            )
            try:
                cov = json.loads((shadow / ".gate_cov.json").read_text())
                executed, missing = {}, {}
                for fpath, d in cov.get("files", {}).items():
                    key = str(Path(fpath))
                    executed[key] = set(d.get("executed_lines", []))
                    missing[key] = set(d.get("missing_lines", []))
            except (OSError, json.JSONDecodeError):
                executed, missing = {}, {}
            missed = []
            for rel, qual, defline, b0, b1 in defs:
                lines = executed.get(rel) or executed.get(str(Path(rel))) or set()
                if not any(ln in lines for ln in range(b0, b1 + 1)):
                    missed.append(f"{rel}:{defline} {qual}")
            line_misses = []
            for rel, lines in changed.items():
                if (
                    lines is None
                    or not rel.endswith(".py")
                    or rel.startswith(TESTS_DIR)
                ):
                    continue
                miss = missing.get(rel) or missing.get(str(Path(rel))) or set()
                dead = sorted(lines & miss)
                if dead:
                    line_misses.append(
                        f"{rel}: {len(dead)} changed line(s) never run (e.g. {dead[:4]})"
                    )
            ok3 = not missed and not line_misses
            parts = []
            if missed:
                parts.append(
                    f"{len(missed)}/{len(defs)} changed def(s) NEVER run: "
                    + "; ".join(missed[:5])
                )
            if line_misses:
                parts.append("; ".join(line_misses[:4]))
            verdicts["G3 execution-proof"] = (
                ok3,
                (
                    f"all {len(defs)} changed def(s) + all changed lines execute under tests"
                    if ok3
                    else " | ".join(parts)
                ),
            )
        # G4: mutation on changed lines
        if args.fast:
            verdicts["G4 mutation-kill"] = (True, "skipped (--fast)")
        elif not defs:
            verdicts["G4 mutation-kill"] = (True, "no changed prod defs")
        else:
            print("G4: injecting mutants into changed lines ...")
            res = g4_mutation(shadow, changed, defs, args.kill)
            if res[0] is None:
                verdicts["G4 mutation-kill"] = (True, res[1])
            else:
                ok, detail, survivors = res
                verdicts["G4 mutation-kill"] = (ok, detail)
                for s in survivors[:8]:
                    print(f"    survivor: {s}")
    finally:
        _drop_shadow(shadow)
    return verdicts


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # audit: ok
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--path", default=None, help="project root (for audit-code wrapper)"
    )
    ap.add_argument("--fast", action="store_true", help="skip G4 mutation")
    ap.add_argument("--no-static", action="store_true", help="skip G1 baseline diff")
    ap.add_argument("--kill", type=int, default=60, help="required mutant kill %%")
    args = ap.parse_args()

    changed, deleted = _changed_files()
    py_changed = [p for p in changed if p.endswith(".py")]
    if not py_changed and not deleted:
        print("GATE: nothing to judge - working tree has no .py changes vs HEAD")
        sys.exit(2)
    print(f"judging {len(py_changed)} changed .py file(s), {len(deleted)} deleted\n")

    verdicts = {}
    # G0: every changed file must PARSE. AST-based checks silently skip
    # unparseable files, so a syntax error would otherwise VANISH from
    # static analysis instead of failing loudly.
    syntax_errors = []
    for rel in py_changed:
        src = ROOT / rel
        if not src.exists():
            continue
        try:
            ast.parse(src.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            syntax_errors.append(f"{rel}:{e.lineno} {e.msg}")
    verdicts["G0 syntax"] = (
        not syntax_errors,
        (
            "all changed files parse"
            if not syntax_errors
            else "; ".join(syntax_errors[:5])
        ),
    )
    if syntax_errors:
        print("G0 FAILED - changed files do not parse; later gates would be blind")
    shadow = _make_shadow(changed, deleted)
    verdicts.update(_run_gates(shadow, changed, args))

    print("\n" + "=" * 74)
    print("GATE VERDICT")
    print("=" * 74)
    ok_all = True
    for name, (ok, detail) in verdicts.items():
        ok_all &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:22} {detail}")
    print("=" * 74)
    if ok_all:
        print("PASS - change is statically clean, suite-green, EXECUTED by tests,")
        print("and the tests DETECT injected bugs in the changed lines.")
        print("Remaining risk is environment/integration: run the real e2e for that.")
    else:
        print("FAIL - fix the gates above before trusting this change.")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
