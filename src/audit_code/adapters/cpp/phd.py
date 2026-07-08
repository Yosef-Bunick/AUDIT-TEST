"""AST-based C/C++ PhD rules via tree-sitter — 9 rules.

Catches structural bugs: shell execution, unsafe C string functions, empty
catch blocks, goto, unsafe casts, using-namespace in headers, hardcoded
tuning constants, god functions, namespace-scope initializer calls.
"""

import re
from pathlib import Path

import tree_sitter as ts
import tree_sitter_cpp as tscpp
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

_CPP_LANG = Language(tscpp.language())
_PARSER = ts.Parser(_CPP_LANG)
_EXT = frozenset({".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"})
_HEADER_EXT = frozenset({".h", ".hpp", ".hh"})


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


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


def _call_name(node, src: bytes) -> str | None:
    """Name of a call_expression callee (identifier or template_function)."""
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "template_function":
        for c in fn.children:
            if c.type == "identifier":
                return src[c.start_byte : c.end_byte].decode()
    return src[fn.start_byte : fn.end_byte].decode()


def _finding(rule_id, severity, message, path, root, node):
    return Finding(
        rule_id=rule_id,
        severity=severity,
        message=message,
        file=rel(path, root),
        line=node.start_point[0] + 1,
        language="cpp",
        source="ast-phd",
    )


_SHELL_FNS = frozenset(
    {"system", "popen", "execl", "execlp", "execle", "execv", "execvp", "execvpe"}
)
_UNSAFE_STR_FNS = frozenset({"gets", "strcpy", "strcat", "sprintf", "vsprintf"})
_UNSAFE_CASTS = frozenset({"reinterpret_cast", "const_cast"})


def _check_shell_call(root, sources):
    """SEC-equivalent: shell execution — injection risk."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            name = _call_name(node, src)
            if name in _SHELL_FNS:
                findings.append(
                    _finding(
                        "cpp-ast-shell-call",
                        Severity.HIGH,
                        f"{name}() executes a shell command — injection risk",
                        path,
                        root,
                        node,
                    )
                )
    return findings


def _check_unsafe_string(root, sources):
    """B-equivalent: unbounded C string functions — buffer overflow."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            name = _call_name(node, src)
            if name in _UNSAFE_STR_FNS:
                findings.append(
                    _finding(
                        "cpp-ast-unsafe-string",
                        Severity.HIGH,
                        f"{name}() has no bounds check — buffer overflow risk",
                        path,
                        root,
                        node,
                    )
                )
    return findings


def _check_empty_catch(root, sources):
    """C-equivalent: catch block with no statements swallows the error."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "catch_clause":
                continue
            body = node.child_by_field_name("body")
            if body is None:
                continue
            stmts = [c for c in body.children if c.is_named and c.type != "comment"]
            if not stmts:
                findings.append(
                    _finding(
                        "cpp-ast-empty-catch",
                        Severity.HIGH,
                        "empty catch block silently swallows the error",
                        path,
                        root,
                        node,
                    )
                )
    return findings


def _check_goto(root, sources):
    """Goto obscures control flow (cleanup-label idiom included — review)."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type == "goto_statement":
                findings.append(
                    _finding(
                        "cpp-ast-goto",
                        Severity.MEDIUM,
                        "goto obscures control flow — prefer structured flow/RAII",
                        path,
                        root,
                        node,
                    )
                )
    return findings


def _check_unsafe_cast(root, sources):
    """reinterpret_cast/const_cast subvert the type system."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            name = _call_name(node, src)
            if name in _UNSAFE_CASTS:
                findings.append(
                    _finding(
                        "cpp-ast-unsafe-cast",
                        Severity.MEDIUM,
                        f"{name} subverts the type system — isolate and justify",
                        path,
                        root,
                        node,
                    )
                )
    return findings


def _check_using_namespace_header(root, sources):
    """`using namespace` in a header leaks into every includer."""
    findings = []
    for path, text in sources.items():
        if path.suffix not in _HEADER_EXT:
            continue
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "using_declaration":
                continue
            if any(c.type == "namespace" for c in node.children):
                findings.append(
                    _finding(
                        "cpp-ast-using-namespace-header",
                        Severity.MEDIUM,
                        "using namespace in a header pollutes every includer",
                        path,
                        root,
                        node,
                    )
                )
    return findings


_KNOB = re.compile(r"^(?:MAX_|MIN_|TIMEOUT|THRESHOLD|LIMIT|COOLDOWN|RETRY|BATCH|POOL)")


def _check_hardcoded_constant(root, sources):
    """G1-equivalent: #define / const values that look like tuning knobs."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            name = None
            if node.type == "preproc_def":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = src[name_node.start_byte : name_node.end_byte].decode()
            elif node.type == "init_declarator":
                decl = node.child_by_field_name("declarator")
                if decl is not None and decl.type == "identifier":
                    name = src[decl.start_byte : decl.end_byte].decode()
            if name and _KNOB.search(name):
                findings.append(
                    _finding(
                        "cpp-ast-hardcoded-const",
                        Severity.MEDIUM,
                        f"constant {name} — tuning knob, move to config",
                        path,
                        root,
                        node,
                    )
                )
    return findings


def _check_god_function(root, sources):
    """DG1-equivalent: functions over 120 lines."""
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "function_definition":
                continue
            span = node.end_point[0] - node.start_point[0] + 1
            if span > 120:
                decl = node.child_by_field_name("declarator")
                name = (
                    src[decl.start_byte : decl.end_byte].decode().split("(")[0]
                    if decl
                    else "?"
                )
                findings.append(
                    _finding(
                        "cpp-ast-god-func",
                        Severity.MEDIUM,
                        f"function {name}() is {span} lines — decompose",
                        path,
                        root,
                        node,
                    )
                )
    return findings


def _check_global_init_call(root, sources):
    """F3-equivalent: namespace-scope initializer calls a function — static
    initialization order across translation units is undefined."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        pm = _build_parent_map(tree.root_node)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            cur = _parent_of(node, pm)
            inside_function = False
            for _ in range(30):
                if cur is None:
                    break
                if cur.type in ("function_definition", "lambda_expression"):
                    inside_function = True
                    break
                cur = _parent_of(cur, pm)
            if inside_function:
                continue
            name = _call_name(node, src)
            if name and name not in _UNSAFE_CASTS and "cast" not in name:
                findings.append(
                    _finding(
                        "cpp-ast-global-init-call",
                        Severity.MEDIUM,
                        f"namespace-scope call to {name}() — static init order fiasco",
                        path,
                        root,
                        node,
                    )
                )
    return findings


_RULES = [
    _check_shell_call,
    _check_unsafe_string,
    _check_empty_catch,
    _check_goto,
    _check_unsafe_cast,
    _check_using_namespace_header,
    _check_hardcoded_constant,
    _check_god_function,
    _check_global_init_call,
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
