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


def _walk_ts(node):
    """Yield all nodes in a tree-sitter tree."""
    yield node
    for child in node.children:
        yield from _walk_ts(child)


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

        def _node_check(f):
            if budget.exhausted():
                return f, None
            rc, out, err = run_tool([node, "--check", str(f)], root, timeout=30)
            return f, (rc, out, err)

        # One subprocess per file is latency-bound, not CPU-bound — run a
        # handful concurrently. Results are consumed in submission order, so
        # findings stay deterministic.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(_node_check, plain[:MAX_PER_FILE_CHECKS]))
        for f, res in results:
            if res is None:
                notes.append(
                    f"time budget exhausted after {checked}/{len(plain)} JS files"
                )
                break
            rc, out, err = res
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
                # No tsc available — use tree-sitter for JSX syntax check
                ts_checked = cls._check_jsx_syntax(root, typed, findings, budget)
                notes.append(
                    f"{ts_checked}/{len(typed)} TS/JSX file(s) checked via tree-sitter"
                    if ts_checked
                    else f"{len(typed)} TS/JSX file(s) NOT checked "
                    "(needs tsc + tsconfig.json or tree-sitter)"
                )
        return cls.result(findings, notes)

    @classmethod
    def _check_jsx_syntax(cls, root: Path, files: list, findings: list, budget) -> int:
        """Fallback JSX/TS syntax check via tree-sitter when tsc isn't available.

        Uses the TypeScript grammars (tsx for .tsx/.jsx, typescript for .ts):
        the plain-JS grammar rejects valid code — TS type annotations, and even
        JSX attributes named `in` — producing false parse errors.
        """
        try:
            import tree_sitter as ts
            from tree_sitter import Language
        except ImportError:
            return 0
        try:
            import tree_sitter_typescript as tst

            tsx_parser = ts.Parser(Language(tst.language_tsx()))
            ts_parser = ts.Parser(Language(tst.language_typescript()))
        except Exception:
            # last resort: JS grammar (JSX-capable, but no TS annotations)
            try:
                import tree_sitter_javascript as tsjs

                tsx_parser = ts_parser = ts.Parser(Language(tsjs.language()))
            except Exception:
                return 0

        checked = 0
        for f in files[:MAX_PER_FILE_CHECKS]:
            if budget.exhausted():
                break
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "\x00" in text:
                # a NUL truncates tree-sitter's lexer mid-file, producing a
                # misleading parse error — report the real problem instead
                findings.append(
                    cls.finding(
                        "NUL byte in source — invisible in editors, breaks "
                        "many parsers",
                        file=rel(f, root),
                        line=text[: text.index("\x00")].count("\n") + 1,
                    )
                )
                checked += 1
                continue
            parser = ts_parser if f.suffix == ".ts" else tsx_parser
            tree = parser.parse(text.encode())
            if tree.root_node.has_error:
                # Find the first real error node
                for node in _walk_ts(tree.root_node):
                    if node.type == "ERROR" or node.is_missing:
                        if cls._is_bare_ampersand_error(node):
                            continue  # grammar limitation, not a syntax error
                        findings.append(
                            cls.finding(
                                f"parse error near '{text[max(0,node.start_byte-10):node.end_byte+10]}'",
                                file=rel(f, root),
                                line=node.start_point[0] + 1,
                            )
                        )
                        break
            checked += 1
        return checked

    @staticmethod
    def _is_bare_ampersand_error(node) -> bool:
        """True when an ERROR node is just a bare `&` in JSX text.

        tree-sitter's JSX grammars only accept `&` as the start of an HTML
        entity, so plain text like `Sync & Import` — valid to Babel/esbuild —
        parses as an ERROR whose children include a lone `&` (or `&&`) token.
        """
        return node.type == "ERROR" and any(
            c.type in ("&", "&&") for c in node.children
        )

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
