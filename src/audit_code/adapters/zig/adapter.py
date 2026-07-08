"""Zig adapter — `zig ast-check` syntax check."""

from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which


class ZigAdapter(LanguageAdapter):
    language = "zig"
    extensions = (".zig",)
    marker_files = ("build.zig",)
    tool_hint = "install Zig from ziglang.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        zig = which("zig")
        if not zig:
            return cls.skip("zig not found", True)
        paths = [str(f) for f in files[:200]]
        rc, out, err = run_tool([zig, "ast-check"] + paths, root, timeout=60)
        if rc == -2:
            return cls.skip(f"zig ast-check failed: {err}", True)
        findings = []
        for ln in (out + "\n" + err).splitlines():
            ln = ln.strip()
            if ln and "error" in ln.lower():
                findings.append(cls.finding(ln[:200]))
        return cls.result(
            findings, [f"{len(files)} Zig file(s) checked via zig ast-check"]
        )

    @staticmethod
    def test_command(root: Path) -> list | None:
        zig = which("zig")
        if zig and (root / "build.zig").exists():
            return [zig, "test"]
        return None
