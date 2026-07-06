"""CLI wiring test — confirms every flag/word is recognized by the parser.
Instant — no subprocess, no audit execution."""

import sys
from pathlib import Path

import pytest

# Add src to path so we can import the CLI module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audit_code.cli import _expand_bare_words, build_audit_parser


def _recognized(args: str) -> bool:
    """Return True if ALL args are recognized by the parser."""
    argv = ["audit-test"] + args.split()
    sys_argv = sys.argv
    try:
        sys.argv = argv
        _expand_bare_words()
        expanded = sys.argv[1:]
        parser = build_audit_parser()
        parsed, unknown = parser.parse_known_args(expanded)
        return len(unknown) == 0
    finally:
        sys.argv = sys_argv


# ── All forms — bare-word and dash-flag ─────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        # modules - bare
        "phd",
        "wiring",
        "runtime",
        "suite",
        "quality",
        "syntax",
        "python",
        "lint",
        "black",
        # modules - dash
        "--phd",
        "--wiring",
        "--runtime",
        "--suite",
        "--quality",
        "--syntax",
        "--python",
        "--lint",
        "--black",
        # integrations - bare
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
        # integrations - dash
        "--semgrep",
        "--bandit",
        "--eslint",
        "--prettier",
        "--checkstyle",
        "--pmd",
        "--go-vet",
        "--golangci-lint",
        "--clippy",
        "--rustfmt",
        "--dotnet-format",
        "--clang-tidy",
        "--cppcheck",
        "--htmlhint",
        "--stylelint",
        # severity - bare
        "high",
        "medium",
        "info",
        "all",
        "h",
        "m",
        # severity - dash
        "--high",
        "--medium",
        "--info",
        "--all",
        "-h",
        "-m",
        # modes - bare
        "fix",
        "full",
        "fast",
        "verbose",
        "min",
        "f",
        "F",
        "v",
        # modes - dash
        "--fix",
        "--full",
        "--fast",
        "--verbose",
        "--min",
        "-f",
        "-F",
        "-v",
        # shortcuts
        "p",
        "w",
        "r",
        "s",
        "q",
        "l",
        "b",
        # new
        "d",
        "deps",
        "--deps",
        "encoding",
        "--encoding",
        "tests",
        "--tests",
        "strict",
        "--strict",
        "report",
        "--report-only",
        # combos
        "q v",
        "phd high",
        "p h v",
        "quality fix",
        "q fast",
        "s v",
        "--skip quality",
        "--skip q",
        "-s q",
        "--phd --high -v",
        "--quality --verbose --fast",
    ],
)
def test_recognized(args: str):
    assert _recognized(args), f"not recognized: {args}"


# ── Negative tests — garbage should NOT be recognized ───────────────────────


@pytest.mark.parametrize(
    "args",
    [
        "hsif",
        "xyz",
        "nope",
        "--nope",
    ],
)
def test_unrecognized(args: str):
    assert not _recognized(args), f"should NOT be recognized: {args}"


# ── Auto-discover: every WORD_MAP entry must be recognized ───────────────────


def _extract_word_map():
    """Parse WORD_MAP dict from cli.py source — keeps test in sync with code."""
    import re

    src = (
        Path(__file__).resolve().parent.parent / "src" / "audit_code" / "cli.py"
    ).read_text()
    in_block = False
    entries = {}
    for line in src.splitlines():
        if "WORD_MAP = {" in line:
            in_block = True
            continue
        if in_block and "}" in line:
            break
        if in_block:
            m = re.match(r'\s*"(\S+)":\s*"--(\S+)"', line)
            if m:
                entries[m.group(1)] = m.group(2)
    return entries


def test_word_map_complete():
    """Every WORD_MAP entry must be recognized by the parser."""
    word_map = _extract_word_map()
    assert word_map, "failed to parse WORD_MAP"
    for bare, flag in sorted(word_map.items()):
        assert _recognized(bare), f"WORD_MAP '{bare}' -> --{flag} not recognized"
        assert _recognized(f"--{flag}"), f"dash flag --{flag} not recognized"


def test_skip_shortcuts():
    """Every _MODULE_SHORT entry must be a valid --skip target."""
    import re

    src = (
        Path(__file__).resolve().parent.parent / "src" / "audit_code" / "cli.py"
    ).read_text()
    in_block = False
    ok = 0
    for line in src.splitlines():
        if "_MODULE_SHORT = {" in line:
            in_block = True
            continue
        if in_block and "}" in line:
            break
        if in_block:
            m = re.match(r'\s*"(\S+)":\s*"(\S+)"', line)
            if m:
                assert _recognized(
                    f"--skip {m.group(1)}"
                ), f"--skip {m.group(1)} not recognized"
                ok += 1
    assert ok >= 8, f"expected >=8 _MODULE_SHORT entries, got {ok}"


# ── Severity resolution + mutual exclusion ───────────────────────────────────


def test_severity_resolution():
    """_resolve_severity maps flags to correct severity strings."""
    from audit_code.cli import _resolve_severity

    parser = build_audit_parser()

    cases = [
        ("--high", "HIGH"),
        ("--medium", "MEDIUM"),
        ("--info", None),
        ("--all", None),
        ("", "HIGH"),  # default
    ]
    for flag, expected in cases:
        argv = [flag] if flag else []
        args, _ = parser.parse_known_args(argv)
        assert (
            _resolve_severity(args) == expected
        ), f"{flag or 'default'} -> {_resolve_severity(args)}"


def test_mutually_exclusive_severity():
    """--high --medium must be rejected by argparse."""
    parser = build_audit_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--high", "--medium"])


def test_verbose_with_flags():
    """-v/--verbose combines with severity flags."""
    parser = build_audit_parser()
    args, _ = parser.parse_known_args(["--verbose"])
    assert args.verbose
    args, _ = parser.parse_known_args(["-v", "--high"])
    assert args.verbose and args.high
