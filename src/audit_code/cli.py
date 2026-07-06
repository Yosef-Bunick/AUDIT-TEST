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
import json
import os
import sys
from collections import namedtuple as _nt  # audit: ok (focus helpers)
from pathlib import Path

from audit_code import encoding_check
from audit_code.audit_shared import force_utf8_streams, normalize_encoding
from audit_code.config import load_project_config
from audit_code.gate import run_gate as gate_main
from audit_code.models import EXIT_FAIL, EXIT_PASS
from audit_code.profiler import (
    classify_dead_symbols,
    compare_projects,
    profile_project,
)
from audit_code.project import find_target_root
from audit_code.reporting import json_report, junit, sarif
from audit_code.runner import run_suite
from audit_code.surgeon import main as surgeon_main

ALL_MODULES = {
    "syntax",
    "wiring",
    "phd",
    "runtime",
    "suite",
    "quality",
    "encoding",
    "tests",
    "python",
    "lint",
    "black",
    "semgrep",
    "bandit",
    "eslint",
    "prettier",
    "checkstyle",
    "pmd",
    "go-vet",
    "golangci-lint",
    "clippy",
    "rustfmt",
    "dotnet-format",
    "clang-tidy",
    "cppcheck",
    "htmlhint",
    "stylelint",
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
    parser.add_argument(
        "-H", "--help", action="help", help="show this help message and exit"
    )
    parser.add_argument(
        "-p",
        "--path",
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
        "--fast",
        action="store_true",
        help="Skip slow checks (coverage, mutation)",
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
        "-v",
        "--verbose",
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
        "--encoding", action="store_true", help="Run source-encoding check only"
    )
    parser.add_argument(
        "--tests", action="store_true", help="Run non-Python test suites"
    )
    parser.add_argument(
        "--python", action="store_true", help="Run Python syntax check only"
    )
    parser.add_argument("--lint", action="store_true", help="Run ruff lint only")
    parser.add_argument("--black", action="store_true", help="Run black format only")
    parser.add_argument(
        "--semgrep", action="store_true", help="Run semgrep security scan"
    )
    parser.add_argument(
        "--bandit", action="store_true", help="Run bandit security scan"
    )
    parser.add_argument("--eslint", action="store_true", help="Run eslint")
    parser.add_argument("--prettier", action="store_true", help="Run prettier")
    parser.add_argument("--checkstyle", action="store_true", help="Run checkstyle")
    parser.add_argument("--pmd", action="store_true", help="Run pmd")
    parser.add_argument("--go-vet", action="store_true", help="Run go-vet")
    parser.add_argument(
        "--golangci-lint", action="store_true", help="Run golangci-lint"
    )
    parser.add_argument("--clippy", action="store_true", help="Run clippy")
    parser.add_argument("--rustfmt", action="store_true", help="Run rustfmt")
    parser.add_argument(
        "--dotnet-format", action="store_true", help="Run dotnet-format"
    )
    parser.add_argument("--clang-tidy", action="store_true", help="Run clang-tidy")
    parser.add_argument("--cppcheck", action="store_true", help="Run cppcheck")
    parser.add_argument("--htmlhint", action="store_true", help="Run htmlhint")
    parser.add_argument("--stylelint", action="store_true", help="Run stylelint")
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
    # argparse maps --go-vet to args.go_vet; module identity keeps the hyphen
    # (it must match the INTEGRATIONS / LANGUAGE_LINTERS keys), so look up the
    # underscore form of the attribute.
    selected = {m for m in ALL_MODULES if getattr(args, m.replace("-", "_"), False)}
    if not selected and args.fix:
        return {"quality"}
    if selected:
        return selected
    if args.skip:
        import re

        _MODULE_SHORT = {
            "q": "quality",
            "w": "wiring",
            "p": "phd",
            "r": "runtime",
            "s": "suite",
            "l": "lint",
            "b": "black",
        }
        raw = {s.strip() for s in re.split(r"[, ]+", args.skip) if s.strip()}
        skip_set = {_MODULE_SHORT.get(x, x) for x in raw}
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
    print(
        "\N{RABBIT}  github.com/Yosef-Bunick/AUDIT-TEST — audit-test by Yosef Bunick  \N{RABBIT}"
    )
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
        fast=args.fast if hasattr(args, "fast") else False,
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
    print(
        "\N{RABBIT}  github.com/Yosef-Bunick/AUDIT-TEST — audit-test by Yosef Bunick  \N{RABBIT}"
    )
    target_root = find_target_root(args.path)
    return gate_main(
        target_root, fast=args.fast, no_static=args.no_static, kill_pct=args.kill
    )


def _force_utf8_output() -> None:
    force_utf8_streams()


# ── focus / ignore commands ──────────────────────────────────────────────────

_Grp = _nt("_Grp", "files path desc")  # audit: ok (named focus groups)
_IGNORE_PATH = Path.cwd() / ".audit-test-ignore"


def _ig_read() -> list[str]:
    try:
        if _IGNORE_PATH.exists():
            return _IGNORE_PATH.read_text(encoding="utf-8").splitlines()
        return []
    except OSError:
        return []


def _ig_write(lines: list[str]) -> None:
    _IGNORE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _confirm(msg: str, twice: bool = False) -> bool:
    ans = input(f"{msg} [y/n]: ").strip().lower()
    if ans != "y":
        print("cancelled")
        return False
    if twice:
        ans2 = input("confirm ALL [q/z]: ").strip().lower()
        if ans2 not in ("q", "z"):
            print("cancelled")
            return False
    return True


def _parse_groups(lines: list[str]) -> tuple[dict[str, _Grp], str]:
    groups: dict[str, _Grp] = {}
    default = ""
    inside = False
    for ln in lines:
        s = ln.strip()
        if s.lower() == "#only":
            inside = not inside
            continue
        if not inside:
            if s.lower().startswith("#path"):
                default = s.split(None, 1)[1].strip() if " " in s else ""
            continue
        if s.startswith("#") or not s:
            continue
        desc, path = "", default
        if "|" in s:
            s, desc = s.split("|", 1)
            desc = desc.strip()
        if "=[" in s:
            name, rest = s.split("=[", 1)
            # file list ends at the first ']'; the remainder is the path, kept
            # verbatim so a leading '/' or Windows 'C:' survives.
            if "]" not in rest:
                continue
            files_str, after = rest.split("]", 1)
            files = tuple(f.strip() for f in files_str.split(",") if f.strip())
            path = after.strip() or default
            groups[name.strip()] = _Grp(files, path, desc)
    return groups, default


def _rebuild_file(
    lines: list[str], groups: dict[str, _Grp], default_path: str
) -> list[str]:
    """Keep outside lines, replace #only block with groups."""
    out: list[str] = []
    inside = False
    for ln in lines:
        s = ln.strip().lower()
        if s == "#only":
            inside = not inside
            continue
        if not inside:
            out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    if groups:
        out.append("#only")
        for name, g in sorted(groups.items()):
            line = f"{name}=[{', '.join(g.files)}]"
            if g.path and g.path != default_path:
                # plain space separator; the parser takes everything after ']'
                # as the path, so the value round-trips exactly as written.
                line += f" {g.path}"
            if g.desc:
                line += f" | {g.desc}"
            out.append(line)
        out.append("#only")
    return out


def _focus_help() -> None:
    print("""focus — manage and run #only groups in .audit-test-ignore

  focus <group>             run audit on group
  focus <group> <flags>     run with audit flags (high, v, full, etc.)
  focus <group> /path       run group from specific path
  focus add <group> <files> append files to group
  focus del <group> <files> remove files from group
  focus path <group> [/p]   show/set group path
  focus desc <group> [text] show/set group description
  focus info [group]        list all groups or show one
  focus clear [group]       remove group(s)
  focus help                this help
""")


def _handle_focus() -> None:  # audit: ok (CLI entry point)
    idx = sys.argv.index("focus")
    args = sys.argv[idx + 1 :]
    act = args[0] if args else ""

    if not args or act == "info":
        groups, default = _parse_groups(_ig_read())
        target = args[1] if len(args) > 1 else ""
        if target:
            if target not in groups:
                print(f"'{target}' not found")
                sys.exit(2)
            g = groups[target]
            d = f"  | {g.desc}" if g.desc else ""
            print(f"{target}=[{', '.join(g.files)}] @ {g.path or default or 'cwd'}{d}")
        else:
            if not groups:
                print("no groups — use 'focus add <name> <files>'")
            for n, g in sorted(groups.items()):
                d = f"  | {g.desc}" if g.desc else ""
                print(f"  {n}=[{', '.join(g.files)}] @ {g.path or default or 'cwd'}{d}")
        sys.exit(0)

    if act == "help":
        _focus_help()
        sys.exit(0)

    if act == "clear":
        group = args[1] if len(args) > 1 else ""
        if group:
            if not _confirm(f"remove group '{group}'?"):
                sys.exit(0)
            lines = _ig_read()
            groups, _ = _parse_groups(lines)
            if group in groups:
                del groups[group]
            _ig_write(_rebuild_file(lines, groups, ""))
            print(f"removed '{group}'")
        else:
            if not _confirm("remove ALL groups?", twice=True):
                sys.exit(0)
            lines = _ig_read()
            out: list[str] = []
            inside = False
            for ln in lines:
                if ln.strip().lower() == "#only":
                    inside = not inside
                    continue
                if not inside:
                    out.append(ln)
            _ig_write(out)
            print("all groups removed")
        sys.exit(0)

    if act == "add":
        if len(args) < 3:
            print("usage: focus add <group> <file...>")
            sys.exit(2)
        _focus_edit("add", args[1], args[2:])
        sys.exit(0)

    if act == "del":
        if len(args) < 3:
            print("usage: focus del <group> <file...>")
            sys.exit(2)
        _focus_edit("del", args[1], args[2:])
        sys.exit(0)

    if act == "path":
        if len(args) < 2:
            print("usage: focus path <group> [/path]")
            sys.exit(2)
        _focus_set_path(args[1], args[2] if len(args) > 2 else "")
        sys.exit(0)

    if act == "desc":
        if len(args) < 2:
            print("usage: focus desc <group> [text]")
            sys.exit(2)
        _focus_set_desc(args[1], " ".join(args[2:]) if len(args) > 2 else "")
        sys.exit(0)

    _focus_run(args)


def _focus_edit(op: str, name: str, files: list[str]) -> None:
    lines = _ig_read()
    groups, default = _parse_groups(lines)
    if name not in groups:
        if op == "del":
            print(f"'{name}' not found")
            sys.exit(2)
        groups[name] = _Grp((), default, "")
    g = groups[name]
    cur = list(g.files)
    if op == "add":
        cur.extend(files)
    else:
        for f in files:
            if f in cur:
                cur.remove(f)
    groups[name] = _Grp(tuple(cur), g.path, g.desc)
    _ig_write(_rebuild_file(lines, groups, default))
    print(f"{name} = [{', '.join(cur)}]")


def _focus_set_path(name: str, new_path: str) -> None:
    lines = _ig_read()
    groups, default = _parse_groups(lines)
    if name not in groups:
        print(f"'{name}' not found")
        sys.exit(2)
    g = groups[name]
    if not new_path:
        print(f"path: {g.path or default or 'cwd'}")
        return
    groups[name] = _Grp(g.files, new_path, g.desc)
    _ig_write(_rebuild_file(lines, groups, default))
    print(f"set path for '{name}' to {new_path}")


def _focus_set_desc(name: str, desc: str) -> None:
    lines = _ig_read()
    groups, default = _parse_groups(lines)
    if name not in groups:
        print(f"'{name}' not found")
        sys.exit(2)
    g = groups[name]
    if not desc:
        print(f"desc: {g.desc or '(none)'}")
        return
    groups[name] = _Grp(g.files, g.path, desc)
    _ig_write(_rebuild_file(lines, groups, default))
    print(f"set desc for '{name}'")


def _focus_run(args: list[str]) -> None:
    if not args:
        print("usage: focus <group> [flags...]")
        sys.exit(2)
    name = args[0]
    lines = _ig_read()
    groups, default = _parse_groups(lines)
    if name not in groups:
        print(f"'{name}' not found. groups: {sorted(groups)}")
        sys.exit(2)
    g = groups[name]
    root = g.path or default or str(Path.cwd())
    rest = args[1:]
    filtered = []
    for a in rest:
        if a.startswith("/") or a.startswith("C:") or a.startswith("D:"):
            root = a
        else:
            filtered.append(a)
    os.environ["AUDIT_FOCUS_GROUP"] = name
    sys.argv = [sys.argv[0], "-p", root] + filtered
    _expand_bare_words()
    parser = build_audit_parser()
    ns = parser.parse_args()
    sys.exit(run_audit(ns))


def _handle_ignore() -> None:  # audit: ok (CLI entry point)
    idx = sys.argv.index("ignore")
    args = sys.argv[idx + 1 :]
    act = args[0] if args else "info"

    if act in ("help", "-H", "--help"):
        print("""ignore — manage skip patterns in .audit-test-ignore

  ignore add <pattern>     add a skip pattern
  ignore del <pattern>     remove a skip pattern
  ignore info              list all skip patterns
  ignore clear             remove ALL custom patterns
  ignore help              this help
""")
        sys.exit(0)

    lines = _ig_read()
    outside: list[str] = []
    only_block: list[str] = []
    in_only = False
    for ln in lines:
        if ln.strip().lower() == "#only":
            in_only = not in_only
            only_block.append(ln)
            continue
        (only_block if in_only else outside).append(ln)

    if act == "info":
        custom = [
            line
            for line in outside
            if line.strip() and not line.strip().startswith("#")
        ]
        if custom:
            print("skip patterns:")
            for p in custom:
                print(f"  {p}")
        else:
            print("no custom skip patterns")
        sys.exit(0)

    if act == "add":
        if len(args) < 2:
            print("usage: ignore add <pattern>")
            sys.exit(2)
        outside.append(args[1])
        _ig_write(outside + only_block)
        print(f"added: {args[1]}")
        sys.exit(0)

    if act == "del":
        if len(args) < 2:
            print("usage: ignore del <pattern>")
            sys.exit(2)
        if args[1] not in outside:
            print(f"not found: {args[1]}")
            sys.exit(2)
        outside.remove(args[1])
        _ig_write(outside + only_block)
        print(f"removed: {args[1]}")
        sys.exit(0)

    if act == "clear":
        if not _confirm("remove ALL custom ignore patterns?"):
            sys.exit(0)
        kept = [
            line for line in outside if line.strip().startswith("#") or not line.strip()
        ]
        _ig_write(kept + only_block)
        print("all patterns removed")
        sys.exit(0)

    print(f"unknown action: {act} — try 'ignore help'")
    sys.exit(2)


# ── mode detection helpers ──────────────────────────────────────────────────


def _is_focus_mode() -> bool:
    return "focus" in sys.argv


def _is_ignore_mode() -> bool:
    return "ignore" in sys.argv


def _is_check_mode() -> bool:
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a == "check"
    return False


def _is_fix_mode() -> bool:
    return "surgeon" in sys.argv


def _is_profile_mode() -> bool:
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a == "profile"
    return False


def _is_compare_mode() -> bool:
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a == "compare"
    return False


def _is_deadcode_mode() -> bool:
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a == "deadcode"
    return False


def _handle_fix() -> None:
    """Delegate to surgeon — surgical line-based file edits."""
    idx = sys.argv.index("surgeon")
    sys.argv = sys.argv[:idx] + sys.argv[idx + 1 :]  # strip 'surgeon'
    surgeon_main()


def _handle_check() -> None:
    """`check [encoding...] [--path DIR]` — verify every file decodes as encoding.

    Encoding may be several tokens ('GB 18030'); if omitted, the target's
    #encoding (or utf-8) is used.  Path-like args or --path pick the project.
    """
    idx = sys.argv.index("check")
    args = sys.argv[idx + 1 :]
    if args and args[0] in ("help", "-h", "--help"):
        print(
            "check — verify every text file decodes under an encoding\n\n"
            "  check                 use the project's #encoding (or utf-8)\n"
            "  check <encoding>      e.g. utf-8 | ascii | UTF-16 | gb18030\n"
            "  check <encoding> --path <dir>   check another project\n\n"
            "Configure a project's expected encoding with a line in "
            ".audit-test-ignore:\n  #encoding utf-8"
        )
        sys.exit(0)

    root: str | None = None
    enc_tokens: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--path", "-p"):
            root = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a.startswith("/") or a.startswith("C:") or a.startswith("D:"):
            root = a
            i += 1
            continue
        enc_tokens.append(a)
        i += 1

    encoding = " ".join(enc_tokens) if enc_tokens else None
    if encoding is not None:
        try:
            encoding = normalize_encoding(encoding)
        except LookupError:
            print(
                f"unknown encoding: {' '.join(enc_tokens)!r} — try utf-8, ascii, gb18030"
            )
            sys.exit(2)
    target = find_target_root(root)
    result = encoding_check.run(target, encoding)
    print(result.stdout)
    sys.exit(EXIT_FAIL if result.is_failure else EXIT_PASS)


# ── profile / compare commands ───────────────────────────────────────────────


def _split_path_json(args: list[str]) -> tuple[str | None, str, list[str]]:
    """Pull --path/-p, --json FILE, and path-like args out of *args*.

    Returns (root, json_file, leftover) so each handler can add its own flags.
    """
    root: str | None = None
    json_file = ""
    leftover: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--path", "-p"):
            root = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a == "--json":
            json_file = args[i + 1] if i + 1 < len(args) else ""
            i += 2
            continue
        if a.startswith(("/", "C:", "D:")):
            root = a
            i += 1
            continue
        leftover.append(a)
        i += 1
    return root, json_file, leftover


def _emit(data: dict, json_file: str, summary: str) -> None:
    """Write JSON to *json_file* if given, else print the human *summary*."""
    if json_file:
        Path(json_file).write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )
        print(f"wrote {json_file}")
    else:
        print(summary)


def _profile_summary(name: str, prof: dict) -> str:
    s, a, p = prof["structure"], prof["architecture"], prof["performance"]
    lines = [
        f"{name}",
        f"  files {s['file_count']}  loc {s['loc']}  "
        f"funcs {s['function_count']} (pub {s['public_function_count']})  "
        f"classes {s['class_count']}  loops {s['loop_count']}",
        f"  parse {prof['metrics']['parse_seconds']}s  "
        f"compute_ops {p['compute_ops']}  speed {p['estimated_speed']}  "
        f"{'monolith' if a['is_monolith'] else 'modular'}",
    ]
    if a["pipeline_stages"]:
        lines.append(f"  stages: {', '.join(a['pipeline_stages'])}")
    for group, hits in prof["capabilities"].items():
        if hits:
            lines.append(f"  {group}: {', '.join(hits)}")
    return "\n".join(lines)


def _handle_profile() -> None:  # audit: ok (CLI entry point)
    """`profile [--path DIR] [--json FILE]` — profile one project."""
    idx = sys.argv.index("profile")
    args = sys.argv[idx + 1 :]
    if args and args[0] in ("help", "-H", "--help"):
        print(
            "profile — single-pass project profile (structure, cost, "
            "capabilities)\n\n"
            "  profile                 profile the current directory\n"
            "  profile --path <dir>    profile another project\n"
            "  profile --json <file>   write the full profile as JSON\n\n"
            "Domain vocabulary is config-driven via [profile] in "
            "audit-code.toml; with no config only generic signals are reported."
        )
        sys.exit(0)
    root, json_file, _ = _split_path_json(args)
    target = find_target_root(root)
    cfg = load_project_config(target)
    prof = profile_project(target, config=cfg)
    _emit(prof, json_file, _profile_summary(target.name, prof))
    sys.exit(EXIT_PASS)


def _compare_table(results: dict[str, dict]) -> str:
    """Render a compact comparison table across profiled projects.

    Adds an ``AHIGH`` column (wiring+phd HIGH findings) when audit counts were
    collected via ``compare --audit``.
    """
    has_audit = any("audit" in prof for prof in results.values())
    audit_col = f" {'AHIGH':>6}" if has_audit else ""
    header = (
        f"{'project':<24} {'loc':>7} {'funcs':>6} {'cls':>4} "
        f"{'loops':>6} {'parse':>7} {'ops':>5} {'speed':>7}{audit_col}  arch"
    )
    rows = [header, "-" * len(header)]
    for name, prof in results.items():
        s, a, p = prof["structure"], prof["architecture"], prof["performance"]
        if has_audit:
            total = prof.get("audit", {}).get("total_high", "-")
            audit_cell = f" {total:>6}"
        else:
            audit_cell = ""
        rows.append(
            f"{name[:24]:<24} {s['loc']:>7} {s['function_count']:>6} "
            f"{s['class_count']:>4} {s['loop_count']:>6} "
            f"{prof['metrics']['parse_seconds']:>7} {p['compute_ops']:>5} "
            f"{p['estimated_speed']:>7}{audit_cell}  "
            f"{'monolith' if a['is_monolith'] else 'modular'}"
        )
    return "\n".join(rows)


def _handle_compare() -> None:  # audit: ok (CLI entry point)
    """`compare [--path ROOT] [--skip a,b] [--json FILE]` — profile subdirs."""
    idx = sys.argv.index("compare")
    args = sys.argv[idx + 1 :]
    if args and args[0] in ("help", "-H", "--help"):
        print(
            "compare — profile every subproject under a root for ranking\n\n"
            "  compare                 compare subdirs of the current directory\n"
            "  compare --path <root>   compare subdirs of another root\n"
            "  compare --skip a,b      ignore named subdirs\n"
            "  compare --audit         also run wiring+phd HIGH counts (slow)\n"
            "  compare --json <file>   write all profiles as JSON"
        )
        sys.exit(0)
    skip = ""
    include_audit = False
    filtered: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--skip":
            skip = args[i + 1] if i + 1 < len(args) else ""
            i += 2
            continue
        if args[i] == "--audit":
            include_audit = True
            i += 1
            continue
        filtered.append(args[i])
        i += 1
    root, json_file, _ = _split_path_json(filtered)
    target = find_target_root(root)
    cfg = load_project_config(target)
    results = compare_projects(target, skip, config=cfg, include_audit=include_audit)
    if not results:
        print(f"no subprojects found under {target}")
        sys.exit(EXIT_PASS)
    _emit(results, json_file, _compare_table(results))
    sys.exit(EXIT_PASS)


def _deadcode_summary(result: dict) -> str:
    """Human summary of classified dead symbols, critical bucket first."""
    lines = [f"dead code: {result['summary']}"]
    labels = [
        ("critical", "CRITICAL (missing features)"),
        ("utility", "utility (likely harmless)"),
        ("other", "other (verify manually)"),
    ]
    for key, label in labels:
        items = result.get(key, [])
        if not items:
            continue
        lines.append(f"  {label}:")
        for it in items:
            loc = f"{it.get('file')}:{it.get('line')}"
            lines.append(f"    {it['name']:32} {loc}")
    return "\n".join(lines)


def _handle_deadcode() -> None:  # audit: ok (CLI entry point)
    """`deadcode [--path DIR] [--json FILE]` — classify wiring's dead symbols."""
    idx = sys.argv.index("deadcode")
    args = sys.argv[idx + 1 :]
    if args and args[0] in ("help", "-H", "--help"):
        print(
            "deadcode — classify the wiring audit's dead symbols by impact\n\n"
            "  deadcode                 classify dead symbols in the cwd\n"
            "  deadcode --path <dir>    classify another project\n"
            "  deadcode --json <file>   write the classification as JSON\n\n"
            "Critical vs utility buckets are config-driven via [profile] "
            "pipeline_verbs / utility_markers in audit-code.toml."
        )
        sys.exit(0)
    root, json_file, _ = _split_path_json(args)
    target = find_target_root(root)
    cfg = load_project_config(target)
    from audit_code.wiring import collect_dead_symbols

    dead = collect_dead_symbols(target)
    result = classify_dead_symbols(target, dead, config=cfg)
    _emit(result, json_file, _deadcode_summary(result))
    sys.exit(EXIT_PASS)


def main():
    _force_utf8_output()
    if _is_gate_mode():
        gate_idx = next(i for i, a in enumerate(sys.argv) if a == "gate")
        sys.argv = sys.argv[:gate_idx] + sys.argv[gate_idx + 1 :]
        parser = build_gate_parser()
        args = parser.parse_args()
        sys.exit(run_gate_cmd(args))
    elif _is_check_mode():
        _handle_check()
    elif _is_profile_mode():
        _handle_profile()
    elif _is_compare_mode():
        _handle_compare()
    elif _is_deadcode_mode():
        _handle_deadcode()
    elif _is_fix_mode():
        _handle_fix()
    elif _is_focus_mode():
        _handle_focus()
    elif _is_ignore_mode():
        _handle_ignore()
    else:
        _expand_bare_words()
        parser = build_audit_parser()
        args = parser.parse_args()
        sys.exit(run_audit(args))


def _expand_bare_words() -> None:
    """Convert bare words like 'phd high fix' into '--phd --high --fix'."""
    WORD_MAP = {
        # modules
        "syntax": "--syntax",
        "python": "--python",
        "wiring": "--wiring",
        "phd": "--phd",
        "runtime": "--runtime",
        "suite": "--suite",
        "quality": "--quality",
        "encoding": "--encoding",
        "tests": "--tests",
        "lint": "--lint",
        "black": "--black",
        "semgrep": "--semgrep",
        "bandit": "--bandit",
        "eslint": "--eslint",
        "prettier": "--prettier",
        "checkstyle": "--checkstyle",
        "pmd": "--pmd",
        "go-vet": "--go-vet",
        "golangci-lint": "--golangci-lint",
        "clippy": "--clippy",
        "rustfmt": "--rustfmt",
        "dotnet-format": "--dotnet-format",
        "clang-tidy": "--clang-tidy",
        "cppcheck": "--cppcheck",
        "htmlhint": "--htmlhint",
        "stylelint": "--stylelint",
        # module shortcuts
        "q": "--quality",
        "w": "--wiring",
        "p": "--phd",
        "r": "--runtime",
        "l": "--lint",
        "b": "--black",
        "s": "--suite",
        # severity
        "high": "--high",  # h, -h
        "medium": "--medium",  # m, -m
        "info": "--info",  # i , -i
        "all": "--all",  # a , -a
        # modes
        "fix": "--fix",  # f -f
        "full": "--full",  # F , -F
        "min": "--min",
        "verbose": "--verbose",  # v , -v
        "strict": "--strict",
        "report": "--report-only",
        # single-letter shortcuts
        "f": "--fix",
        "h": "--high",
        "m": "--medium",
        "v": "--verbose",
        "F": "--full",
        "fast": "--fast",
    }
    new_argv = [sys.argv[0]]
    prev_was_value_flag = False
    value_flags = {
        "--path",
        "-p",
        "--skip",
        "-s",
        "--json",
        "--sarif",
        "--junit",
        "--profile",
        "--config",
    }
    for arg in sys.argv[1:]:
        if prev_was_value_flag:
            new_argv.append(arg)  # pass through — it's a value, not a flag
            prev_was_value_flag = False
        elif arg.startswith("-") or arg == "gate":
            new_argv.append(arg)
            prev_was_value_flag = arg in value_flags
        else:
            # exact-case first so case-sensitive keys win ('F'->--full),
            # then fall back to the lowercased form ('PHD'->--phd, 'f'->--fix)
            new_argv.append(WORD_MAP.get(arg, WORD_MAP.get(arg.lower(), arg)))
            prev_was_value_flag = False
    sys.argv = new_argv


if __name__ == "__main__":
    main()
