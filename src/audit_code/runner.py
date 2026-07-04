"""Runner — orchestrates the full audit suite.

Order:
  1. Language detection (adapters), filtered by [audit].languages config.
  2. Per-language syntax audits — real checks or honest SKIPs.
  3. Native test suites for non-Python languages (default/full modes).
  4. The five Python deep audits (wiring/phd/runtime/suite/quality) —
     only when Python was detected.
  5. Optional --profile checks.
"""

import time
from pathlib import Path

from audit_code import phd, quality, runtime, suite, wiring
from audit_code.adapters import discover
from audit_code.adapters.base import run_tool, which
from audit_code.config import FULL_SUITE_TIMEOUT
from audit_code.models import AuditResult, AuditStatus
from audit_code.profiles import load as profile_load


def run_suite(
    target_root: Path,
    mode: str = "default",
    fix: bool = False,
    profile: str = "",
    config: dict | None = None,
) -> list[AuditResult]:
    """Run the full audit suite against a target project."""
    results: list[AuditResult] = []
    start = time.monotonic()

    adapters = discover(target_root)
    wanted = [
        str(lang).lower()
        for lang in ((config or {}).get("audit", {}).get("languages") or [])
    ]
    if wanted and "auto" not in wanted:
        adapters = [a for a in adapters if a.language in wanted]
    print(
        "  languages: " + (", ".join(a.language for a in adapters) or "none detected")
    )

    # 1. Per-language syntax audits
    for adapter in adapters:
        _run_step(
            results,
            adapter.audit_id(),
            f"{adapter.language} syntax",
            lambda a=adapter: a.syntax_check(target_root),
        )

    # 2. Native test suites for non-Python (Python's `suite` audit covers it)
    if mode != "min":
        for adapter in adapters:
            if adapter.language == "python":
                continue
            cmd = adapter.test_command(target_root)
            if not cmd:
                continue
            _run_step(
                results,
                f"{adapter.language}-tests",
                f"{adapter.language} test suite",
                lambda c=cmd, lang=adapter.language: _run_test_suite(
                    target_root, lang, c
                ),
            )

    # 3. Python deep audits
    python_detected = any(a.language == "python" for a in adapters)
    if python_detected:
        audit_modules = [
            ("wiring", "Is it connected?"),
            ("phd", "Does it meet the bar?"),
            ("runtime", "Will it hang or crash?"),
            ("suite", "Is the test suite healthy?"),
            ("quality", "External gates + execution proof"),
        ]
        if mode == "min":
            # Fast mode: wiring + quality only (skip slow suite/coverage runs)
            audit_modules = [
                ("wiring", "Is it connected?"),
                ("quality", "External gates (fast checks only)"),
            ]
        for module_name, description in audit_modules:
            _run_step(
                results,
                module_name,
                description,
                lambda m=module_name: _run_one_module(target_root, m, mode, fix),
            )
    else:
        skip = AuditResult(
            audit_id="python-audits",
            status=AuditStatus.SKIP,
            stdout=(
                "no Python detected — wiring/phd/runtime/suite/quality "
                "audits skipped"
            ),
        )
        results.append(skip)
        print(f"  [-] {'python-audits':16} {_detail_line(skip)}")

    # 4. Optional profile
    if profile:
        profile_fn = profile_load(profile)
        if profile_fn is None:
            err = AuditResult(
                audit_id=f"profile-{profile}",
                status=AuditStatus.ERROR,
                stderr=f"unknown profile: {profile}",
            )
            results.append(err)
            print(f"  [!] {f'profile-{profile}':16} {_detail_line(err)}")
        else:
            _run_step(
                results,
                f"profile-{profile}",
                f"profile checks: {profile}",
                lambda: profile_fn(target_root),
            )

    total = round(time.monotonic() - start, 1)

    # Print summary
    print()
    print("=" * 60)
    print("AUDIT RESULTS")
    print("=" * 60)
    for r in results:
        print(f"  [{r.status.value:5}] {r.audit_id:16} {_detail_line(r)}")
    print("=" * 60)
    print(f"Total: {total}s   mode: {mode}")

    if mode == "full":
        for r in results:
            if r.stdout:
                print(f"\n{'#' * 74}\n# {r.audit_id}\n{'#' * 74}")
                print(r.stdout)

    return results


def _run_step(results: list, audit_id: str, description: str, fn) -> None:
    """Run one audit step with timing, crash capture, and progress output."""
    progress = f"  [{audit_id:16}] {description} ... "
    print(progress, end="", flush=True)
    step_start = time.monotonic()
    try:
        result = fn()
    except Exception as exc:
        result = AuditResult(
            audit_id=audit_id,
            status=AuditStatus.CRASH,
            stderr=f"{type(exc).__name__}: {exc}",
        )
    result.duration_seconds = round(time.monotonic() - step_start, 1)
    results.append(result)
    line = f"  [{_status_char(result.status)}] {audit_id:16} {_detail_line(result)}"
    print(f"\r{line}{' ' * max(0, len(progress) - len(line))}")


def _run_test_suite(target_root: Path, language: str, cmd: list) -> AuditResult:
    """Execute a non-Python language's native test suite."""
    exe = cmd[0]
    if not which(exe) and not Path(exe).exists():
        return AuditResult(
            audit_id=f"{language}-tests",
            status=AuditStatus.SKIP,
            stdout=f"test runner not found: {exe}",
            tool_missing=True,
        )
    rc, out, err = run_tool(cmd, target_root, timeout=FULL_SUITE_TIMEOUT)
    tail = "\n".join(((out + "\n" + err).strip()).splitlines()[-30:])
    if rc == -1:
        status = AuditStatus.FAIL
        tail = f"[timed out after {FULL_SUITE_TIMEOUT}s]\n" + tail
    elif rc == -2:
        status = AuditStatus.SKIP
    else:
        status = AuditStatus.PASS if rc == 0 else AuditStatus.FAIL
    return AuditResult(
        audit_id=f"{language}-tests",
        status=status,
        stdout=f"$ {' '.join(str(c) for c in cmd)}\n{tail}",
        high=0 if status == AuditStatus.PASS else 1,
    )


def _run_one_module(
    target_root: Path, module_name: str, mode: str, fix: bool = False
) -> AuditResult:
    """Run one Python audit module via direct import."""

    module_map = {
        "wiring": wiring,
        "phd": phd,
        "runtime": runtime,
        "suite": suite,
        "quality": quality,
    }

    mod = module_map.get(module_name)
    if mod is None:
        return AuditResult(
            audit_id=module_name,
            status=AuditStatus.ERROR,
            stderr=f"Unknown audit module: {module_name}",
        )

    run_fn = getattr(mod, "run", None)
    if run_fn is None:
        return AuditResult(
            audit_id=module_name,
            status=AuditStatus.ERROR,
            stderr=f"Module {module_name} has no run() function",
        )

    kwargs = {}
    if module_name == "quality" and mode == "min":
        kwargs["fast"] = True  # skip coverage in min mode
    if module_name == "quality" and fix:
        kwargs["fix"] = True

    return run_fn(target_root, **kwargs)


def _status_char(status: AuditStatus) -> str:
    chars = {
        AuditStatus.PASS: "✓",
        AuditStatus.WARN: "△",
        AuditStatus.FAIL: "✗",
        AuditStatus.CRASH: "☠",
        AuditStatus.ERROR: "!",
        AuditStatus.SKIP: "-",
    }
    return chars.get(status, "?")


def _detail_line(result: AuditResult) -> str:
    if result.status == AuditStatus.CRASH:
        msg = (result.stderr.strip().splitlines() or ["no output"])[-1]
        return msg[:80]
    if result.status == AuditStatus.ERROR:
        return result.stderr[:80]
    if result.status == AuditStatus.SKIP:
        reason = (result.stdout.strip().splitlines() or ["skipped"])[0]
        return f"SKIP: {reason}"[:80]
    if result.status == AuditStatus.PASS:
        return "clean"
    parts = []
    if result.high:
        parts.append(f"{result.high} HIGH")
    if result.medium:
        parts.append(f"{result.medium} MEDIUM")
    if result.info:
        parts.append(f"{result.info} INFO")
    return ", ".join(parts) if parts else "clean"
