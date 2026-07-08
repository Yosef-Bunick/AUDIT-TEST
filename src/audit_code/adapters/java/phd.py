"""AST-based Java PhD rules via tree-sitter — 10 rules."""

import re
from pathlib import Path

import tree_sitter as ts
import tree_sitter_java as tsjava
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

_JAVA_LANG = Language(tsjava.language())
_PARSER = ts.Parser(_JAVA_LANG)
_EXT = frozenset({".java"})


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _call_chain(node, src: bytes) -> str:
    obj = node.child_by_field_name("object")
    name = node.child_by_field_name("name")
    if obj is None or name is None:
        return ""
    return f"{src[obj.start_byte:obj.end_byte].decode()}.{src[name.start_byte:name.end_byte].decode()}"


def _build_parent_map(root_node):
    pm = {}

    def w(n):
        for c in n.children:
            pm[(c.start_byte, c.end_byte)] = n
            w(c)

    w(root_node)
    return pm


def _parent_of(node, pm):
    return pm.get((node.start_byte, node.end_byte))


def _node_text(node, src):
    return src[node.start_byte : node.end_byte].decode()


# ── existing ──────────────────────────────────────────────────────────────


def _check_thread_exit(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "method_invocation":
                continue
            chain = _call_chain(node, src)
            if chain == "Thread.sleep":
                findings.append(
                    Finding(
                        rule_id="java-ast-thread-sleep",
                        severity=Severity.MEDIUM,
                        message="Thread.sleep() — blocks thread pool",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="java",
                        source="ast-phd",
                    )
                )
            elif chain == "System.exit":
                findings.append(
                    Finding(
                        rule_id="java-ast-system-exit",
                        severity=Severity.MEDIUM,
                        message="System.exit() — hard shutdown",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="java",
                        source="ast-phd",
                    )
                )
    return findings


def _check_runtime_exec(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "method_invocation":
                continue
            name_node = node.child_by_field_name("name")
            if name_node and _node_text(name_node, src) == "exec":
                findings.append(
                    Finding(
                        rule_id="java-ast-runtime-exec",
                        severity=Severity.HIGH,
                        message="Runtime.exec() — shell injection risk",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="java",
                        source="ast-phd",
                    )
                )
    return findings


# ── new structural rules ──────────────────────────────────────────────────


def _check_empty_catch(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "catch_clause":
                continue
            body = node.child_by_field_name("body")
            if body and not [c for c in body.children if c.is_named]:
                findings.append(
                    Finding(
                        rule_id="java-ast-empty-catch",
                        severity=Severity.HIGH,
                        message="empty catch block silently swallows exception",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="java",
                        source="ast-phd",
                    )
                )
    return findings


def _check_system_gc(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "method_invocation":
                continue
            chain = _call_chain(node, src)
            if chain in ("System.gc", "System.runFinalization"):
                findings.append(
                    Finding(
                        rule_id="java-ast-system-gc",
                        severity=Severity.MEDIUM,
                        message=f"{chain}() — explicit GC, let JVM manage memory",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="java",
                        source="ast-phd",
                    )
                )
    return findings


def _check_hardcoded_constant(root, sources):
    KNOB = re.compile(
        r"^(?:MAX_|MIN_|TIMEOUT|THRESHOLD|LIMIT|COOLDOWN|RETRY|BATCH|POOL|DEFAULT_)"
    )
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "field_declaration":
                continue
            for child in node.children:
                if child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        name = _node_text(name_node, src)
                        if KNOB.search(name) and "static" in _node_text(node, src):
                            findings.append(
                                Finding(
                                    rule_id="java-ast-hardcoded-const",
                                    severity=Severity.MEDIUM,
                                    message=f"static {name} — tuning knob, move to config/properties",
                                    file=rel(path, root),
                                    line=node.start_point[0] + 1,
                                    language="java",
                                    source="ast-phd",
                                )
                            )
    return findings


def _check_god_method(root, sources):
    findings = []
    for path, text in sources.items():
        tree = _PARSER.parse(text.encode())
        src = text.encode()
        for node in _walk(tree.root_node):
            if node.type not in ("method_declaration", "constructor_declaration"):
                continue
            span = node.end_point[0] - node.start_point[0] + 1
            if span > 80:
                name_node = node.child_by_field_name("name")
                name = _node_text(name_node, src) if name_node else "<init>"
                findings.append(
                    Finding(
                        rule_id="java-ast-god-method",
                        severity=Severity.MEDIUM,
                        message=f"method {name}() is {span} lines — decompose",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="java",
                        source="ast-phd",
                    )
                )
    return findings


def _check_resource_leak(root, sources):
    """Flag FileInputStream/Connection without try-with-resources or close()."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "local_variable_declaration":
                continue
            txt = _node_text(node, src)
            for res in (
                "FileInputStream",
                "FileOutputStream",
                "BufferedReader",
                "Connection",
                "Statement",
                "ResultSet",
            ):
                if res in txt and "try" not in txt:
                    findings.append(
                        Finding(
                            rule_id="java-ast-resource-leak",
                            severity=Severity.HIGH,
                            message=f"possible resource leak: {res} without try-with-resources",
                            file=rel(path, root),
                            line=node.start_point[0] + 1,
                            language="java",
                            source="ast-phd",
                        )
                    )
                    break
    return findings


def _check_print_stack_trace(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "method_invocation":
                continue
            chain = _call_chain(node, src)
            if chain.endswith(".printStackTrace"):
                findings.append(
                    Finding(
                        rule_id="java-ast-print-stacktrace",
                        severity=Severity.MEDIUM,
                        message="printStackTrace() — use proper logging instead",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="java",
                        source="ast-phd",
                    )
                )
    return findings


def _check_synchronized_this(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "synchronized_statement":
                continue
            txt = _node_text(node, src)
            if "synchronized(this)" in txt or "synchronized (this)" in txt:
                findings.append(
                    Finding(
                        rule_id="java-ast-sync-this",
                        severity=Severity.MEDIUM,
                        message="synchronized(this) — use private lock object instead",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="java",
                        source="ast-phd",
                    )
                )
    return findings


_RULES = [
    _check_thread_exit,
    _check_runtime_exec,
    _check_empty_catch,
    _check_system_gc,
    _check_hardcoded_constant,
    _check_god_method,
    _check_resource_leak,
    _check_print_stack_trace,
    _check_synchronized_this,
]


def run(root: Path, files: list[Path]) -> list[Finding]:
    sources = {}
    for f in files:
        if f.suffix in _EXT:
            try:
                sources[f] = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    findings = []
    for rule in _RULES:
        findings.extend(rule(root, sources))
    return findings
