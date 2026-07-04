"""CLI entry point for audit-code.

Usage:
    audit-code                    run normal audit on cwd
    audit-code --min              fast local checks only
    audit-code --full             complete analysis
    audit-code --path <dir>       audit a specific project
    audit-code --report-only      print findings, always exit 0
    audit-code gate               judge only the working-tree diff vs HEAD
    audit-code gate --fast        skip mutation (G4)
    audit-code gate --path <dir>  gate a specific project
    audit-code gate --kill N      set mutation kill % threshold
"""

import argparse
import sys

from audit_code.config import load_project_config
from audit_code.gate import run_gate as gate_main
from audit_code.models import EXIT_FAIL, EXIT_PASS
from audit_code.project import find_target_root
from audit_code.reporting import json_report, junit, sarif
from audit_code.runner import run_suite


def _is_gate_mode() -> bool:
    """Check if first positional arg is 'gate'."""
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a == "gate"
    return False


def build_audit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit-code",
        description="Code and test verification orchestrator.",
    )
    parser.add_argument(
        "--path",
        "-p",
        default=None,
        help="Path to project to audit (default: current directory)",
    )
    parser.add_argument(
        "--min",
        action="store_true",
        help="Fast local checks: syntax + static + basic security",
    )
    parser.add_argument(
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
    return parser


def build_gate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit-code gate",
        description="Judge ONLY the working-tree diff vs HEAD",
    )
    parser.add_argument(
        "--path",
        "-p",
        default=None,
        help="Path to project (default: current directory)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip mutation testing (G4)",
    )
    parser.add_argument(
        "--no-static",
        action="store_true",
        help="Skip static baseline diff (G1)",
    )
    parser.add_argument(
        "--kill",
        type=int,
        default=60,
        metavar="PCT",
        help="Required mutant kill percentage (default: 60)",
    )
    return parser


def run_audit(args: argparse.Namespace) -> int:
    """Run the standard audit suite."""
    target_root = find_target_root(args.path)

    cfg = load_project_config(target_root, args.config)

    mode = "min" if args.min else ("full" if args.full else "default")
    profile = args.profile or next(iter(cfg.get("audit", {}).get("profiles") or []), "")
    results = run_suite(
        target_root, mode=mode, fix=args.fix, profile=profile, config=cfg
    )

    # Write reports (CLI flag wins; audit-code.toml [reporting] is the default)
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
    """Run the change gate."""
    target_root = find_target_root(args.path)

    return gate_main(
        target_root,
        fast=args.fast,
        no_static=args.no_static,
        kill_pct=args.kill,
    )


def _force_utf8_output() -> None:
    """Windows consoles default to cp1252, which cannot print the ✓/✗ status
    glyphs — reconfigure stdout/stderr instead of crashing mid-report."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(  # type: ignore[union-attr]
                encoding="utf-8", errors="replace"
            )
        except (AttributeError, OSError):
            pass


def main():
    _force_utf8_output()
    if _is_gate_mode():
        # Remove 'gate' from argv so argparse parses cleanly
        gate_idx = next(i for i, a in enumerate(sys.argv) if a == "gate")
        gate_args = sys.argv[:gate_idx] + sys.argv[gate_idx + 1 :]
        sys.argv = gate_args
        parser = build_gate_parser()
        args = parser.parse_args()
        sys.exit(run_gate_cmd(args))
    else:
        parser = build_audit_parser()
        args = parser.parse_args()
        sys.exit(run_audit(args))


if __name__ == "__main__":
    main()
