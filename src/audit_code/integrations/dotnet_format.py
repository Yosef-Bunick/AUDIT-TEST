"""dotnet format integration — C# formatter (format drift only)."""

from audit_code.models import Severity

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # --verify-no-changes exits 2 when files need formatting. Cosmetic → MEDIUM.
    return _run_tool(
        "dotnet",
        ["dotnet", "format", "--verify-no-changes"],
        "dotnet-format",
        target_root,
        timeout,
        severity=Severity.MEDIUM,
        violation_codes={2},
    )
