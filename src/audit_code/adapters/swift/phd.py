"""AST-based Swift PhD rules via tree-sitter — 9 rules."""

import re

import tree_sitter as ts
import tree_sitter_swift as tssw
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

_SW_LANG = Language(tssw.language())
_PARSER = ts.Parser(_SW_LANG)
_EXT = frozenset({".swift"})


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


def _check_force_unwrap(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type == "force_try_expression":
                fnd.append(
                    Finding(
                        rule_id="sw-ast-force-try",
                        severity=Severity.HIGH,
                        message="try! force unwrap — crashes on error",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="swift",
                        source="ast-phd",
                    )
                )
            elif n.type == "force_cast_expression":
                fnd.append(
                    Finding(
                        rule_id="sw-ast-force-cast",
                        severity=Severity.HIGH,
                        message="as! force cast — crashes on type mismatch",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="swift",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_fatal_error(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type == "simple_identifier" and _nt(n, s) == "fatalError":
                fnd.append(
                    Finding(
                        rule_id="sw-ast-fatalerror",
                        severity=Severity.HIGH,
                        message="fatalError() — crashes process",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="swift",
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
                        rule_id="sw-ast-empty-catch",
                        severity=Severity.HIGH,
                        message="empty catch block",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="swift",
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
            if n.type not in ("property_declaration", "let_declaration"):
                continue
            txt = _nt(n, s)
            if "let " in txt or "static let" in txt:
                for c in n.children:
                    if c.type == "simple_identifier" and K.search(_nt(c, s)):
                        fnd.append(
                            Finding(
                                rule_id="sw-ast-hardcoded-const",
                                severity=Severity.MEDIUM,
                                message=f"let {_nt(c,s)} — tuning knob, move to config",
                                file=rel(p, root),
                                line=n.start_point[0] + 1,
                                language="swift",
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
                        rule_id="sw-ast-god-func",
                        severity=Severity.MEDIUM,
                        message=f"func {name}() is {sp} lines",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="swift",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_force_cast_any(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "force_cast_expression":
                continue
            fnd.append(
                Finding(
                    rule_id="sw-ast-force-cast",
                    severity=Severity.HIGH,
                    message="force cast — use if let / guard let instead",
                    file=rel(p, root),
                    line=n.start_point[0] + 1,
                    language="swift",
                    source="ast-phd",
                )
            )
    return fnd


def _check_implicit_return(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "return_statement":
                continue
            if len([c for c in n.children if c.is_named]) == 0:
                fnd.append(
                    Finding(
                        rule_id="sw-ast-implicit-return",
                        severity=Severity.INFO,
                        message="implicit return — explicit is clearer",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="swift",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_nslog(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type == "call_expression" and "NSLog" in _nt(n, s):
                fnd.append(
                    Finding(
                        rule_id="sw-ast-nslog",
                        severity=Severity.INFO,
                        message="NSLog() — debug leftover, use os_log or proper logging",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="swift",
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
            fn = n.child_by_field_name("function")
            if fn:
                fnd.append(
                    Finding(
                        rule_id="sw-ast-module-side-effect",
                        severity=Severity.MEDIUM,
                        message=f"top-level call to {_nt(fn,s)}() — side effect at import",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="swift",
                        source="ast-phd",
                    )
                )
    return fnd


_RULES = [
    _check_force_unwrap,
    _check_fatal_error,
    _check_empty_catch,
    _check_hardcoded_const,
    _check_god_func,
    _check_force_cast_any,
    _check_implicit_return,
    _check_nslog,
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
