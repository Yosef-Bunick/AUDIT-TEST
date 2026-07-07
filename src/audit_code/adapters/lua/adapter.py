"""Lua adapter — `luac -p` syntax check."""

from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which


class LuaAdapter(LanguageAdapter):
    language = "lua"
    extensions = (".lua",)
    marker_files = ("*.rockspec",)
    tool_hint = "install Lua from lua.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        luac = which("luac") or which("luajit")
        if not luac:
            return cls.skip("luac/luajit not found", True)
        findings = []
        checked = 0
        for f in files[:200]:
            rc, out, err = run_tool([luac, "-p", str(f)], root, timeout=10)
            if rc == 0:
                checked += 1
                continue
            if rc == -2:
                continue
            checked += 1
            for ln in (out + "\n" + err).splitlines():
                ln = ln.strip()
                if ln:
                    findings.append(cls.finding(ln, file=str(f.relative_to(root))))
        return cls.result(findings, [f"{checked} Lua file(s) checked via luac -p"])

    @staticmethod
    def test_command(root: Path) -> list | None:
        return None
