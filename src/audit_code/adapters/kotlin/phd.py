"""AST-based Kotlin PhD rules via tree-sitter — 9 rules."""

import re

import tree_sitter as ts
import tree_sitter_kotlin as tskt
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

_KT_LANG = Language(tskt.language())
_PARSER = ts.Parser(_KT_LANG)
_EXT = frozenset({".kt", ".kts"})


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


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


def _nt(n, s):
    return s[n.start_byte : n.end_byte].decode()


# ── rules ──


def _check_notnull(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type == "postfix_unary_expression":
                op = n.child_by_field_name("operator")
                if op and "!!" in _nt(op, s):
                    fnd.append(
                        Finding(
                            rule_id="kt-ast-notnull",
                            severity=Severity.HIGH,
                            message="!! not-null assertion — throws NPE",
                            file=rel(p, root),
                            line=n.start_point[0] + 1,
                            language="kotlin",
                            source="ast-phd",
                        )
                    )
            elif n.type == "!!":
                fnd.append(
                    Finding(
                        rule_id="kt-ast-notnull",
                        severity=Severity.HIGH,
                        message="!! not-null assertion",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="kotlin",
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
            if n.type != "catch_block":
                continue
            b = n.child_by_field_name("body")
            if b and not [c for c in b.children if c.is_named]:
                fnd.append(
                    Finding(
                        rule_id="kt-ast-empty-catch",
                        severity=Severity.HIGH,
                        message="empty catch block",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="kotlin",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_println(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type == "simple_identifier" and _nt(n, s) == "println":
                fnd.append(
                    Finding(
                        rule_id="kt-ast-println",
                        severity=Severity.INFO,
                        message="println left in source — use proper logging",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="kotlin",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_hardcoded_const(root, sources):
    K = re.compile(
        r"^(?:MAX_|MIN_|TIMEOUT|THRESHOLD|LIMIT|COOLDOWN|RETRY|BATCH|POOL|DEFAULT_)"
    )
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "property_declaration":
                continue
            nm = n.child_by_field_name("name")
            if nm and K.search(_nt(nm, s)) and "val" in _nt(n, s)[:10]:
                fnd.append(
                    Finding(
                        rule_id="kt-ast-hardcoded-const",
                        severity=Severity.MEDIUM,
                        message=f"val {_nt(nm,s)} — tuning knob, move to config",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="kotlin",
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
            if n.type != "function_declaration":
                continue
            sp = n.end_point[0] - n.start_point[0] + 1
            if sp > 80:
                nm = n.child_by_field_name("name")
                name = _nt(nm, s) if nm else "?"
                fnd.append(
                    Finding(
                        rule_id="kt-ast-god-func",
                        severity=Severity.MEDIUM,
                        message=f"fun {name}() is {sp} lines",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="kotlin",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_system_exit(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "call_expression":
                continue
            txt = _nt(n, s)
            if "System.exit" in txt or "exitProcess" in txt:
                fnd.append(
                    Finding(
                        rule_id="kt-ast-system-exit",
                        severity=Severity.MEDIUM,
                        message="System.exit() — hard shutdown",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="kotlin",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_lateinit(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "property_declaration":
                continue
            txt = _nt(n, s)
            if "lateinit" in txt and "var" in txt:
                nm = n.child_by_field_name("name")
                if nm:
                    fnd.append(
                        Finding(
                            rule_id="kt-ast-lateinit",
                            severity=Severity.MEDIUM,
                            message=f"lateinit var {_nt(nm,s)} — NPE risk if accessed before init",
                            file=rel(p, root),
                            line=n.start_point[0] + 1,
                            language="kotlin",
                            source="ast-phd",
                        )
                    )
    return fnd


def _check_todo(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type == "call_expression" and "TODO" in _nt(n, s):
                fnd.append(
                    Finding(
                        rule_id="kt-ast-todo",
                        severity=Severity.MEDIUM,
                        message="TODO() left in source — unimplemented, throws NotImplementedError",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="kotlin",
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
            if n.type != "call_expression":
                continue
            cur = _pof(n, pm)
            is_mod = True
            for _ in range(15):
                if cur is None:
                    break
                if cur.type in ("function_declaration", "class_declaration"):
                    is_mod = False
                    break
                cur = _pof(cur, pm)
            if not is_mod:
                continue
            fn = n.child_by_field_name("expression")
            if fn:
                fnd.append(
                    Finding(
                        rule_id="kt-ast-module-side-effect",
                        severity=Severity.MEDIUM,
                        message="top-level call — side effect at import",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="kotlin",
                        source="ast-phd",
                    )
                )
    return fnd


_RULES = [
    _check_notnull,
    _check_empty_catch,
    _check_println,
    _check_hardcoded_const,
    _check_god_func,
    _check_system_exit,
    _check_lateinit,
    _check_todo,
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
