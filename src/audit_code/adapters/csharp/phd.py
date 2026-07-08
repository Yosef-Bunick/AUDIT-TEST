"""AST-based C# PhD rules via tree-sitter — 10 rules."""

import re

import tree_sitter as ts
import tree_sitter_c_sharp as tscs
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

_CS_LANG = Language(tscs.language())
_PARSER = ts.Parser(_CS_LANG)
_EXT = frozenset({".cs"})


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _chain(node, src):
    obj = node.child_by_field_name("expression") or node.child_by_field_name("object")
    name = node.child_by_field_name("name")
    if obj is None or name is None:
        return ""
    return f"{src[obj.start_byte:obj.end_byte].decode()}.{src[name.start_byte:name.end_byte].decode()}"


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


# ── existing ──


def _check_sleep_exit_exec(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type not in ("invocation_expression", "member_access_expression"):
                continue
            c = _chain(n, s)
            if c == "Thread.Sleep":
                fnd.append(
                    Finding(
                        rule_id="cs-ast-thread-sleep",
                        severity=Severity.MEDIUM,
                        message="Thread.Sleep() blocks thread pool",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
                        source="ast-phd",
                    )
                )
            elif c == "Environment.Exit":
                fnd.append(
                    Finding(
                        rule_id="cs-ast-env-exit",
                        severity=Severity.MEDIUM,
                        message="Environment.Exit() hard shutdown",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
                        source="ast-phd",
                    )
                )
            elif c == "Process.Start":
                fnd.append(
                    Finding(
                        rule_id="cs-ast-process-start",
                        severity=Severity.HIGH,
                        message="Process.Start() shell injection risk",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
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
                        rule_id="cs-ast-empty-catch",
                        severity=Severity.HIGH,
                        message="empty catch block",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
                        source="ast-phd",
                    )
                )
    return fnd


# ── new rules ──


def _check_hardcoded_const(root, sources):
    K = re.compile(
        r"^(?:MAX_|MIN_|TIMEOUT|THRESHOLD|LIMIT|COOLDOWN|RETRY|BATCH|POOL|DEFAULT_)"
    )
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "field_declaration":
                continue
            txt = _nt(n, s)
            if "const" in txt or "static readonly" in txt:
                for c in n.children:
                    if c.type == "variable_declarator":
                        nm = c.child_by_field_name("name")
                        if nm and K.search(_nt(nm, s)):
                            fnd.append(
                                Finding(
                                    rule_id="cs-ast-hardcoded-const",
                                    severity=Severity.MEDIUM,
                                    message=f"const {_nt(nm,s)} — tuning knob, move to config",
                                    file=rel(p, root),
                                    line=n.start_point[0] + 1,
                                    language="csharp",
                                    source="ast-phd",
                                )
                            )
    return fnd


def _check_god_method(root, sources):
    fnd = []
    for p, t in sources.items():
        tr = _PARSER.parse(t.encode())
        s = t.encode()
        for n in _walk(tr.root_node):
            if n.type not in ("method_declaration", "constructor_declaration"):
                continue
            sp = n.end_point[0] - n.start_point[0] + 1
            if sp > 80:
                nm = n.child_by_field_name("name")
                name = _nt(nm, s) if nm else "<ctor>"
                fnd.append(
                    Finding(
                        rule_id="cs-ast-god-method",
                        severity=Severity.MEDIUM,
                        message=f"method {name}() is {sp} lines",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_gc_collect(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type not in ("invocation_expression", "member_access_expression"):
                continue
            c = _chain(n, s)
            if c in ("GC.Collect", "GC.WaitForPendingFinalizers"):
                fnd.append(
                    Finding(
                        rule_id="cs-ast-gc-collect",
                        severity=Severity.MEDIUM,
                        message=f"{c}() — explicit GC, let runtime manage",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_lock_this(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type != "lock_statement":
                continue
            txt = _nt(n, s)
            if "lock(this)" in txt or "lock (this)" in txt:
                fnd.append(
                    Finding(
                        rule_id="cs-ast-lock-this",
                        severity=Severity.MEDIUM,
                        message="lock(this) — use private lock object",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_empty_using(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)

        for n in _walk(tr.root_node):
            if n.type != "using_statement":
                continue
            b = n.child_by_field_name("body")
            if b and not [c for c in b.children if c.is_named]:
                fnd.append(
                    Finding(
                        rule_id="cs-ast-empty-using",
                        severity=Severity.MEDIUM,
                        message="empty using block",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
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
            if n.type not in ("invocation_expression",):
                continue
            cur = _pof(n, pm)
            is_mod = True
            for _ in range(15):
                if cur is None:
                    break
                if cur.type in (
                    "method_declaration",
                    "constructor_declaration",
                    "property_declaration",
                ):
                    is_mod = False
                    break
                cur = _pof(cur, pm)
            if not is_mod:
                continue
            fn = n.child_by_field_name("function")
            if fn:
                fnd.append(
                    Finding(
                        rule_id="cs-ast-module-side-effect",
                        severity=Severity.MEDIUM,
                        message=f"module-level call to {_nt(fn,s)}() — side effect at import",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
                        source="ast-phd",
                    )
                )
    return fnd


def _check_console_write(root, sources):
    fnd = []
    for p, t in sources.items():
        s = t.encode()
        tr = _PARSER.parse(s)
        for n in _walk(tr.root_node):
            if n.type not in ("invocation_expression",):
                continue
            c = _chain(n, s)
            if c in ("Console.Write", "Console.WriteLine"):
                fnd.append(
                    Finding(
                        rule_id="cs-ast-console-write",
                        severity=Severity.INFO,
                        message=f"{c}() — debug leftover, use proper logging",
                        file=rel(p, root),
                        line=n.start_point[0] + 1,
                        language="csharp",
                        source="ast-phd",
                    )
                )
    return fnd


_RULES = [
    _check_sleep_exit_exec,
    _check_empty_catch,
    _check_hardcoded_const,
    _check_god_method,
    _check_gc_collect,
    _check_lock_this,
    _check_empty_using,
    _check_module_side_effect,
    _check_console_write,
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
