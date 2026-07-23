"""defgraph.py — intra-file function call graph.

Traces which defs call which other defs inside a single file. Same vybe as
graph: +N steps forward (callees), -N steps back (callers).

Python uses ast; the other languages reuse the tree-sitter grammars already
installed for the phd adapters. Edges are restricted to names defined in the
same file — cross-file resolution is graph.py's job.
"""

import os
from pathlib import Path

from audit_code.graph import format_tree, trace_downstream, trace_upstream
from audit_code.models import AuditResult, AuditStatus

# ── per-language tree-sitter specs ────────────────────────────────────────

# ext → (grammar_loader_name, def_node_types, call_node_types)
# grammar_loader_name maps into _load_language below.
_TS_SPECS: dict[str, tuple[str, frozenset[str], frozenset[str]]] = {
    ".js": (
        "javascript",
        frozenset(
            {
                "function_declaration",
                "generator_function_declaration",
                "method_definition",
            }
        ),
        frozenset({"call_expression", "new_expression"}),
    ),
    ".rs": (
        "rust",
        frozenset({"function_item"}),
        frozenset({"call_expression"}),
    ),
    ".go": (
        "go",
        frozenset({"function_declaration", "method_declaration"}),
        frozenset({"call_expression"}),
    ),
    ".java": (
        "java",
        frozenset({"method_declaration", "constructor_declaration"}),
        frozenset({"method_invocation", "object_creation_expression"}),
    ),
    ".kt": (
        "kotlin",
        frozenset({"function_declaration"}),
        frozenset({"call_expression"}),
    ),
    ".swift": (
        "swift",
        frozenset({"function_declaration"}),
        frozenset({"call_expression"}),
    ),
    ".php": (
        "php",
        frozenset({"function_definition", "method_declaration"}),
        frozenset(
            {
                "function_call_expression",
                "member_call_expression",
                "scoped_call_expression",
                "object_creation_expression",
            }
        ),
    ),
    ".cs": (
        "csharp",
        frozenset(
            {
                "method_declaration",
                "local_function_statement",
                "constructor_declaration",
            }
        ),
        frozenset({"invocation_expression", "object_creation_expression"}),
    ),
    ".c": (
        "cpp",
        frozenset({"function_definition"}),
        frozenset({"call_expression"}),
    ),
}

# Extension aliases sharing a spec
for _alias, _canon in {
    ".jsx": ".js",
    ".mjs": ".js",
    ".cjs": ".js",
    ".ts": ".js",
    ".tsx": ".js",
    ".kts": ".kt",
    ".phtml": ".php",
    ".h": ".c",
    ".cpp": ".c",
    ".hpp": ".c",
    ".cc": ".c",
    ".hh": ".c",
    ".cxx": ".c",
    ".hxx": ".c",
}.items():
    _TS_SPECS[_alias] = _TS_SPECS[_canon]

_DEF_GRAPH_EXTS = frozenset(_TS_SPECS) | {".py"}

# Node types that carry a simple name inside a call target
_IDENTIFIER_TYPES = frozenset(
    {
        "identifier",
        "field_identifier",
        "property_identifier",
        "simple_identifier",
        "type_identifier",
        "name",
    }
)


def _load_language(name: str, ext: str):
    """Lazy-load a tree-sitter parser for a language, or None if unavailable."""
    try:
        import tree_sitter as ts
        from tree_sitter import Language

        if name == "javascript":
            if ext in (".ts", ".tsx"):
                import tree_sitter_typescript as tsts

                return ts.Parser(Language(tsts.language_typescript()))
            import tree_sitter_javascript as tsjs

            return ts.Parser(Language(tsjs.language()))
        if name == "rust":
            import tree_sitter_rust as tsrust

            return ts.Parser(Language(tsrust.language()))
        if name == "go":
            import tree_sitter_go as tsgo

            return ts.Parser(Language(tsgo.language()))
        if name == "java":
            import tree_sitter_java as tsjava

            return ts.Parser(Language(tsjava.language()))
        if name == "kotlin":
            import tree_sitter_kotlin as tskt

            return ts.Parser(Language(tskt.language()))
        if name == "swift":
            import tree_sitter_swift as tssw

            return ts.Parser(Language(tssw.language()))
        if name == "php":
            import tree_sitter_php as tsphp

            return ts.Parser(Language(tsphp.language_php()))
        if name == "csharp":
            import tree_sitter_c_sharp as tscs

            return ts.Parser(Language(tscs.language()))
        if name == "cpp":
            import tree_sitter_cpp as tscpp

            return ts.Parser(Language(tscpp.language()))
    except ImportError:
        return None
    return None


# ── tree-sitter extraction ────────────────────────────────────────────────


def _def_name(node, src: bytes) -> str | None:
    """Extract the defined name from a def node (name field, else declarator dig)."""
    named = node.child_by_field_name("name")
    if named is not None and named.type in _IDENTIFIER_TYPES:
        return src[named.start_byte : named.end_byte].decode()
    # C/C++: function_definition → declarator → ... → identifier
    decl = node.child_by_field_name("declarator")
    while decl is not None:
        if decl.type in _IDENTIFIER_TYPES:
            return src[decl.start_byte : decl.end_byte].decode()
        nxt = decl.child_by_field_name("declarator") or decl.child_by_field_name("name")
        if nxt is None:
            # Fall back to first identifier child
            nxt = next((c for c in decl.children if c.type in _IDENTIFIER_TYPES), None)
            if nxt is not None:
                return src[nxt.start_byte : nxt.end_byte].decode()
        decl = nxt
    if named is not None:
        return src[named.start_byte : named.end_byte].decode()
    return None


def _call_name(node, src: bytes) -> str | None:
    """Extract the simple called name from a call node.

    obj.method(), Mod::func(), ptr->fn() all reduce to the last identifier
    of the call target.
    """
    target = (
        node.child_by_field_name("function")
        or node.child_by_field_name("name")
        or node.child_by_field_name("constructor")
        or node.child_by_field_name("type")
    )
    if target is None:
        # Kotlin/Swift call_expression: target is the first child
        target = node.children[0] if node.children else None
    if target is None:
        return None
    if target.type in _IDENTIFIER_TYPES:
        return src[target.start_byte : target.end_byte].decode()
    # Take the last identifier in the target subtree (a.b.c() → c)
    last = None
    stack = [target]
    while stack:
        cur = stack.pop(0)
        if cur.type in _IDENTIFIER_TYPES:
            last = cur
        stack.extend(cur.children)
    if last is not None:
        return src[last.start_byte : last.end_byte].decode()
    return None


def _walk_stop(node, stop_types: frozenset[str]):
    """Yield descendants of node, not descending into nested stop_types nodes."""
    for child in node.children:
        yield child
        if child.type not in stop_types:
            yield from _walk_stop(child, stop_types)


def _ts_def_graph(filepath: Path, text: str) -> dict[str, set[str]] | None:
    """Build {def_name: {called_def_names}} via tree-sitter, or None if no parser."""
    lang_name, def_types, call_types = _TS_SPECS[filepath.suffix]
    parser = _load_language(lang_name, filepath.suffix)
    if parser is None:
        return None

    src = text.encode()
    tree = parser.parse(src)

    def_nodes: list[tuple[str, object]] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in def_types:
            name = _def_name(node, src)
            if name:
                def_nodes.append((name, node))
        stack.extend(node.children)

    defined = {name for name, _ in def_nodes}
    calls: dict[str, set[str]] = {name: set() for name in defined}

    for name, node in def_nodes:
        # Calls inside nested defs belong to the nested def, not this one
        for inner in _walk_stop(node, def_types):
            if inner.type in call_types:
                callee = _call_name(inner, src)
                if callee and callee in defined and callee != name:
                    calls[name].add(callee)
    return calls


# ── Python extraction ─────────────────────────────────────────────────────


def _py_def_graph(text: str) -> dict[str, set[str]] | None:
    """Build {def_name: {called_def_names}} for a Python file via ast."""
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    def_nodes: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            def_nodes.append((node.name, node))

    defined = {name for name, _ in def_nodes}
    calls: dict[str, set[str]] = {name: set() for name in defined}
    def_ids = {id(n) for _, n in def_nodes}

    def _body_walk(node):
        """Walk descendants without descending into nested defs."""
        for child in ast.iter_child_nodes(node):
            yield child
            if id(child) not in def_ids:
                yield from _body_walk(child)

    for name, node in def_nodes:
        for inner in _body_walk(node):
            if not isinstance(inner, ast.Call):
                continue
            fn = inner.func
            callee = None
            if isinstance(fn, ast.Name):
                callee = fn.id
            elif isinstance(fn, ast.Attribute):
                callee = fn.attr
            if callee and callee in defined and callee != name:
                calls[name].add(callee)
    return calls


# ── file resolution ───────────────────────────────────────────────────────

_SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "node_modules", "dist", "build"}


def _find_file(target_root: Path, module: str) -> Path | None:
    """Resolve a file argument like 'cli.py' or 'src/audit_code/cli.py'."""
    direct = Path(module)
    if direct.is_file():
        return direct
    candidate = target_root / module
    if candidate.is_file():
        return candidate
    # Fuzzy: walk the tree for a basename match
    want = Path(module).name
    for dirpath, dirnames, filenames in os.walk(os.fspath(target_root)):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if want in filenames:
            return Path(dirpath) / want
    return None


# ── main entry ─────────────────────────────────────────────────────────────


def build_def_graph(filepath: Path) -> dict[str, set[str]] | None:
    """Build the intra-file call graph for one file, or None if unparseable."""
    if filepath.suffix not in _DEF_GRAPH_EXTS:
        return None
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if filepath.suffix == ".py":
        return _py_def_graph(text)
    return _ts_def_graph(filepath, text)


def run(
    target_root: Path,
    module: str,
    def_name: str,
    forward: int = 2,
    back: int = 2,
    json_out: bool = False,
) -> AuditResult:
    """Main entry point for audit-test graph <file> --def <name>."""
    filepath = _find_file(Path(target_root).resolve(), module)
    if filepath is None:
        return AuditResult(
            audit_id="graph",
            status=AuditStatus.ERROR,
            stderr=f"file not found: {module}",
        )

    graph = build_def_graph(filepath)
    if graph is None:
        return AuditResult(
            audit_id="graph",
            status=AuditStatus.ERROR,
            stderr=f"cannot build def graph for {filepath.name} "
            f"(unsupported extension, syntax error, or missing grammar)",
        )

    node = def_name
    if node not in graph:
        # Fuzzy: substring match
        matches = sorted(k for k in graph if def_name in k)
        if len(matches) == 1:
            node = matches[0]
        else:
            hint = f" — did you mean: {', '.join(matches[:8])}?" if matches else ""
            available = ", ".join(sorted(graph)[:20])
            return AuditResult(
                audit_id="graph",
                status=AuditStatus.ERROR,
                stderr=f"def not found: {def_name}{hint}\n"
                f"defs in {filepath.name}: {available}",
            )

    upstream = trace_upstream(graph, node, back)
    downstream = trace_downstream(graph, node, forward)

    if json_out:
        import json as _json

        edges = []
        for src in {node} | set(upstream) | set(downstream):
            for tgt in graph.get(src, set()):
                if tgt in upstream or tgt in downstream or tgt == node or src == node:
                    edges.append({"from": src, "to": tgt, "type": "call"})
        result = {
            "file": str(filepath),
            "def": node,
            "upstream": sorted([n for n in upstream if n != node]),
            "downstream": sorted([n for n in downstream if n != node]),
            "edges": edges,
        }
        return AuditResult(
            audit_id="graph",
            status=AuditStatus.PASS,
            stdout=_json.dumps(result, indent=2, ensure_ascii=False),
        )

    tree = format_tree(node, upstream, downstream, graph)
    return AuditResult(
        audit_id="graph",
        status=AuditStatus.PASS,
        stdout=f"{filepath.name}\n{tree}",
    )
