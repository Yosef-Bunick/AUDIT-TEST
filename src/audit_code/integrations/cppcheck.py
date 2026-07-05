"""cppcheck integration — C/C++ static analysis."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # cppcheck exits 0 even WITH findings by default; --error-exitcode=1 makes
    # a detected issue return 1 so it is not silently swallowed.
    return _run_tool(
        "cppcheck",
        ["cppcheck", ".", "--enable=all", "--quiet", "--error-exitcode=1"],
        "cppcheck",
        target_root,
        timeout,
        violation_codes={1},
    )
