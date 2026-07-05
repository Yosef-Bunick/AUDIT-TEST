"""go vet integration — Go suspicious-construct checker."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # go vet exits non-zero on any reported problem or build error.
    return _run_tool(
        "go",
        ["go", "vet", "./..."],
        "go-vet",
        target_root,
        timeout,
        violation_codes=None,
    )
