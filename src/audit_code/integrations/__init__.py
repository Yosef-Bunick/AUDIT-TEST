"""Integration dispatch — maps module names to integration run() functions."""

from audit_code.integrations import (
    bandit,
    checkstyle,
    clang_tidy,
    clippy,
    cppcheck,
    dotnet_format,
    eslint,
    go_vet,
    golangci_lint,
    htmlhint,
    pmd,
    prettier,
    rustfmt,
    semgrep,
    stylelint,
)

INTEGRATIONS = {
    "bandit": bandit,
    "checkstyle": checkstyle,
    "clang-tidy": clang_tidy,
    "clippy": clippy,
    "cppcheck": cppcheck,
    "dotnet-format": dotnet_format,
    "eslint": eslint,
    "go-vet": go_vet,
    "golangci-lint": golangci_lint,
    "htmlhint": htmlhint,
    "pmd": pmd,
    "prettier": prettier,
    "rustfmt": rustfmt,
    "semgrep": semgrep,
    "stylelint": stylelint,
}
