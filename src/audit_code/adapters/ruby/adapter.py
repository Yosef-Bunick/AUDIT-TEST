"""Ruby adapter — `ruby -c` syntax check."""

import re
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which

_ERR = re.compile(r"^(.+\.rb):(\d+):\s*(.+)$")


class RubyAdapter(LanguageAdapter):
    language = "ruby"
    extensions = (".rb",)
    marker_files = ("Gemfile",)
    tool_hint = "install Ruby from ruby-lang.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        ruby = which("ruby")
        if not ruby:
            return cls.skip("ruby not found", True)
        findings = []
        checked = 0
        for f in files[:200]:
            rc, out, err = run_tool([ruby, "-c", str(f)], root, timeout=10)
            if rc == 0:
                checked += 1
                continue
            if rc == -2:
                continue
            for ln in (out + "\n" + err).splitlines():
                m = _ERR.match(ln.strip())
                if m:
                    findings.append(
                        cls.finding(
                            m.group(3),
                            file=str(f.relative_to(root)),
                            line=int(m.group(2)),
                        )
                    )
            checked += 1
        return cls.result(findings, [f"{checked} Ruby file(s) checked via ruby -c"])

    @staticmethod
    def test_command(root: Path) -> list | None:
        ruby = which("ruby")
        if ruby and (root / "Rakefile").exists():
            return (
                [ruby, "-e", "require 'rake'; Rake.application.run"] if False else None
            )
        return None
