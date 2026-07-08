"""AST-based JavaScript/TypeScript PhD rules via tree-sitter.

Regex (polyglot.py) catches surface patterns. This module catches
structural bugs that need an actual parse tree: hook misuse, missing
keys, dead state, etc.
"""

from pathlib import Path

import tree_sitter as ts
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language

from audit_code.adapters.base import rel
from audit_code.models import Finding, Severity

# ── parser setup ──

_JS_LANG = Language(tsjs.language())
_TS_LANG = Language(tsts.language_typescript())
_JS_PARSER = ts.Parser(_JS_LANG)
_TS_PARSER = ts.Parser(_TS_LANG)

_JS_EXT = frozenset({".js", ".jsx", ".mjs", ".cjs"})
_TS_EXT = frozenset({".ts", ".tsx"})
_ALL_EXT = _JS_EXT | _TS_EXT


def _parser_for(path: Path) -> ts.Parser:
    return _TS_PARSER if path.suffix in _TS_EXT else _JS_PARSER


# ── tree walker with parent tracking ──


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_key(node) -> tuple:
    """Stable key for a tree-sitter node (byte offsets survive re-traversal)."""
    return (node.start_byte, node.end_byte)


def _build_parent_map(root_node):
    """Build {node_key: parent_node} for ancestor traversal."""
    parents = {}

    def walk(n):
        for c in n.children:
            parents[_node_key(c)] = n
            walk(c)

    walk(root_node)
    return parents


def _has_catch_in_chain(node, parents, src, max_depth=15) -> bool:
    """Walk up from a call_expression to check if .catch() appears anywhere
    in the method chain, traversing through member_expression and
    await_expression intermediate nodes."""
    cur = node
    for _ in range(max_depth):
        parent = parents.get(_node_key(cur))
        if parent is None:
            return False
        if parent.type == "try_statement":
            return True  # try/catch is error handling
        if parent.type == "call_expression":
            pfn = parent.child_by_field_name("function")
            if pfn and pfn.type == "member_expression":
                pprop = pfn.child_by_field_name("property")
                if pprop and src[pprop.start_byte : pprop.end_byte].decode() == "catch":
                    return True
        elif parent.type == "member_expression":
            pprop = parent.child_by_field_name("property")
            if pprop and src[pprop.start_byte : pprop.end_byte].decode() == "catch":
                return True
        # Continue walking up through any node type
        cur = parent
    return False


# ── rule: useEffect missing dependency array (J1.7) ─────────────────────


def _check_useffect_deps(root: Path, sources: dict[Path, str]) -> list[Finding]:
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()

        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            fn = node.child_by_field_name("function")
            if fn is None:
                continue
            if src[fn.start_byte : fn.end_byte].decode() != "useEffect":
                continue
            args = node.child_by_field_name("arguments")
            if args is None:
                continue
            named = [c for c in args.children if c.is_named]
            if len(named) >= 2:
                continue  # has dep array
            findings.append(
                Finding(
                    rule_id="js-ast-useeffect-deps",
                    severity=Severity.MEDIUM,
                    message="useEffect without dependency array — may cause "
                    "infinite re-renders",
                    file=rel(path, root),
                    line=node.start_point[0] + 1,
                    language="javascript",
                    source="ast-phd",
                )
            )
    return findings


# ── rule: dangerouslySetInnerHTML via AST (J1.1 — AST version) ───────────


def _check_dangerous_html(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """AST-level check: any JSX attribute named dangerouslySetInnerHTML."""
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()

        for node in _walk(tree.root_node):
            if node.type != "jsx_attribute":
                continue
            name_node = next((c for c in node.children if c.is_named), None)
            if name_node is None:
                continue
            if (
                src[name_node.start_byte : name_node.end_byte].decode()
                == "dangerouslySetInnerHTML"
            ):
                findings.append(
                    Finding(
                        rule_id="js-ast-dangerous-html",
                        severity=Severity.HIGH,
                        message="dangerouslySetInnerHTML — React XSS vector, "
                        "use a sanitizer instead",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="javascript",
                        source="ast-phd",
                    )
                )
    return findings


# ── rule: J1.5 — fetch() without error handling ──────────────────────────


def _check_fetch_no_error(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """Flag fetch() calls without .catch() or try/catch wrapper."""
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()
        parents = _build_parent_map(tree.root_node)

        # Track which fetch() root calls we've already flagged to avoid
        # double-flagging when .then() is also present.
        flagged_lines: set[int] = set()

        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            fn = node.child_by_field_name("function")
            if fn is None:
                continue
            # Only match direct `fetch(...)` calls (not `.then()` or `.catch()`)
            if fn.type != "identifier":
                continue
            if src[fn.start_byte : fn.end_byte].decode() != "fetch":
                continue

            line = node.start_point[0] + 1
            if line in flagged_lines:
                continue

            # Walk up the full method chain: fetch().then().catch()
            # If we find .catch() anywhere, or a try_statement ancestor, skip
            if _has_catch_in_chain(node, parents, src):
                continue

            flagged_lines.add(line)
            findings.append(
                Finding(
                    rule_id="js-ast-fetch-no-error",
                    severity=Severity.MEDIUM,
                    message="fetch() without error handling — add .catch() or try/catch",
                    file=rel(path, root),
                    line=line,
                    language="javascript",
                    source="ast-phd",
                )
            )
    return findings


# ── rule: J1.6 — missing key in .map() returning JSX ─────────────────────


def _check_map_missing_key(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """Flag .map() calls that return JSX without a key prop."""
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()

        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            fn = node.child_by_field_name("function")
            if fn is None or fn.type != "member_expression":
                continue
            prop = fn.child_by_field_name("property")
            if prop is None:
                continue
            if src[prop.start_byte : prop.end_byte].decode() != "map":
                continue

            # Check the callback returns JSX
            args = node.child_by_field_name("arguments")
            if args is None:
                continue
            named_args = [c for c in args.children if c.is_named]
            if not named_args:
                continue
            callback = named_args[0]  # first arg to .map()
            # Arrow function body
            body = callback.child_by_field_name("body")
            if body is None:
                continue
            # Is the body JSX?
            is_jsx = body.type in ("jsx_element", "jsx_self_closing_element")
            if not is_jsx:
                # Check if it's a return_statement containing JSX
                if body.type == "statement_block":
                    for stmt in body.children:
                        if stmt.type == "return_statement":
                            ret_val = next(
                                (
                                    c
                                    for c in stmt.children
                                    if c.is_named
                                    and c.type
                                    in ("jsx_element", "jsx_self_closing_element")
                                ),
                                None,
                            )
                            if ret_val:
                                is_jsx = True
                                body = ret_val
                                break

            if not is_jsx:
                continue

            # Check if the JSX element has a key attribute
            has_key = False
            if body.type == "jsx_element":
                opening = next(
                    (c for c in body.children if c.type == "jsx_opening_element"), None
                )
            else:  # jsx_self_closing_element
                opening = body

            if opening:
                for attr in opening.children:
                    if attr.type != "jsx_attribute":
                        continue
                    name_node = next((c for c in attr.children if c.is_named), None)
                    if (
                        name_node
                        and src[name_node.start_byte : name_node.end_byte].decode()
                        == "key"
                    ):
                        has_key = True
                        break

            if not has_key:
                findings.append(
                    Finding(
                        rule_id="js-ast-map-missing-key",
                        severity=Severity.MEDIUM,
                        message=".map() returning JSX without a key prop — "
                        "causes React reconciliation bugs",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="javascript",
                        source="ast-phd",
                    )
                )
    return findings


# ── rule: J1.11 — inline style= objects ──────────────────────────────────


def _check_inline_style_objects(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """Flag JSX style={{...}} inline objects (new object every render)."""
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()

        for node in _walk(tree.root_node):
            if node.type != "jsx_attribute":
                continue
            name_node = next((c for c in node.children if c.is_named), None)
            if name_node is None:
                continue
            if src[name_node.start_byte : name_node.end_byte].decode() != "style":
                continue
            # Check if value is an object literal (not a variable ref)
            for child in node.children:
                if child.type == "jsx_expression":
                    inner = next((c for c in child.children if c.is_named), None)
                    if inner and inner.type == "object":
                        findings.append(
                            Finding(
                                rule_id="js-ast-inline-style-object",
                                severity=Severity.INFO,
                                message="inline style={{...}} creates a new object "
                                "every render — extract to a constant or use CSS",
                                file=rel(path, root),
                                line=node.start_point[0] + 1,
                                language="javascript",
                                source="ast-phd",
                            )
                        )
                        break
    return findings


# ── rule: J1.13 — .then() missing .catch() ───────────────────────────────


def _check_then_no_catch(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """Flag .then() chains without a terminal .catch()."""
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()
        parents = _build_parent_map(tree.root_node)

        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            fn = node.child_by_field_name("function")
            if fn is None or fn.type != "member_expression":
                continue
            prop = fn.child_by_field_name("property")
            if prop is None:
                continue
            prop_name = src[prop.start_byte : prop.end_byte].decode()
            if prop_name != "then":
                continue

            # Walk up through the chain checking for .catch()
            if _has_catch_in_chain(node, parents, src):
                continue

            findings.append(
                Finding(
                    rule_id="js-ast-then-no-catch",
                    severity=Severity.HIGH,
                    message=".then() without .catch() — unhandled promise rejection",
                    file=rel(path, root),
                    line=node.start_point[0] + 1,
                    language="javascript",
                    source="ast-phd",
                )
            )
    return findings


# ── rule: J1.14 — duplicate export names ──────────────────────────────────


def _check_duplicate_exports(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """Flag files where the same name is exported more than once."""
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()

        seen: dict[str, int] = {}  # name -> first line
        for node in _walk(tree.root_node):
            if node.type != "export_statement":
                continue
            # Extract the exported name
            exported_name = None
            for child in node.children:
                if child.type == "function_declaration":
                    ide = child.child_by_field_name("name")
                    if ide:
                        exported_name = src[ide.start_byte : ide.end_byte].decode()
                elif child.type == "lexical_declaration":
                    for decl in child.children:
                        if decl.type == "variable_declarator":
                            ide = decl.child_by_field_name("name")
                            if ide:
                                exported_name = src[
                                    ide.start_byte : ide.end_byte
                                ].decode()
                elif child.type == "class_declaration":
                    ide = child.child_by_field_name("name")
                    if ide:
                        exported_name = src[ide.start_byte : ide.end_byte].decode()

            if exported_name is None:
                continue

            if exported_name in seen:
                findings.append(
                    Finding(
                        rule_id="js-ast-duplicate-export",
                        severity=Severity.MEDIUM,
                        message=f"'{exported_name}' exported multiple times "
                        f"(first at line {seen[exported_name]}) — shadowing bug",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="javascript",
                        source="ast-phd",
                    )
                )
            else:
                seen[exported_name] = node.start_point[0] + 1
    return findings


# ── rule: J1.8 — useState variable never used in JSX ────────────────────


def _check_usestate_unused_in_jsx(
    root: Path, sources: dict[Path, str]
) -> list[Finding]:
    """Flag useState destructured variables that never appear in any JSX
    expression within the same component function body."""
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()
        parents = _build_parent_map(tree.root_node)

        for fn_node in _walk(tree.root_node):
            if fn_node.type not in ("function_declaration", "arrow_function"):
                continue
            body = fn_node.child_by_field_name("body")
            if body is None:
                continue

            # Collect useState destructured names + their lines
            state_vars: dict[str, int] = {}  # name -> line
            for node in _walk(body):
                if node.type != "call_expression":
                    continue
                callee = node.child_by_field_name("function")
                if callee is None or callee.type != "identifier":
                    continue
                if src[callee.start_byte : callee.end_byte].decode() != "useState":
                    continue
                # Walk up to variable_declarator → array_pattern
                cur = node
                for _ in range(5):
                    cur = parents.get(_node_key(cur))
                    if cur is None:
                        break
                    if cur.type == "variable_declarator":
                        pat = cur.child_by_field_name("name")
                        if pat and pat.type == "array_pattern":
                            # First array element is the state variable
                            first = next(
                                (
                                    c
                                    for c in pat.children
                                    if c.is_named and c.type == "identifier"
                                ),
                                None,
                            )
                            if first:
                                name = src[first.start_byte : first.end_byte].decode()
                                state_vars[name] = node.start_point[0] + 1
                        break

            if not state_vars:
                continue

            # Collect all identifiers used inside JSX expressions in this body
            used_in_jsx: set[str] = set()
            for node in _walk(body):
                if node.type == "identifier":
                    # Check if inside a jsx_expression or jsx_element
                    cur = node
                    for _ in range(15):
                        cur = parents.get(_node_key(cur))
                        if cur is None:
                            break
                        if cur.type in (
                            "jsx_expression",
                            "jsx_element",
                            "jsx_self_closing_element",
                        ):
                            name = src[node.start_byte : node.end_byte].decode()
                            used_in_jsx.add(name)
                            break

            for name, line in state_vars.items():
                if name not in used_in_jsx:
                    findings.append(
                        Finding(
                            rule_id="js-ast-usestate-unused-in-jsx",
                            severity=Severity.MEDIUM,
                            message=f"useState variable '{name}' never used in "
                            "JSX — dead state",
                            file=rel(path, root),
                            line=line,
                            language="javascript",
                            source="ast-phd",
                        )
                    )
    return findings


# ── rule: J2.3 — React.memo missing on exported components ───────────────


def _check_missing_react_memo(root: Path, sources: dict[Path, str]) -> list[Finding]:
    """Flag exported components not wrapped in React.memo."""
    findings: list[Finding] = []
    for path, text in sources.items():
        parser = _parser_for(path)
        tree = parser.parse(text.encode())
        src = text.encode()

        for node in _walk(tree.root_node):
            if node.type != "export_statement":
                continue
            # Skip export default wrapped in React.memo
            child = next(
                (c for c in node.children if c.is_named and c.type != "export"), None
            )
            if child is None:
                continue

            if child.type == "call_expression":
                fn = child.child_by_field_name("function")
                if fn and fn.type == "member_expression":
                    prop = fn.child_by_field_name("property")
                    if prop and src[prop.start_byte : prop.end_byte].decode() == "memo":
                        continue  # already wrapped

            # Extract component name + check if it returns JSX
            comp_name = None
            returns_jsx = False
            if child.type == "function_declaration":
                ide = child.child_by_field_name("name")
                if ide:
                    comp_name = src[ide.start_byte : ide.end_byte].decode()
                body = child.child_by_field_name("body")
                returns_jsx = body is not None and _body_returns_jsx(body, src)
            elif child.type == "lexical_declaration":
                for decl in child.children:
                    if decl.type != "variable_declarator":
                        continue
                    ide = decl.child_by_field_name("name")
                    val = decl.child_by_field_name("value")
                    if val and val.type == "arrow_function":
                        if ide:
                            comp_name = src[ide.start_byte : ide.end_byte].decode()
                        abody = val.child_by_field_name("body")
                        returns_jsx = abody is not None and _body_returns_jsx(
                            abody, src
                        )
            elif child.type == "class_declaration":
                ide = child.child_by_field_name("name")
                if ide:
                    comp_name = src[ide.start_byte : ide.end_byte].decode()
                returns_jsx = True  # class components always render JSX
            else:
                continue

            if comp_name and returns_jsx:
                findings.append(
                    Finding(
                        rule_id="js-ast-missing-react-memo",
                        severity=Severity.INFO,
                        message=f"exported component '{comp_name}' not wrapped in "
                        "React.memo — may cause unnecessary re-renders",
                        file=rel(path, root),
                        line=node.start_point[0] + 1,
                        language="javascript",
                        source="ast-phd",
                    )
                )
    return findings


def _body_returns_jsx(body, src) -> bool:
    """Check if a function/arrow body returns JSX."""
    # Direct JSX return: () => <div/>
    if body.type in ("jsx_element", "jsx_self_closing_element"):
        return True
    # Block body with return statement containing JSX
    if body.type == "statement_block":
        for stmt in body.children:
            if stmt.type == "return_statement":
                for child in stmt.children:
                    if child.is_named and child.type in (
                        "jsx_element",
                        "jsx_self_closing_element",
                    ):
                        return True
    return False


# ── dispatch ──


_RULES = [
    _check_useffect_deps,
    _check_dangerous_html,
    _check_fetch_no_error,
    _check_map_missing_key,
    _check_inline_style_objects,
    _check_then_no_catch,
    _check_duplicate_exports,
    _check_usestate_unused_in_jsx,
    _check_missing_react_memo,
]


def run(root: Path, files: list[Path]) -> list[Finding]:
    """Run all AST-based JS PhD rules and return findings."""
    sources = {}
    for f in files:
        if f.suffix in _ALL_EXT:
            try:
                sources[f] = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

    findings: list[Finding] = []
    for rule in _RULES:
        findings.extend(rule(root, sources))
    return findings
