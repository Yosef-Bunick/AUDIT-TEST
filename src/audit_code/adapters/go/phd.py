"""AST-based Go PhD rules via tree-sitter — 10 rules."""

import re
from pathlib import Path

import tree_sitter as ts
import tree_sitter_go as tsgo
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

_GO_LANG = Language(tsgo.language())
_PARSER = ts.Parser(_GO_LANG)
_EXT = frozenset({".go"})


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _call_name(node, src: bytes) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    return src[fn.start_byte : fn.end_byte].decode()


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


# ── existing rules ────────────────────────────────────────────────────────


def _check_defer_loop(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        pm = _build_parent_map(tree.root_node)
        for node in _walk(tree.root_node):
            if node.type != "defer_statement":
                continue
            cur = _parent_of(node, pm)
            for _ in range(20):
                if cur is None:
                    break
                if cur.type == "for_statement":
                    findings.append(
                        Finding(
                            rule_id="go-ast-defer-loop",
                            severity=Severity.MEDIUM,
                            message="defer in loop — resources accumulate",
                            file=rel(path, root),
                            line=node.start_point[0] + 1,
                            language="go",
                            source="ast-phd",
                        )
                    )
                    break
                cur = _parent_of(cur, pm)
    return findings


def _check_http_no_ctx(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            name = _call_name(node, src)
            if name in ("Get", "Post") and "http" in text:
                args = node.child_by_field_name("arguments")
                if (
                    args
                    and "context"
                    not in src[args.start_byte : args.end_byte].decode().lower()
                ):
                    findings.append(
                        Finding(
                            rule_id="go-ast-http-noctx",
                            severity=Severity.MEDIUM,
                            message=f"http.{name}() without context — request can hang forever",
                            file=rel(path, root),
                            line=node.start_point[0] + 1,
                            language="go",
                            source="ast-phd",
                        )
                    )
    return findings


def _check_goroutine_norecover(root, sources):
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "go_statement":
                continue
            body = src[node.start_byte : node.end_byte].decode()
            if "recover" not in body and "defer" not in body:
                findings.append(
                    Finding(
                        rule_id="go-ast-goroutine-norecover",
                        severity=Severity.MEDIUM,
                        message="go func() without defer/recover — uncaught panic kills process",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="go",
                        source="ast-phd",
                    )
                )
    return findings


# ── new structural rules ──────────────────────────────────────────────────


def _check_discarded_error(root, sources):
    """C2-equivalent: error variables assigned but never checked."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        pm = _build_parent_map(tree.root_node)
        for node in _walk(tree.root_node):
            if node.type != "assignment_statement":
                continue
            txt = src[node.start_byte : node.end_byte].decode()
            if ", err :=" in txt or ", err =" in txt or "err :=" in txt:
                # Check if err is checked in following statements
                parent = _parent_of(node, pm)
                if parent and parent.type == "block":
                    after = src[parent.start_byte : parent.end_byte].decode()
                    err_pos = after.find(txt)
                    rest = after[err_pos + len(txt) :]
                    if "err !=" not in rest and "if err" not in rest:
                        findings.append(
                            Finding(
                                rule_id="go-ast-discarded-err",
                                severity=Severity.HIGH,
                                message="error assigned but never checked — silent failure",
                                file=rel(path, root),
                                line=node.start_point[0] + 1,
                                language="go",
                                source="ast-phd",
                            )
                        )
    return findings


def _check_hardcoded_constant(root, sources):
    """G1-equivalent: const values that look like tuning knobs."""
    KNOB = re.compile(
        r"^(?:MAX_|MIN_|TIMEOUT|THRESHOLD|LIMIT|COOLDOWN|RETRY|BATCH|POOL)"
    )
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "const_declaration":
                continue
            for child in node.children:
                if child.type == "const_spec":
                    name_node = child.child_by_field_name("name")
                    if name_node and KNOB.search(
                        src[name_node.start_byte : name_node.end_byte].decode()
                    ):
                        findings.append(
                            Finding(
                                rule_id="go-ast-hardcoded-const",
                                severity=Severity.MEDIUM,
                                message=f"const {src[name_node.start_byte:name_node.end_byte].decode()} — tuning knob, move to config",
                                file=rel(path, root),
                                line=node.start_point[0] + 1,
                                language="go",
                                source="ast-phd",
                            )
                        )
    return findings


def _check_god_function(root, sources):
    """DG1-equivalent: functions over 120 lines."""
    findings: list[Finding] = []
    for path, text in sources.items():
        tree = _PARSER.parse(text.encode())
        src = text.encode()
        for node in _walk(tree.root_node):
            if node.type != "function_declaration":
                continue
            span = node.end_point[0] - node.start_point[0] + 1
            if span > 120:
                name_node = node.child_by_field_name("name")
                name = (
                    src[name_node.start_byte : name_node.end_byte].decode()
                    if name_node
                    else "?"
                )
                findings.append(
                    Finding(
                        rule_id="go-ast-god-func",
                        severity=Severity.MEDIUM,
                        message=f"func {name}() is {span} lines — decompose",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="go",
                        source="ast-phd",
                    )
                )
    return findings


def _check_naked_return(root, sources):
    """Flag naked returns in non-trivial functions (confusing control flow)."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "return_statement":
                continue
            children = node.children
            if len(children) == 1 and children[0].type == "return":
                findings.append(
                    Finding(
                        rule_id="go-ast-naked-return",
                        severity=Severity.MEDIUM,
                        message="naked return — confusing in named-return functions, use explicit values",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="go",
                        source="ast-phd",
                    )
                )
    return findings


def _check_empty_if_err(root, sources):
    """C8-equivalent: if err != nil { } with empty body."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "if_statement":
                continue
            txt = src[node.start_byte : node.end_byte].decode()
            if "err != nil" in txt or "err == nil" in txt:
                body = node.child_by_field_name("consequence")
                if body and not [c for c in body.children if c.is_named]:
                    findings.append(
                        Finding(
                            rule_id="go-ast-empty-errcheck",
                            severity=Severity.HIGH,
                            message="empty if err != nil block — silently discards error",
                            file=rel(path, root),
                            line=node.start_point[0] + 1,
                            language="go",
                            source="ast-phd",
                        )
                    )
    return findings


def _check_module_side_effect(root, sources):
    """F3-equivalent: function calls at package level (init without init func)."""
    findings = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        pm = _build_parent_map(tree.root_node)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            cur = _parent_of(node, pm)
            is_module_level = True
            for _ in range(15):
                if cur is None:
                    break
                if cur.type in ("function_declaration", "method_declaration"):
                    is_module_level = False
                    break
                cur = _parent_of(cur, pm)
            if not is_module_level:
                continue
            name = _call_name(node, src)
            if name and name not in (
                "make",
                "new",
                "len",
                "cap",
                "append",
                "copy",
                "delete",
                "close",
                "panic",
                "recover",
                "print",
                "println",
            ):
                findings.append(
                    Finding(
                        rule_id="go-ast-module-side-effect",
                        severity=Severity.MEDIUM,
                        message=f"package-level call to {name}() — side effect at import, wrap in init()",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="go",
                        source="ast-phd",
                    )
                )
    return findings


_RULES = [
    _check_defer_loop,
    _check_http_no_ctx,
    _check_goroutine_norecover,
    _check_discarded_error,
    _check_hardcoded_constant,
    _check_god_function,
    _check_naked_return,
    _check_empty_if_err,
    _check_module_side_effect,
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
