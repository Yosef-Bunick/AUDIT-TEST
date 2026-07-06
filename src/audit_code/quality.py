"""quality.py - external-tool quality gates + EXECUTION-verified coverage.

Q1 [MEDIUM] black --check
Q2 [MEDIUM/HIGH] ruff lint (I=import-order, S=security)
Q3 [MEDIUM] mypy
Q4 [HIGH] CVE scan
Q5 [MEDIUM] per-def EXECUTION coverage
Q6 [MEDIUM] docstring coverage
Q7 [MEDIUM] test hygiene
Q8 [INFO] mutation testing (opt-in)
"""

import argparse as _argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from audit_code.audit_shared import force_utf8_streams, should_audit
from audit_code.config import (
    DOC_THRESHOLD_PCT,
    MIN_FLAG_BODY_LINES,
    TOOL_TIMEOUT,
)  # noqa: E402
from audit_code.models import (
    AuditResult,
    AuditStatus,
    Finding,
    Severity,
)

EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "graphify-out",
    "sandbox",
    ".ruff_cache",
    ".pytest_cache",
    "scratch",
    "dist",
    "build",
    ".eggs",
    "bunick-ai-desktop",
    "logs",
    "eval_results",
    "golden_tasks",
    "fixes and info",
    ".vscode",
    ".idea",
}


def _tool(name: str, target_root: Path) -> str | None:
    try:
        spec = importlib.util.find_spec(name.replace("-", "_"))
        if spec is not None:
            locs = list(getattr(spec, "submodule_search_locations", None) or [])
            origin = spec.origin or (locs[0] if locs else "")
            if origin:
                origin_path = Path(origin).resolve()
                # If inside target_root but under .venv/ or venv/, it's a
                # legitimate installed package, not a project shadow.
                in_venv = any(p in (".venv", "venv") for p in origin_path.parts)
                if origin:
                    origin_path = Path(origin).resolve()
                    in_venv = any(p in (".venv", "venv") for p in origin_path.parts)
                    if not origin_path.is_relative_to(target_root) or in_venv:
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
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            py_file = Path(dirpath) / fn
            if any(part in EXCLUDE_DIRS for part in py_file.parts):
                continue
            if not should_audit(py_file):  # honour active focus group
                continue
            (
                tests
                if tests_dir in py_file.parents or py_file.parent == tests_dir
                else prod
            ).append(py_file)
    return prod, tests


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

    walk(tree)
    return out


# ── Q5 coverage cache ─────────────────────────────────────────────────────────
# Quality-only mode (e.g. `audit-test q v`) has no `suite` audit to share a
# coverage run with, so Q5 would run the whole test suite again. Cache the
# `.coverage` data keyed by a byte-fingerprint of every source + test file: a
# hit means the code is identical, so the coverage result is provably the same
# and a full rerun is skipped. Any edit changes the fingerprint and reruns.


def _q5_cache_dir(root: Path) -> Path:
    """Persistent, per-project directory for the Q5 coverage cache."""
    key = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "audit_q5_cache" / key


def _q5_fingerprint(root: Path, tests_dir: Path, pytest_extra: str = "") -> str:
    """Content hash of every source + test .py plus the pytest invocation."""
    prod, tests = _py_files(root, tests_dir)
    h = hashlib.sha256()
    h.update(b"q5-cache-v1\0")
    h.update(pytest_extra.encode("utf-8") + b"\0")
    for p in sorted(prod + tests):
        try:
            h.update(str(p.relative_to(root)).encode("utf-8") + b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
        except (OSError, ValueError):
            continue
    return h.hexdigest()


def _q5_cache_load(root: Path, fingerprint: str) -> Path | None:
    """Return the cached .coverage path if it matches *fingerprint*, else None."""
    if os.environ.get("AUDIT_NO_Q5_CACHE"):
        return None
    cache = _q5_cache_dir(root)
    key_file = cache / "key"
    cov_file = cache / "cov.coverage"
    try:
        if (
            key_file.exists()
            and cov_file.exists()
            and key_file.read_text(encoding="utf-8") == fingerprint
        ):
            return cov_file
    except OSError:
        return None
    return None


def _q5_cache_save(root: Path, fingerprint: str, data_file: Path) -> None:
    """Persist *data_file* as the Q5 coverage cache for *fingerprint* (best-effort)."""
    cache = _q5_cache_dir(root)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copy2(data_file, cache / "cov.coverage")
        (cache / "key").write_text(fingerprint, encoding="utf-8")
    except OSError:
        pass


def run(  # needs fix (god function 550+ lines — decompose into sub-audits)
    target_root: Path,
    fast: bool = False,
    strict_mypy: bool = False,
    mutation: bool = False,
    strict: bool = True,
    tests: str = "tests",
    pytest_extra: str = "-p no:logfire",
    fix: bool = False,
    shared_cov: Path | None = None,
) -> AuditResult:
    """Run quality audit against a target project.

    shared_cov: coverage data file already produced by the suite audit's run.
    When present and non-empty, Q5 reuses it instead of running the whole test
    suite a second time under coverage."""
    findings: list[Finding] = []
    stdout_lines: list[str] = []

    # --fix mode: format + lint-fix only, skip coverage/mypy/CVE/etc
    if fix:
        fast = True

    force_utf8_streams()

    root = target_root.resolve()
    tests_dir = (root / tests).resolve()
    counts = {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    prod, test_files = _py_files(root, tests_dir)
    stdout_lines.append(
        f"scanned root: {root} ({len(prod)} prod files, {len(test_files)} test files)"
    )
    stdout_lines.append("")

    # Q0: syntax
    bad = []
    for p in prod + test_files:
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            bad.append(f"{p.relative_to(root)}:{e.lineno}  {e.msg}")
    stdout_lines.append("=" * 74)
    stdout_lines.append(f"Q0 [HIGH] files that do not PARSE - {len(bad)} finding(s)")
    stdout_lines.append("=" * 74)
    counts["HIGH"] += len(bad)
    for b in bad[:15]:
        stdout_lines.append(f"  {b}")
        findings.append(
            Finding(rule_id="Q0", severity=Severity.HIGH, message=b, source="quality")
        )
    if not bad:
        stdout_lines.append("  every .py file parses")
    stdout_lines.append("")

    # Q1: black
    tool = _tool("black", target_root)
    stdout_lines.append("=" * 74)
    stdout_lines.append("Q1 [MEDIUM] black formatting drift")
    stdout_lines.append("=" * 74)
    if not tool:
        stdout_lines.append("  SKIP: black not installed (pip install black)")
        stdout_lines.append("")
    elif fix:
        excl = "|".join(re.escape(d) for d in sorted(EXCLUDE_DIRS))
        rc, out = _run(tool.split() + [".", "--extend-exclude", excl], root)
        changed = len(re.findall(r"^reformatted (.+)$", out, re.MULTILINE))
        msg = (
            f"\n  black: {changed} file(s) reformatted"
            if changed
            else "\n  black: already clean"
        )
        stdout_lines.append(msg)
        print(msg)
        stdout_lines.append("")
    else:
        excl = "|".join(re.escape(d) for d in sorted(EXCLUDE_DIRS))
        rc, out = _run(tool.split() + ["--check", ".", "--extend-exclude", excl], root)
        files = re.findall(r"^would reformat (.+)$", out, re.MULTILINE)
        if rc == 0:
            stdout_lines.append("  formatted cleanly")
        elif not files and rc != 1:
            stdout_lines.append(f"  SKIP: black errored: {out.strip()[:200]}")
        else:
            counts["MEDIUM"] += len(files)
            stdout_lines.append(
                f"  {len(files)} file(s) would be reformatted (first 15):"
            )
            for f in files[:15]:
                stdout_lines.append(f"    {f}")
                findings.append(
                    Finding(
                        rule_id="Q1",
                        severity=Severity.MEDIUM,
                        message=f"would reformat {f}",
                        source="quality",
                    )
                )
        stdout_lines.append("")

    # Q2: ruff
    tool = _tool("ruff", target_root)
    stdout_lines.append("=" * 74)
    stdout_lines.append("Q2 [MEDIUM/HIGH] ruff lint (I=import-order, S=security->HIGH)")
    stdout_lines.append("=" * 74)
    if not tool:
        stdout_lines.append("  SKIP: ruff not installed (pip install ruff)")
        stdout_lines.append("")
    elif fix:
        rc, out = _run(
            tool.split()
            + [
                "check",
                ".",
                "--fix",
                "--select",
                "E,F,W,I,B,S",
                "--ignore",
                "S101,S105,S110,S112,S603,S607,B007,B023,B905,E501",
                "--exit-zero",
            ],
            root,
        )
        fixed_count = len(re.findall(r"^Fixed \d", out, re.MULTILINE)) or (
            1 if "fixed" in out.lower() else 0
        )
        msg = f"\n  ruff: {fixed_count} issue(s) auto-fixed"
        stdout_lines.append(msg)
        print(msg)
        # Still report remaining unfixable issues
        stdout_lines.append("  re-running check for remaining issues...")
        rc2, out2 = _run(
            tool.split()
            + [
                "check",
                ".",
                "--select",
                "E,F,W,I,B,S",
                "--ignore",
                "S101,S105,S110,S112,S603,S607,B007,B023,B905,E501",
                "--output-format",
                "json",
                "--exit-zero",
            ],
            root,
        )
        try:
            ruff_findings = json.loads(out2[out2.index("[") : out2.rindex("]") + 1])
        except (ValueError, json.JSONDecodeError):
            ruff_findings = []
        sec = [f for f in ruff_findings if str(f.get("code", "")).startswith("S")]
        lint = [f for f in ruff_findings if not str(f.get("code", "")).startswith("S")]
        counts["HIGH"] += len(sec)
        counts["MEDIUM"] += len(lint)
        stdout_lines.append(
            f"  remaining — security (S*): {len(sec)}   lint/style: {len(lint)}"
        )
        for f in sec:
            findings.append(
                Finding(
                    rule_id="Q2",
                    severity=Severity.HIGH,
                    message=f"{f.get('code')}: {f.get('message', '')}",
                    file=f.get("filename"),
                    source="quality",
                )
            )
        for f in lint:
            findings.append(
                Finding(
                    rule_id="Q2",
                    severity=Severity.MEDIUM,
                    message=f"{f.get('code')}: {f.get('message', '')}",
                    file=f.get("filename"),
                    source="quality",
                )
            )
        stdout_lines.append("")
    else:
        rc, out = _run(
            tool.split()
            + [
                "check",
                ".",
                "--select",
                "E,F,W,I,B,S",
                "--ignore",
                "S101,S105,S110,S112,S603,S607,B007,B023,B905,E501",
                "--output-format",
                "json",
                "--exit-zero",
            ],
            root,
        )
        try:
            ruff_findings = json.loads(out[out.index("[") : out.rindex("]") + 1])
        except (ValueError, json.JSONDecodeError):
            stdout_lines.append(
                f"  SKIP: could not parse ruff output: {out.strip()[:200]}"
            )
            stdout_lines.append("")
            ruff_findings = []
        sec = [f for f in ruff_findings if str(f.get("code", "")).startswith("S")]
        lint = [f for f in ruff_findings if not str(f.get("code", "")).startswith("S")]
        counts["HIGH"] += len(sec)
        counts["MEDIUM"] += len(lint)
        stdout_lines.append(f"  security (S*): {len(sec)}   lint/style: {len(lint)}")
        for f in sec:
            findings.append(
                Finding(
                    rule_id="Q2",
                    severity=Severity.HIGH,
                    message=f"{f.get('code')}: {f.get('message', '')}",
                    file=f.get("filename"),
                    source="quality",
                )
            )
        for f in lint:
            findings.append(
                Finding(
                    rule_id="Q2",
                    severity=Severity.MEDIUM,
                    message=f"{f.get('code')}: {f.get('message', '')}",
                    file=f.get("filename"),
                    source="quality",
                )
            )
        stdout_lines.append("")

    # Q3: mypy
    tool = _tool("mypy", target_root)
    stdout_lines.append("=" * 74)
    stdout_lines.append(
        f"Q3 [MEDIUM] mypy type errors ({'strict' if strict_mypy else 'default'})"
    )
    stdout_lines.append("=" * 74)
    if not tool:
        stdout_lines.append("  SKIP: mypy not installed (pip install mypy)")
        stdout_lines.append("")
    else:
        args = tool.split() + [
            ".",
            "--ignore-missing-imports",
            "--no-error-summary",
            "--follow-imports=silent",
            "--exclude",
            "|".join(sorted(EXCLUDE_DIRS)),
        ]
        if strict_mypy:
            args.append("--strict")
        rc, out = _run(args, root)
        errs = [line for line in out.splitlines() if ": error:" in line]
        if rc in (-1, -2):
            stdout_lines.append(f"  SKIP: {out.strip()[:200]}")
        else:
            counts["MEDIUM"] += len(errs)
            if not errs:
                stdout_lines.append("  clean")
            else:
                stdout_lines.append(f"  {len(errs)} error(s) (first 10):")
                for line in errs[:10]:
                    stdout_lines.append(f"    {line[:120]}")
                    findings.append(
                        Finding(
                            rule_id="Q3",
                            severity=Severity.MEDIUM,
                            message=line[:200],
                            source="quality",
                        )
                    )
        stdout_lines.append("")

    # Q4: CVEs
    stdout_lines.append("=" * 74)
    stdout_lines.append("Q4 [HIGH] known CVEs in installed dependencies")
    stdout_lines.append("=" * 74)
    cve_found = False
    for name, cargs in (
        ("pip-audit", ["--progress-spinner", "off"]),
        ("safety", ["check", "--output", "text"]),
    ):
        tool = _tool(name, target_root)
        if not tool:
            continue
        rc, out = _run(tool.split() + cargs, root, timeout=300)
        if rc in (-1, -2) or "error" in out.lower()[:200]:
            continue
        vulns = len(
            re.findall(r"(?i)\bvulnerabilit(?:y|ies) found|-> vuln|CVE-\d{4}", out)
        )
        if rc == 0 and not vulns:
            stdout_lines.append(f"  {name}: no known vulnerabilities")
            cve_found = True
            break
        counts["HIGH"] += max(vulns, 1)
        cve_found = True
        stdout_lines.append(f"  {name}: {max(vulns,1)} vulnerability signal(s)")
        findings.append(
            Finding(
                rule_id="Q4",
                severity=Severity.HIGH,
                message=f"{max(vulns,1)} vulnerabilities via {name}",
                source="quality",
            )
        )
        break
    if not cve_found:
        stdout_lines.append("  SKIP: neither pip-audit nor safety installed")
    stdout_lines.append("")

    # Q5: per-def execution coverage
    stdout_lines.append("=" * 74)
    stdout_lines.append("Q5 [MEDIUM] defs whose body NEVER EXECUTES under the suite")
    stdout_lines.append("=" * 74)
    if fast:
        stdout_lines.append("  SKIP: --fast")
        stdout_lines.append("")
    else:
        if importlib.util.find_spec("coverage") is None:
            stdout_lines.append("  SKIP: coverage not installed (pip install coverage)")
            stdout_lines.append("")
        else:
            tmp = Path(tempfile.mkdtemp(prefix="audit_q5_"))
            json_file = tmp / "cov.json"
            rc = 0
            cache_hit = False
            # Byte-fingerprint of all source + test files: identical bytes ⇒
            # identical coverage, so a cached .coverage can stand in for a rerun.
            cache_key = _q5_fingerprint(root, tests_dir, pytest_extra)
            if (
                shared_cov is not None
                and shared_cov.exists()
                and shared_cov.stat().st_size > 0
            ):
                # The suite audit already ran the whole suite under coverage;
                # reuse its data instead of a second full test run.
                data_file = shared_cov
                env = dict(os.environ, COVERAGE_FILE=str(data_file))
                stdout_lines.append(
                    "  reusing the suite audit's coverage run (deduped — no second run)"
                )
            else:
                cached = _q5_cache_load(root, cache_key)
                if cached is not None:
                    # Quality-only mode: no suite ran, but nothing changed since
                    # the last coverage run, so reuse the cached .coverage.
                    data_file = cached
                    env = dict(os.environ, COVERAGE_FILE=str(data_file))
                    cache_hit = True
                    stdout_lines.append(
                        "  reusing cached Q5 coverage (source unchanged since last "
                        "run; set AUDIT_NO_Q5_CACHE=1 to force a rerun)"
                    )
                else:
                    data_file = tmp / ".coverage"
                    env = dict(os.environ, COVERAGE_FILE=str(data_file))
                    stdout_lines.append(
                        "  running suite under coverage (this is a full test run)..."
                    )
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
                            *pytest_extra.split(),
                        ],
                        root,
                        timeout=1800,
                        env=env,
                    )
            # Persist a freshly produced coverage file (from either the shared
            # suite run or this mode's own run) so the next quality-only run is
            # instant while the source is unchanged.
            if not cache_hit and data_file.exists() and data_file.stat().st_size > 0:
                _q5_cache_save(root, cache_key, data_file)
            if not data_file.exists():
                reason = (
                    f"coverage run exit={rc}"
                    if rc != 0
                    else "no .coverage file produced"
                )
                stdout_lines.append(
                    f"  SKIP: {reason} — did pytest crash or have no tests?"
                )
                stdout_lines.append("")
            else:
                rc2, out2 = _run(
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
                except OSError as e:
                    stdout_lines.append(f"  SKIP: could not read coverage json ({e})")
                    stdout_lines.append("")
                    cov = None
                except json.JSONDecodeError as e:
                    stdout_lines.append(f"  SKIP: coverage json is malformed: {e}")
                    stdout_lines.append("")
                    cov = None
                if cov is not None and not cov.get("files"):
                    stdout_lines.append(
                        "  SKIP: coverage json has no file data (empty project?)"
                    )
                    stdout_lines.append("")
                    cov = None
                if cov is not None:
                    executed = {}
                    for fname, fdata in cov.get("files", {}).items():
                        executed[Path(root / fname).resolve()] = set(
                            fdata.get("executed_lines", [])
                        )

                    prod_files, _ = _py_files(root, tests_dir)
                    total = never = 0
                    flagged = []
                    for p in prod_files:
                        lines = executed.get(p.resolve())
                        for qual, defline, b0, b1 in _def_spans(p):
                            total += 1
                            ran = bool(lines) and any(
                                ln in (lines or set()) for ln in range(b0, b1 + 1)
                            )
                            if not ran:
                                never += 1
                                if (b1 - b0 + 1) >= MIN_FLAG_BODY_LINES:
                                    flagged.append(
                                        (
                                            p.relative_to(root),
                                            defline,
                                            qual,
                                            b1 - b0 + 1,
                                        )
                                    )
                    pct = 100.0 * (total - never) / total if total else 100.0
                    counts["MEDIUM"] += len(flagged)
                    stdout_lines.append(
                        f"  {total} defs scanned; {total - never} executed under tests "
                        f"({pct:.1f}%); {never} never ran ({len(flagged)} flagged)"
                    )
                    shown = 0
                    for rel, def_ln, qual, size in sorted(flagged, key=lambda x: -x[3]):
                        if shown < 25:
                            shown += 1
                            stdout_lines.append(
                                f"    {str(rel):44} :{def_ln:<5} {qual}  ({size} lines)"
                            )
                        findings.append(
                            Finding(
                                rule_id="Q5",
                                severity=Severity.MEDIUM,
                                message=f"{qual} never executed",
                                file=str(rel),
                                line=def_ln,
                                source="quality",
                            )
                        )
            stdout_lines.append("")

    # Q6: docstring coverage
    stdout_lines.append("=" * 74)
    stdout_lines.append(
        f"Q6 [MEDIUM] docstring coverage (< {DOC_THRESHOLD_PCT}% fails)"
    )
    stdout_lines.append("=" * 74)
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
    stdout_lines.append(
        f"  {have}/{need} public defs+classes documented ({pct:.1f}%) - "
        f"{'PASS' if ok else 'FAIL'}"
    )
    if not ok:
        for rel, n in sorted(worst.items(), key=lambda kv: -kv[1])[:10]:
            stdout_lines.append(f"    {n:4} undocumented in {rel}")
            findings.append(
                Finding(
                    rule_id="Q6",
                    severity=Severity.MEDIUM,
                    message=f"{n} undocumented in {rel}",
                    source="quality",
                )
            )
    stdout_lines.append("")

    # Q7: test hygiene
    stdout_lines.append("=" * 74)
    hygiene_findings = []
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
                    hygiene_findings.append(
                        (p, node.lineno, "time.sleep() in a test - flaky AND slow")
                    )
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr == "skip"
                    and not node.args
                    and not node.keywords
                ):
                    hygiene_findings.append(
                        (p, node.lineno, "skip with NO reason - rots silently")
                    )
    stdout_lines.append(
        f"Q7 [MEDIUM] test hygiene - {len(hygiene_findings)} finding(s)"
    )
    stdout_lines.append("=" * 74)
    counts["MEDIUM"] += len(hygiene_findings)
    for p, h_ln, msg in hygiene_findings[:20]:
        stdout_lines.append(f"  {p.relative_to(root)}:{h_ln}  {msg}")
        findings.append(
            Finding(
                rule_id="Q7",
                severity=Severity.MEDIUM,
                message=msg,
                file=str(p.relative_to(root)),
                line=h_ln,
                source="quality",
            )
        )
    stdout_lines.append("")

    # Q8: mutation (opt-in)
    stdout_lines.append("=" * 74)
    stdout_lines.append("Q8 [INFO] mutation testing")
    stdout_lines.append("=" * 74)
    tool = _tool("mutmut", target_root)
    if not tool:
        stdout_lines.append("  SKIP: mutmut not installed")
    elif not mutation:
        stdout_lines.append("  SKIP: pass --mutation to run (slow)")
    else:
        rc, out = _run(
            tool.split() + ["run", "--paths-to-mutate", "."], root, timeout=3600
        )
        killed = len(re.findall(r"killed", out, re.IGNORECASE))
        survived = len(re.findall(r"survived", out, re.IGNORECASE))
        stdout_lines.append(f"  killed: {killed}   survived: {survived}")
        counts["INFO"] += survived
    stdout_lines.append("")

    # Summary
    stdout_lines.append("=" * 74)
    stdout_lines.append(
        f"SUMMARY  HIGH: {counts['HIGH']}   MEDIUM: {counts['MEDIUM']}   "
        f"INFO: {counts['INFO']}   suppressed: 0"
    )

    status = (
        AuditStatus.FAIL
        if counts["HIGH"]
        else (
            AuditStatus.WARN
            if (counts["MEDIUM"] or counts["INFO"])
            else AuditStatus.PASS
        )
    )

    return AuditResult(
        audit_id="quality",
        status=status,
        findings=findings,
        high=counts["HIGH"],
        medium=counts["MEDIUM"],
        info=counts["INFO"],
        stdout="\n".join(stdout_lines),
        completed=True,
    )


if __name__ == "__main__":
    ap = _argparse.ArgumentParser()
    ap.add_argument("--path", default=None, help="repo root")
    ap.add_argument("--tests", default="tests")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--strict-mypy", action="store_true")
    ap.add_argument("--mutation", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--pytest-extra", default="-p no:logfire")
    args = ap.parse_args()

    root = Path(args.path).resolve() if args.path else Path.cwd()
    result = run(
        root,
        fast=args.fast,
        strict_mypy=args.strict_mypy,
        mutation=args.mutation,
        strict=args.strict,
        tests=args.tests,
        pytest_extra=args.pytest_extra,
    )
    print(result.stdout)
    if args.strict and result.high:
        sys.exit(1)
