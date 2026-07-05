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
