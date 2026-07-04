"""Runner — orchestrates the full audit suite.

Replaces run_all_audits.py. Imports audit modules directly instead of
subprocess + regex-parsing stdout.
"""

import time
from pathlib import Path

from audit_code import phd, quality, runtime, suite, wiring
from audit_code.models import AuditResult, AuditStatus


def run_suite(
    target_root: Path, mode: str = "default", fix: bool = False
) -> list[AuditResult]:
    """Run the full audit suite against a target project.

    Args:
        target_root: path to the project being audited.
        mode: "min", "default", or "full".

    Returns:
        List of AuditResult, one per audit module.
    """
    results: list[AuditResult] = []
    start = time.monotonic()

    # Audit order matches the original: wiring, phd, runtime, suite, quality
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
        print(f"  [{module_name:8}] {description} ...", end=" ", flush=True)
        audit_start = time.monotonic()
        try:
            result = _run_one_module(target_root, module_name, mode, fix)
        except Exception as exc:
            result = AuditResult(
                audit_id=module_name,
                status=AuditStatus.CRASH,
                stderr=f"{type(exc).__name__}: {exc}",
            )
        result.duration_seconds = round(time.monotonic() - audit_start, 1)
        results.append(result)

        # Print one-line status
        status_char = _status_char(result.status)
        detail = _detail_line(result)
        print(f"\r  [{status_char}] {module_name:8} {detail}")

    total = round(time.monotonic() - start, 1)

    # Print summary
    print()
    print("=" * 60)
    print("AUDIT RESULTS")
    print("=" * 60)
    for r in results:
        print(f"  [{r.status.value:5}] {r.audit_id:10} {_detail_line(r)}")
    print("=" * 60)
    print(f"Total: {total}s   mode: {mode}")

    if mode == "full":
        for r in results:
            if r.stdout:
                print(f"\n{'#' * 74}\n# {r.audit_id}\n{'#' * 74}")
                print(r.stdout)

    return results


def _run_one_module(
    target_root: Path, module_name: str, mode: str, fix: bool = False
) -> AuditResult:
    """Run one audit module, preferring direct import over subprocess."""

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

    # Call the module's run() function if it exists
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

    result = run_fn(target_root, **kwargs)
    return result


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
