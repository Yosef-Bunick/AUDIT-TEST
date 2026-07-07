"""Elixir adapter — `elixirc` syntax check."""

from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which


class ElixirAdapter(LanguageAdapter):
    language = "elixir"
    extensions = (".ex", ".exs")
    marker_files = ("mix.exs",)
    tool_hint = "install Elixir from elixir-lang.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        elixirc = which("elixirc")
        if not elixirc:
            return cls.skip("elixirc not found", True)
        paths = [str(f) for f in files[:200]]
        rc, out, err = run_tool(
            [elixirc, "--no-docs", "-o", "/tmp/ex_out"] + paths, root, timeout=120
        )
        if rc == -2:
            return cls.skip(f"elixirc failed: {err}", True)
        findings = []
        for ln in (out + "\n" + err).splitlines():
            ln = ln.strip()
            if ln and ("error" in ln.lower() or "warning" in ln.lower()):
                findings.append(cls.finding(ln[:200]))
        return cls.result(
            findings, [f"{len(files)} Elixir file(s) checked via elixirc"]
        )

    @staticmethod
    def test_command(root: Path) -> list | None:
        mix = which("mix")
        if mix and (root / "mix.exs").exists():
            return [mix, "test"]
        return None
