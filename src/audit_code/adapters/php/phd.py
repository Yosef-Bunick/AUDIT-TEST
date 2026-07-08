"""AST-based PHP PhD rules via tree-sitter — 9 rules."""

import re

import tree_sitter as ts
import tree_sitter_php as tsphp
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

_PHP_LANG = Language(tsphp.language_php())
_PARSER = ts.Parser(_PHP_LANG)
_EXT = frozenset({".php", ".phtml"})


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _nt(n, s):
    return s[n.start_byte : n.end_byte].decode()


def _pmap(rn):
    pm = {}

    def w(n):
        for c in n.children:
            pm[(c.start_byte, c.end_byte)] = n
            w(c)

    w(rn)
    return pm


def _pof(n, pm):
    return pm.get((n.start_byte, n.end_byte))


# ── rules ──


def _check_dynamic_exec(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "function_call_expression":
                continue
            fn = n.child_by_field_name("function")
            if fn and _nt(fn, s) in ("eval", "assert", "create_function"):
                fnd.append(
                    Finding(
                        rule_id="php-ast-dynamic-exec",
                        severity=Severity.HIGH,
                        message=f"{_nt(fn,s)}() — dynamic code execution",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="php",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_shell_exec(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "function_call_expression":
                continue
            fn = n.child_by_field_name("function")
            if fn and _nt(fn, s) in (
                "shell_exec",
                "exec",
                "system",
                "passthru",
                "popen",
                "proc_open",
            ):
                fnd.append(
                    Finding(
                        rule_id="php-ast-shell-exec",
                        severity=Severity.HIGH,
                        message=f"{_nt(fn,s)}() — shell injection risk",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="php",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_empty_catch(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "catch_clause":
                continue
            b = n.child_by_field_name("body")
            if b and not [c for c in b.children if c.is_named]:
                fnd.append(
                    Finding(
                        rule_id="php-ast-empty-catch",
                        severity=Severity.HIGH,
                        message="empty catch block",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="php",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_error_suppression(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type == "error_suppression_expression":
                fnd.append(
                    Finding(
                        rule_id="php-ast-error-suppress",
                        severity=Severity.MEDIUM,
                        message="@ error suppression — silently discards errors",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="php",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_hardcoded_secret(root, sources):
    K = re.compile(r"(?i)(api[_-]?key|secret|passwd|password|token|access[_-]?key)")
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "assignment_expression":
                continue
            txt = _nt(n, s)
            if K.search(txt) and not any(
                x in txt.lower() for x in ("env", "getenv", "$_")
            ):
                fnd.append(
                    Finding(
                        rule_id="php-ast-hardcoded-secret",
                        severity=Severity.HIGH,
                        message="possible hardcoded secret — use env vars",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="php",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_god_func(root, sources):
    fnd = []
    for p, t in sources.items():
        tr = _PARSER.parse(t.encode())
        s = t.encode()
        for n in _walk(tr.root_node):
            if n.type != "function_definition":
                continue
            sp = n.end_point[0] - n.start_point[0] + 1
            if sp > 80:
                nm = n.child_by_field_name("name")
                name = _nt(nm, s) if nm else "?"
                fnd.append(
                    Finding(
                        rule_id="php-ast-god-func",
                        severity=Severity.MEDIUM,
                        message=f"function {name}() is {sp} lines",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="php",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_extract(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type == "function_call_expression":
                fn = n.child_by_field_name("function")
                if fn and _nt(fn, s) == "extract":
                    fnd.append(
                        Finding(
                            rule_id="php-ast-extract",
                            severity=Severity.HIGH,
                            message="extract() — variable injection, use explicit array access",
                            file=rel(p, root),
                            line=n.start_point[0] + 1,
                            language="php",
                            source="ast-phd",
                        )
                    )
    return fnd


def _check_include_var(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type not in ("include_expression", "require_expression"):
                continue
            txt = _nt(n, s)
            if "$" in txt:
                fnd.append(
                    Finding(
                        rule_id="php-ast-include-var",
                        severity=Severity.HIGH,
                        message="include/require with variable — LFI risk",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="php",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_module_side_effect(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        pm = _pmap(tr.root_node)
        for n in _walk(tr.root_node):
            if n.type != "function_call_expression":
                continue
            cur = _pof(n, pm)
            is_mod = True
            for _ in range(15):
                if cur is None:
                    break
                if cur.type in (
                    "function_definition",
                    "method_declaration",
                    "class_declaration",
                ):
                    is_mod = False
                    break
                cur = _pof(cur, pm)
            if not is_mod:
                continue
            fn = n.child_by_field_name("function")
            if fn:
                name = _nt(fn, s)
                if name not in (
                    "define",
                    "ini_set",
                    "error_reporting",
                    "date_default_timezone_set",
                ):
                    fnd.append(
                        Finding(
                            rule_id="php-ast-module-side-effect",
                            severity=Severity.MEDIUM,
                            message=f"top-level call to {name}() — side effect",
                            file=rel(p, root),
                            line=n.start_point[0] + 1,
                            language="php",
                            source="ast-phd",
                        )
                    )
    return fnd


_RULES = [
    _check_dynamic_exec,
    _check_shell_exec,
    _check_empty_catch,
    _check_error_suppression,
    _check_hardcoded_secret,
    _check_god_func,
    _check_extract,
    _check_include_var,
    _check_module_side_effect,
]


def run(root, files):
    sources = {}
    for f in files:
        if f.suffix in _EXT:
            try:
                sources[f] = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    fnd = []
    for r in _RULES:
        fnd.extend(r(root, sources))
    return fnd
