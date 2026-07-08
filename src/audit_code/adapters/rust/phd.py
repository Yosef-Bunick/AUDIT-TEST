"""AST-based Rust PhD rules via tree-sitter.

Catches structural bugs: unsafe blocks, shell injection, panics, bare unwraps,
empty error handlers, fs operations without error handling, god functions,
hardcoded constants, inline imports, module-level side effects.
"""

from pathlib import Path

import tree_sitter as ts
import tree_sitter_rust as tsrust
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

_RUST_LANG = Language(tsrust.language())
_PARSER = ts.Parser(_RUST_LANG)
_EXT = frozenset({".rs"})


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _call_name(node, src: bytes) -> str | None:
    """Return the function name of a call_expression, or None."""
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    return src[fn.start_byte : fn.end_byte].decode()


def _node_text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode()


# ── existing rules ────────────────────────────────────────────────────────


def _check_unsafe(root: Path, sources: dict[Path, str]) -> list[Finding]:
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type == "unsafe_block":
                findings.append(
                    Finding(
                        rule_id="rs-ast-unsafe",
                        severity=Severity.HIGH,
                        message="unsafe block — bypasses Rust's memory safety guarantees",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="rust",
                        source="ast-phd",
                    )
                )
    return findings


def _check_command(root: Path, sources: dict[Path, str]) -> list[Finding]:
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            name = _call_name(node, src)
            if name and (name == "Command" or name.startswith("Command::")):
                findings.append(
                    Finding(
                        rule_id="rs-ast-command",
                        severity=Severity.HIGH,
                        message="std::process::Command — shell injection risk, validate input",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="rust",
                        source="ast-phd",
                    )
                )
    return findings


def _check_panic_lib(root: Path, sources: dict[Path, str]) -> list[Finding]:
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "macro_invocation":
                continue
            macro = node.child_by_field_name("macro")
            if macro is None:
                continue
            name = src[macro.start_byte : macro.end_byte].decode()
            if name in ("panic", "todo", "unimplemented"):
                findings.append(
                    Finding(
                        rule_id="rs-ast-panic",
                        severity=Severity.MEDIUM,
                        message=f"{name}!() — aborts the process, prefer Result/Option",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="rust",
                        source="ast-phd",
                    )
                )
    return findings


# ── new structural rules (Python PhD equivalents) ─────────────────────────


def _check_bare_unwrap(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """C1-equivalent: .unwrap() / .expect() — panics on error, like bare except."""
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type == "field_expression":
                field = node.child_by_field_name("field")
                if field is None:
                    continue
                name = src[field.start_byte : field.end_byte].decode()
                if name == "unwrap":
                    findings.append(
                        Finding(
                            rule_id="rs-ast-unwrap",
                            severity=Severity.MEDIUM,
                            message=".unwrap() — panics on error, use ? or match instead",
                            file=rel(path, root),
                            line=node.start_point[0] + 1,
                            language="rust",
                            source="ast-phd",
                        )
                    )
                elif name == "expect":
                    findings.append(
                        Finding(
                            rule_id="rs-ast-expect",
                            severity=Severity.INFO,
                            message=".expect() — panics on error with a message; review if recoverable",
                            file=rel(path, root),
                            line=node.start_point[0] + 1,
                            language="rust",
                            source="ast-phd",
                        )
                    )
    return findings


def _check_empty_error_handler(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """C8-equivalent: empty match arms / if-let error handlers that discard errors."""
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "match_arm":
                continue
            # Check for Err(_) => {} or Err(_) => () patterns
            body = next(
                (c for c in node.children if c.type == "block" or c.type == "unit"),
                None,
            )
            if body is None:
                continue
            if body.type == "unit":
                findings.append(
                    Finding(
                        rule_id="rs-ast-empty-err",
                        severity=Severity.MEDIUM,
                        message="empty error handler — silently discards Err variant",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="rust",
                        source="ast-phd",
                    )
                )
            elif body.type == "block":
                named = [c for c in body.children if c.is_named]
                if len(named) <= 1:  # empty block or single trivial expression
                    findings.append(
                        Finding(
                            rule_id="rs-ast-empty-err",
                            severity=Severity.MEDIUM,
                            message="near-empty error handler — at minimum log the error",
                            file=rel(path, root),
                            line=node.start_point[0] + 1,
                            language="rust",
                            source="ast-phd",
                        )
                    )
    return findings


def _check_fs_remove(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """C7-equivalent: std::fs::remove_dir_all / remove_file without ? or match."""
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        parents = _build_parent_map(tree.root_node)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            name = _call_name(node, src)
            if name not in ("remove_dir_all", "remove_file") and not (
                name
                and (
                    name.endswith("::remove_dir_all") or name.endswith("::remove_file")
                )
            ):
                continue
            parent = _parent_of(node, parents)
            if parent and parent.type == "expression_statement":
                findings.append(
                    Finding(
                        rule_id="rs-ast-fs-remove",
                        severity=Severity.HIGH,
                        message=f"std::fs::{name}() result discarded — "
                        "permission errors silently ignored, use ? or .context()",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="rust",
                        source="ast-phd",
                    )
                )
    return findings


def _check_hardcoded_constant(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """G1-equivalent: const values that look like tuning knobs (MAX_*, TIMEOUT, etc.)."""
    import re

    KNOB_RE = re.compile(
        r"^(?:MAX_|MIN_|TIMEOUT|THRESHOLD|LIMIT|COOLDOWN|RETRY|BATCH|POOL)"
    )
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "const_item":
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = src[name_node.start_byte : name_node.end_byte].decode()
            if KNOB_RE.search(name):
                findings.append(
                    Finding(
                        rule_id="rs-ast-hardcoded-const",
                        severity=Severity.MEDIUM,
                        message=f"const {name} — tuning knob hardcoded, move to config",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="rust",
                        source="ast-phd",
                    )
                )
    return findings


def _check_inline_use(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """P1-equivalent: `use` statements inside function bodies."""
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        parents = _build_parent_map(tree.root_node)
        for node in _walk(tree.root_node):
            if node.type != "use_declaration":
                continue
            cur = _parent_of(node, parents)
            for _ in range(10):
                if cur is None:
                    break
                if cur.type in ("function_item", "closure_expression"):
                    findings.append(
                        Finding(
                            rule_id="rs-ast-inline-use",
                            severity=Severity.INFO,
                            message="use inside function — hoist to module level",
                            file=rel(path, root),
                            line=node.start_point[0] + 1,
                            language="rust",
                            source="ast-phd",
                        )
                    )
                    break
                cur = _parent_of(cur, parents)
    return findings


def _check_god_function(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """DG1-equivalent: functions over 120 lines."""
    findings: list[Finding] = []
    for path, text in sources.items():
        tree = _PARSER.parse(text.encode())
        for node in _walk(tree.root_node):
            if node.type != "function_item":
                continue
            span = node.end_point[0] - node.start_point[0] + 1
            if span > 120:
                name_node = node.child_by_field_name("name")
                name = name_node and _node_text(name_node, text.encode()) or "?"
                findings.append(
                    Finding(
                        rule_id="rs-ast-god-func",
                        severity=Severity.MEDIUM,
                        message=f"fn {name}() is {span} lines — decompose into smaller functions",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="rust",
                        source="ast-phd",
                    )
                )
    return findings


def _check_module_side_effect(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """F3-equivalent: module-level function calls (side effects at import time)."""
    findings: list[Finding] = []
    for path, text in sources.items():
        src = text.encode()
        tree = _PARSER.parse(src)
        parents = _build_parent_map(tree.root_node)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            cur = _parent_of(node, parents)
            is_module_level = True
            for _ in range(15):
                if cur is None:
                    break
                if cur.type in ("function_item", "closure_expression", "impl_item"):
                    is_module_level = False
                    break
                cur = _parent_of(cur, parents)
            if not is_module_level:
                continue
            # Skip macro invocations (lazy_static, include_str, etc. are fine)
            name = _call_name(node, src)
            if name and name not in ("include_str", "include_bytes", "env"):
                findings.append(
                    Finding(
                        rule_id="rs-ast-module-side-effect",
                        severity=Severity.MEDIUM,
                        message=f"module-level call to {name}() — side effect at import time, "
                        "wrap in lazy_static or init function",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="rust",
                        source="ast-phd",
                    )
                )
    return findings


# ── parent map (tree-sitter nodes are immutable, use byte offsets) ────────


def _build_parent_map(root_node):
    """Build {(start_byte, end_byte): parent_node} dict."""
    pm = {}

    def walk(n):
        for c in n.children:
            pm[(c.start_byte, c.end_byte)] = n
            walk(c)

    walk(root_node)
    return pm


def _parent_of(node, parent_map):
    return parent_map.get((node.start_byte, node.end_byte))


# ── dispatch ──

_RULES = [
    _check_unsafe,
    _check_command,
    _check_panic_lib,
    _check_bare_unwrap,
    _check_empty_error_handler,
    _check_fs_remove,
    _check_hardcoded_constant,
    _check_inline_use,
    _check_god_function,
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

    findings: list[Finding] = []
    for rule in _RULES:
        findings.extend(rule(root, sources))
    return findings
