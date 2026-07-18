"""PHP adapter — `php -l` lint check."""

import re
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which

_ERR = re.compile(
    r"(?:Parse|Fatal) error:\s*(.+?)\s+in\s+(.+?)\s+on line\s+(\d+)", re.IGNORECASE
)


class PhpAdapter(LanguageAdapter):
    language = "php"
    extensions = (".php", ".phtml")
    marker_files = ("composer.json",)
    tool_hint = "install PHP from php.net"

    @classmethod
    def check_files(cls, root: Path, files: list):
        php = which("php")
        if not php:
            return cls.skip("php not found", True)
        findings = []
        checked = 0
        for f in files[:200]:
            rc, out, err = run_tool([php, "-l", str(f)], root, timeout=10)
            if rc == 0:
                checked += 1
                continue
            if rc == -2:
                continue
            for ln in (out + "\n" + err).splitlines():
                m = _ERR.search(ln)
                if m:
                    findings.append(
                        cls.finding(
                            m.group(1),
                            file=str(f.relative_to(root)),
                            line=int(m.group(3)),
                        )
                    )
            checked += 1
        return cls.result(findings, [f"{checked} PHP file(s) linted via php -l"])

    @staticmethod
    def test_command(root: Path) -> list | None:
        php = which("php")
        if php and (root / "phpunit.xml").exists():
            return [php, "vendor/bin/phpunit"]
        return None
