"""CLI entry point for audit-code.

Usage:
    audit-code                         run all checks on cwd
    audit-code --high                  only HIGH severity (default)
    audit-code --medium                HIGH + MEDIUM severity
    audit-code --info                  HIGH + MEDIUM + INFO
    audit-code --all                   all findings (same as --info)
    audit-code --verbose               full detail output
    audit-code --min                   fast checks: wiring + phd + quality
    audit-code --full                  complete analysis + raw output

    audit-code --phd                   PHD static audit only
    audit-code --phd --wiring          run phd + wiring only
    audit-code --phd --high -v         phd, HIGH only, full detail
    audit-code --suite --quality       test suite + quality gates only

    audit-code --path <dir>            audit a specific project
    audit-code --report-only           print findings, always exit 0
    audit-code gate                    judge only the working-tree diff vs HEAD
"""

import argparse
import sys

from audit_code.config import load_project_config
from audit_code.gate import run_gate as gate_main
from audit_code.models import EXIT_FAIL, EXIT_PASS
from audit_code.project import find_target_root
from audit_code.reporting import json_report, junit, sarif
from audit_code.runner import run_suite

ALL_MODULES = {
    "syntax",
    "wiring",
    "phd",
    "runtime",
    "suite",
    "quality",
    "tests",
    "python",
    "lint",
    "black",
}


def _is_gate_mode() -> bool:
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a == "gate"
    return False


def build_audit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit-test",
        description="Code and test verification orchestrator.",
        add_help=False,
    )
    parser.add_argument("-H", "--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--path",
        "-p",
        default=None,
        help="Path to project to audit (default: current directory)",
    )
    parser.add_argument(
        "--min",
        action="store_true",
        help="Fast local checks: wiring + phd + quality",
    )
    parser.add_argument(
        "-F",
        "--full",
        action="store_true",
        help="Complete analysis: all checks + full raw output",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help="Exit non-zero on any FAIL or CRASH (default)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print findings but always exit 0",
    )
    parser.add_argument(
        "-f",
        "--fix",
        action="store_true",
        help="Auto-format: run black + ruff --fix (modifies files)",
    )
    parser.add_argument(
        "--json",
        default="",
        metavar="FILE",
        help="Write JSON report to FILE",
    )
    parser.add_argument(
        "--sarif",
        default="",
        metavar="FILE",
        help="Write SARIF report to FILE (GitHub code scanning)",
    )
    parser.add_argument(
        "--junit",
        default="",
        metavar="FILE",
        help="Write JUnit XML report to FILE",
    )
    parser.add_argument(
        "--profile",
        default="",
        metavar="NAME",
        help="Enable a project-specific audit profile",
    )
    parser.add_argument(
        "--config",
        default="",
        metavar="FILE",
        help="Path to audit-code.toml config file",
    )

    # --- severity level (mutually exclusive) ---
    sev = parser.add_mutually_exclusive_group()
    sev.add_argument(
        "-h", "--high", action="store_true", help="Only HIGH severity (default)"
    )
    sev.add_argument(
        "-m", "--medium", action="store_true", help="HIGH + MEDIUM severity"
    )
    sev.add_argument("--info", action="store_true", help="HIGH + MEDIUM + INFO")
    sev.add_argument("--all", action="store_true", help="All findings (same as --info)")

    # --- verbosity ---
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Full detail output for every audit step",
    )

    # --- per-module selection (any combination) ---
    parser.add_argument(
        "--syntax", action="store_true", help="Run language syntax checks only"
    )
    parser.add_argument("--wiring", action="store_true", help="Run wiring audit")
    parser.add_argument("--phd", action="store_true", help="Run PHD static audit")
    parser.add_argument("--runtime", action="store_true", help="Run runtime audit")
    parser.add_argument("--suite", action="store_true", help="Run test suite audit")
    parser.add_argument("--quality", action="store_true", help="Run quality gates")
    parser.add_argument(
        "--tests", action="store_true", help="Run non-Python test suites"
    )
    parser.add_argument(
        "--python", action="store_true", help="Run Python syntax check only"
    )
    parser.add_argument("--lint", action="store_true", help="Run ruff lint only")
    parser.add_argument("--black", action="store_true", help="Run black format only")
    parser.add_argument(
        "-s",
        "--skip",
        default="",
        metavar="MODULES",
        help="Skip specific modules (comma-separated: phd,suite,quality)",
    )

    return parser


def build_gate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit-code gate",
        description="Judge ONLY the working-tree diff vs HEAD",
    )
    parser.add_argument("--path", "-p", default=None, help="Path to project")
    parser.add_argument(
        "--fast", action="store_true", help="Skip mutation testing (G4)"
    )
    parser.add_argument(
        "--no-static", action="store_true", help="Skip static baseline diff (G1)"
    )
    parser.add_argument(
        "--kill",
        type=int,
        default=60,
        metavar="PCT",
        help="Required mutant kill percentage (default: 60)",
    )
    sev = parser.add_mutually_exclusive_group()
    sev.add_argument(
        "--high", action="store_true", help="Only HIGH severity in G1 (default)"
    )
    sev.add_argument("--medium", action="store_true", help="HIGH + MEDIUM in G1")
    sev.add_argument("--info", action="store_true", help="All findings in G1")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Full detail output"
    )
    return parser


def _resolve_severity(args: argparse.Namespace) -> str | None:
    if args.info or args.all:
        return None
    if args.medium:
        return "MEDIUM"
    return "HIGH"


def _resolve_modules(args: argparse.Namespace) -> set[str] | None:
    """Return the set of modules selected, or None for all (mode logic).

    --fix with no module flags defaults to quality-only.
    --skip removes modules from the default set.
    """
    selected = {m for m in ALL_MODULES if getattr(args, m, False)}
    if not selected and args.fix:
        return {"quality"}
    if selected:
        return selected
    if args.skip:
        import re
        skip_set = {s.strip() for s in re.split(r"[, ]+", args.skip) if s.strip()}
        result = ALL_MODULES - skip_set
        # --min further restricts: skip runtime + suite (slow checks)
        if args.min:
            slow = {"runtime", "suite", "tests", "lint"}
            result -= slow
        return result
    if args.min:
        return {"syntax", "wiring", "phd", "quality"}
    return None  # all modules


def run_audit(args: argparse.Namespace) -> int:
    target_root = find_target_root(args.path)
    cfg = load_project_config(target_root, args.config)

    mode = "min" if args.min else ("full" if args.full else "default")
    profile = args.profile or next(iter(cfg.get("audit", {}).get("profiles") or []), "")
    severity = _resolve_severity(args)
    modules = _resolve_modules(args)

    results = run_suite(
        target_root,
        mode=mode,
        fix=args.fix,
        profile=profile,
        config=cfg,
        severity=severity,
        verbose=args.verbose,
        modules=modules,
    )

    reporting_cfg = cfg.get("reporting", {})
    json_out = args.json or reporting_cfg.get("json", "")
    sarif_out = args.sarif or reporting_cfg.get("sarif", "")
    junit_out = args.junit or reporting_cfg.get("junit", "")
    if json_out:
        json_report.write(results, json_out)
    if sarif_out:
        sarif.write(results, sarif_out)
    if junit_out:
        junit.write(results, junit_out)

    if args.report_only:
        return EXIT_PASS

    for r in results:
        if r.is_failure:
            return EXIT_FAIL
    return EXIT_PASS


def run_gate_cmd(args: argparse.Namespace) -> int:
    target_root = find_target_root(args.path)
    return gate_main(
        target_root, fast=args.fast, no_static=args.no_static, kill_pct=args.kill
    )


def _force_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


def main():
    _force_utf8_output()
    if _is_gate_mode():
        gate_idx = next(i for i, a in enumerate(sys.argv) if a == "gate")
        sys.argv = sys.argv[:gate_idx] + sys.argv[gate_idx + 1 :]
        parser = build_gate_parser()
        args = parser.parse_args()
        sys.exit(run_gate_cmd(args))
    else:
        _expand_bare_words()
        parser = build_audit_parser()
        args = parser.parse_args()
        sys.exit(run_audit(args))


def _expand_bare_words() -> None:
    """Convert bare words like 'phd high fix' into '--phd --high --fix'."""
    WORD_MAP = {
        # modules
        "syntax": "--syntax", "python": "--python",
        "wiring": "--wiring", "phd": "--phd",
        "runtime": "--runtime", "suite": "--suite",
        "quality": "--quality", "tests": "--tests",
        "lint": "--lint", "black": "--black",
        # severity
        "high": "--high", "medium": "--medium", "info": "--info", "all": "--all",
        # modes
        "fix": "--fix", "full": "--full", "min": "--min",
        "verbose": "--verbose", "strict": "--strict",
        "report": "--report-only",
    }
    new_argv = [sys.argv[0]]
    for arg in sys.argv[1:]:
        if arg.startswith("-") or arg == "gate":
            new_argv.append(arg)
        else:
            new_argv.append(WORD_MAP.get(arg.lower(), arg))
    sys.argv = new_argv


if __name__ == "__main__":
    main()
