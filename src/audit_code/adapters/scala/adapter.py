"""Scala adapter — `scalac` syntax check."""

from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which


class ScalaAdapter(LanguageAdapter):
    language = "scala"
    extensions = (".scala",)
    marker_files = ("build.sbt",)
    tool_hint = "install Scala from scala-lang.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        scalac = which("scalac")
        if not scalac:
            return cls.skip("scalac not found", True)
        paths = [str(f) for f in files[:200]]
        rc, out, err = run_tool(
            [scalac, "-Ystop-after:parser", "-d", "/tmp/sc_out"] + paths,
            root,
            timeout=120,
        )
        if rc == -2:
            return cls.skip(f"scalac failed: {err}", True)
        findings = []
        for ln in (out + "\n" + err).splitlines():
            ln = ln.strip()
            if ln and "error" in ln.lower():
                findings.append(cls.finding(ln[:200]))
        return cls.result(findings, [f"{len(files)} Scala file(s) checked via scalac"])

    @staticmethod
    def test_command(root: Path) -> list | None:
        sbt = which("sbt")
        if sbt and (root / "build.sbt").exists():
            return [sbt, "test"]
        return None
