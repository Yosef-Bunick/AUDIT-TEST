"""JavaScript/TypeScript adapter.

Plain JS (.js/.mjs/.cjs) is syntax-checked with `node --check` (a real parse).
TS/TSX/JSX need a compiler: if tsc + tsconfig.json are present we run
`tsc --noEmit` and judge only TS1xxx diagnostics (the grammar/syntax range);
type errors (TS2xxx+) are counted but not judged here. Without node the check
is SKIPPED — never faked as a pass.
"""

import json
import re
from pathlib import Path

from audit_code.adapters.base import (
    MAX_PER_FILE_CHECKS,
    LanguageAdapter,
    TimeBudget,
    rel,
    run_tool,
    which,
)

_PLAIN = (".js", ".mjs", ".cjs")
_TYPED = (".ts", ".tsx", ".jsx")
# tsc output: path(line,col): error TS1005: ';' expected.
_TSC_ERR = re.compile(r"^(.*?)\((\d+),\d+\): error (TS1\d{3}): (.*)$")
_TSC_ANY = re.compile(r": error TS\d+:")


class JavaScriptAdapter(LanguageAdapter):
    """Language adapter for JavaScript/TypeScript projects."""

    language = "javascript"
    extensions = _PLAIN + _TYPED
    marker_files = ("package.json",)
    tool_hint = "install Node.js from nodejs.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        node = which("node")
        if not node:
            return cls.skip("Node.js not found — cannot check syntax", True)

        plain = [f for f in files if f.suffix in _PLAIN]
        typed = [f for f in files if f.suffix in _TYPED]
        findings, notes = [], []

        budget = TimeBudget()
        checked = 0
        for f in plain[:MAX_PER_FILE_CHECKS]:
            if budget.exhausted():
                notes.append(
                    f"time budget exhausted after {checked}/{len(plain)} JS files"
                )
                break
            rc, out, err = run_tool([node, "--check", str(f)], root, timeout=30)
            checked += 1
            if rc != 0:
                err_lines = (err or out).strip().splitlines()
                msg = next(
                    (ln for ln in err_lines if "Error" in ln),
                    err_lines[0] if err_lines else "syntax error",
                )
                m = re.search(r":(\d+)\s*$", err_lines[0]) if err_lines else None
                findings.append(
                    cls.finding(
                        msg.strip(),
                        file=rel(f, root),
                        line=int(m.group(1)) if m else None,
                    )
                )
        notes.insert(0, f"{checked}/{len(plain)} JS file(s) checked via node --check")
        if len(plain) > MAX_PER_FILE_CHECKS:
            notes.append(f"capped at {MAX_PER_FILE_CHECKS} files")

        if typed:
            tsc = cls._find_tsc(root)
            if tsc and (root / "tsconfig.json").exists():
                rc, out, err = run_tool(tsc + ["--noEmit", "--pretty", "false"], root)
                text = out + "\n" + err
                syntax_errs = 0
                for ln in text.splitlines():
                    m = _TSC_ERR.match(ln.strip())
                    if m:
                        syntax_errs += 1
                        findings.append(
                            cls.finding(
                                f"{m.group(3)}: {m.group(4)}",
                                file=m.group(1),
                                line=int(m.group(2)),
                            )
                        )
                total_errs = len(_TSC_ANY.findall(text))
                notes.append(
                    f"tsc --noEmit on {len(typed)} TS/JSX file(s): "
                    f"{syntax_errs} syntax error(s); "
                    f"{total_errs - syntax_errs} type error(s) not judged here"
                )
            else:
                notes.append(
                    f"{len(typed)} TS/JSX file(s) NOT checked "
                    "(needs tsc + tsconfig.json)"
                )
        return cls.result(findings, notes)

    @staticmethod
    def _find_tsc(root: Path) -> list | None:
        for name in ("tsc.cmd", "tsc"):
            local = root / "node_modules" / ".bin" / name
            if local.exists():
                return [str(local)]
        tsc = which("tsc")
        return [tsc] if tsc else None

    @staticmethod
    def test_command(target_root: Path) -> list | None:
        pkg = target_root / "package.json"
        if not pkg.exists():
            return None
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        script = (data.get("scripts") or {}).get("test", "")
        if not script or "no test specified" in script:
            return None
        npm = which("npm")
        return [npm, "test", "--silent"] if npm else None
