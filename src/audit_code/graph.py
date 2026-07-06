"""graph.py — dependency graph tracer.

Traces import relationships between modules. Same vybe as scan:
+N steps forward (downstream), -N steps back (upstream).
"""

from pathlib import Path

from audit_code.models import AuditResult, AuditStatus


def _parse_imports(filepath: Path) -> set[str]:
    """Extract local module names imported by a .py file."""
    import ast
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()

    pkg = _package_root(filepath)
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    # Resolve local modules: "audit_code.config" → "/abs/path/src/audit_code/config.py"
    resolved: set[str] = set()
    for name in imports:
        found = _resolve_local(pkg, name)
        if found:
            resolved.add(found)
    return resolved


def _package_root(filepath: Path) -> Path | None:
    """Walk up to find the project root (where pyproject.toml lives)."""
    d = filepath.resolve().parent
    while d != d.parent:
        if (d / "pyproject.toml").exists() or (d / "setup.py").exists():
            return d
        d = d.parent
    return None


def _resolve_local(pkg_root: Path | None, name: str) -> str | None:
    """Resolve an import name to an absolute .py path, or None if external."""
    if not pkg_root:
        return None
    # Common source roots: project root, or src/ subdirectory
    roots = [pkg_root]
    src = pkg_root / "src"
    if src.is_dir():
        roots.append(src)
    for root in roots:
        # Direct module: audit_code/config.py
        parts = name.split(".")
        candidate = root.joinpath(*parts).with_suffix(".py")
        if candidate.exists():
            return str(candidate)
        # Package: audit_code → audit_code/__init__.py
        candidate = root.joinpath(*parts) / "__init__.py"
        if candidate.exists():
            return str(candidate)
    return None


def build_graph(target_root: Path) -> dict[str, set[str]]:
    """Build adjacency: {module_path: {imported_module_paths}}."""
    import os
    graph: dict[str, set[str]] = {}
    skip = {".venv", "venv", "__pycache__", ".git", "node_modules", "dist", "build"}

    for dirpath, dirnames, filenames in os.walk(os.fspath(target_root)):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = Path(dirpath) / fname
            imports = _parse_imports(full)
            resolved = set()
            for imp in imports:
                # imp is now an absolute path like /mnt/c/AI/audit/src/audit_code/config.py
                # Store as relative to target_root for display
                try:
                    rel = str(Path(imp).relative_to(target_root))
                except ValueError:
                    continue
                resolved.add(rel)
            try:
                rel = str(full.relative_to(target_root))
            except ValueError:
                continue
            graph[rel] = resolved

    return graph


def trace_upstream(graph: dict[str, set[str]], node: str, depth: int) -> dict:
    """BFS upstream: who imports this node, up to `depth` levels."""
    visited: dict[str, int] = {node: 0}
    queue = [(node, 0)]

    # Build reverse index
    imported_by: dict[str, set[str]] = {}
    for src, targets in graph.items():
        for tgt in targets:
            imported_by.setdefault(tgt, set()).add(src)

    while queue:
        current, dist = queue.pop(0)
        if dist >= depth:
            continue
        for caller in imported_by.get(current, set()):
            if caller not in visited:
                visited[caller] = dist + 1
                queue.append((caller, dist + 1))

    return visited


def trace_downstream(graph: dict[str, set[str]], node: str, depth: int) -> dict:
    """BFS downstream: what does this node import, up to `depth` levels."""
    visited: dict[str, int] = {node: 0}
    queue = [(node, 0)]

    while queue:
        current, dist = queue.pop(0)
        if dist >= depth:
            continue
        for target in graph.get(current, set()):
            if target not in visited:
                visited[target] = dist + 1
                queue.append((target, dist + 1))

    return visited


def format_tree(node: str, upstream: dict, downstream: dict, graph: dict) -> str:
    """Draw an ASCII tree showing upstream ← node → downstream."""
    lines = []

    # Upstream (callers)
    if len(upstream) > 1:
        up_nodes = sorted(
            [(n, d) for n, d in upstream.items() if n != node],
            key=lambda x: x[1],
        )
        for name, dist in up_nodes:
            prefix = "    " * (dist - 1) + "┌── " if dist == 1 else "    " * (dist - 1) + "├── "
            lines.append(f"{prefix}{name}  (calls this)")

    # Current node
    lines.append(f"► {node}")

    # Downstream (imports)
    if len(downstream) > 1:
        down_nodes = sorted(
            [(n, d) for n, d in downstream.items() if n != node],
            key=lambda x: x[1],
        )
        for name, dist in down_nodes:
            prefix = "    " * (dist - 1) + "└── " if dist == 1 else "    " * (dist - 1) + "├── "
            lines.append(f"{prefix}{name}")

    return "\n".join(lines)


def run(
    target_root: Path,
    module: str = "",
    forward: int = 2,
    back: int = 2,
    json_out: bool = False,
) -> AuditResult:
    """Main entry point for audit-test graph command."""
    import json

    graph = build_graph(target_root)
    if module:
        # Normalize: accept "cli.py" or "src/audit_code/cli.py"
        candidates = [
            module,
            f"src/audit_code/{module}",
        ]
        node = None
        for c in candidates:
            if c in graph:
                node = c
                break
        if node is None:
            # Fuzzy: find any matching file
            for k in graph:
                if k.endswith(module) or module in k:
                    node = k
                    break
        if node is None:
            return AuditResult(
                audit_id="graph",
                status=AuditStatus.ERROR,
                stderr=f"module not found: {module}",
            )
    else:
        node = next(iter(graph)) if graph else ""

    upstream = trace_upstream(graph, node, back)
    downstream = trace_downstream(graph, node, forward)

    if json_out:
        import json as _json
        edges = []
        for src in {node} | set(upstream) | set(downstream):
            for tgt in graph.get(src, set()):
                if tgt in upstream or tgt in downstream or tgt == node or src == node:
                    edges.append({"from": src, "to": tgt, "type": "import"})
        result = {
            "node": node,
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
        stdout=tree,
    )
