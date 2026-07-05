"""clang-tidy integration — C/C++ linter.

clang-tidy needs a compilation database (compile_commands.json) and explicit
source files; `clang-tidy -p .` with no sources just prints usage and exits
non-zero. When no database is present we SKIP honestly rather than emit a fake
crash — generate one with `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`.
"""

from pathlib import Path

from audit_code.models import AuditResult, AuditStatus

from ._tool_runner import _run_tool

_SRC_GLOBS = ("*.c", "*.cc", "*.cpp", "*.cxx")
_MAX_FILES = 200  # cap the argv so a huge tree does not overflow the command line


def run(target_root, timeout=300):
    root = Path(target_root)
    db_dir = next(
        (
            cand.parent
            for cand in (
                root / "compile_commands.json",
                root / "build" / "compile_commands.json",
            )
            if cand.exists()
        ),
        None,
    )
    if db_dir is None:
        return AuditResult(
            audit_id="clang-tidy",
            status=AuditStatus.SKIP,
            stdout=(
                "SKIP: no compile_commands.json found "
                "(generate with cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON)"
            ),
        )

    sources = [str(p) for g in _SRC_GLOBS for p in root.rglob(g)]
    if not sources:
        return AuditResult(
            audit_id="clang-tidy",
            status=AuditStatus.SKIP,
            stdout="SKIP: no C/C++ source files found",
        )

    return _run_tool(
        "clang-tidy",
        ["clang-tidy", "-p", str(db_dir), *sources[:_MAX_FILES]],
        "clang-tidy",
        target_root,
        timeout,
        violation_codes=None,
    )
