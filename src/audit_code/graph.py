"""graph.py — dependency graph tracer.

Traces import relationships between modules. Same vybe as scan:
+N steps forward (downstream), -N steps back (upstream).

Supports Python (ast.parse) and JavaScript/TypeScript (tree-sitter).
"""

from pathlib import Path

from audit_code.models import AuditResult, AuditStatus

# ── JS/TS import extraction ───────────────────────────────────────────────

_JS_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
_JS_INDEX_FILES = frozenset({"index.js", "index.jsx", "index.ts", "index.tsx"})


def _get_js_parser():
    """Lazy-load tree-sitter JS parser (same setup as ast_rules.py)."""
    try:
        import tree_sitter as _ts
        import tree_sitter_javascript as _tsjs
        import tree_sitter_typescript as _tsts
        from tree_sitter import Language as _Lang
    except ImportError:
        return None, None

    _js_lang = _Lang(_tsjs.language())
    _ts_lang = _Lang(_tsts.language_typescript())
    return _ts.Parser(_js_lang), _ts.Parser(_ts_lang)


def _parse_js_imports(filepath: Path) -> set[str]:
    """Extract local import paths from a .js/.ts file using tree-sitter."""
    js_parser, ts_parser = _get_js_parser()
    if js_parser is None or ts_parser is None:
        return set()

    parser = ts_parser if filepath.suffix in (".ts", ".tsx") else js_parser

    try:
        src = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()

    tree = parser.parse(src.encode())
    src_bytes = src.encode()
    imports: set[str] = set()

    for node in _walk_ts(tree.root_node):
        # ES module: import ... from 'specifier'
        if node.type == "import_statement":
            spec = _ts_child_by_field(node, "source")
            if spec and spec.type == "string":
                raw = _ts_string_value(spec, src_bytes)
                if raw and not raw.startswith(("@", "node:")) and "/" in raw:
                    imports.add(raw)
            continue

        if node.type != "call_expression":
            continue

        fn = _ts_child_by_field(node, "function")
        if fn is None:
            continue

        # Dynamic: import('specifier')
        if fn.type == "import":
            args = _ts_child_by_field(node, "arguments")
            if args:
                strings = [
                    c for c in args.children if c.is_named and c.type == "string"
                ]
                if strings:
                    raw = _ts_string_value(strings[0], src_bytes)
                    if raw and not raw.startswith(("@", "node:")) and "/" in raw:
                        imports.add(raw)
            continue

        # CJS: require('specifier')
        if fn.type == "identifier":
            name = src_bytes[fn.start_byte : fn.end_byte].decode()
            if name == "require":
                args = _ts_child_by_field(node, "arguments")
                if args:
                    strings = [
                        c for c in args.children if c.is_named and c.type == "string"
                    ]
                    if strings:
                        raw = _ts_string_value(strings[0], src_bytes)
                        if raw and not raw.startswith(("@", "node:")) and "/" in raw:
                            imports.add(raw)

    return imports


def _resolve_js_local(base_dir: Path, spec: str) -> str | None:
    """Resolve a JS/TS import spec relative to base_dir.

    './foo' → base_dir/foo.js | foo/index.js | foo.ts
    Returns absolute path as string, or None if unresolvable.
    """
    if spec.startswith(("@", "node:", "http:", "https:")):
        return None

    candidate = (base_dir / spec).resolve()

    # Exact match
    if candidate.is_file():
        return str(candidate)

    # Extension probing
    for ext in (
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        "/index.js",
        "/index.jsx",
        "/index.ts",
        "/index.tsx",
    ):
        probe = (
            Path(str(candidate) + ext)
            if not ext.startswith("/")
            else candidate / ext.lstrip("/")
        )
        if probe.is_file():
            return str(probe)

    return None


def _ts_child_by_field(node, field):
    """tree-sitter child_by_field_name that returns None on missing field."""
    try:
        return node.child_by_field_name(field)
    except Exception:
        return None


def _ts_string_value(node, src: bytes) -> str | None:
    """Extract the string value from a tree-sitter string node (strips quotes)."""
    try:
        raw = src[node.start_byte : node.end_byte].decode()
        if len(raw) >= 2 and raw[0] in ('"', "'", "`") and raw[-1] == raw[0]:
            return raw[1:-1]
        return raw
    except Exception:
        return None


def _walk_ts(node):
    """Yield all nodes in a tree-sitter tree (like _walk in ast_rules.py)."""
    yield node
    for child in node.children:
        yield from _walk_ts(child)


# ── Python import extraction (existing) ────────────────────────────────────


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


# ── Rust import extraction ────────────────────────────────────────────────

_RUST_EXTS = frozenset({".rs"})


def _parse_rust_imports(filepath: Path) -> set[str]:
    """Extract local crate/module imports from a .rs file."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    imports: set[str] = set()
    # use crate::X::Y  |  use super::X  |  use self::X
    for m in _re.finditer(r"\buse\s+(crate|super|self)::(\S+)", text):
        imports.add(f"{m.group(1)}::{m.group(2).rstrip(';')}")
    # mod foo;  (inline module declaration — resolves to foo.rs or foo/mod.rs)
    for m in _re.finditer(r"\bmod\s+(\w+)\s*;", text):
        imports.add(f"mod:{m.group(1)}")
    return imports


def _resolve_rust_local(base_dir: Path, spec: str) -> str | None:
    """Resolve a Rust import to a file path."""
    if spec.startswith("mod:"):
        name = spec[4:]
        for candidate in [base_dir / f"{name}.rs", base_dir / name / "mod.rs"]:
            if candidate.is_file():
                return str(candidate)
        return None
    # crate::engine::core → src/engine/core.rs or src/engine/core/mod.rs
    if spec.startswith("crate::"):
        parts = spec[7:].split("::")
        # Walk up to find project root (where Cargo.toml lives)
        d = base_dir
        while d != d.parent and not (d / "Cargo.toml").exists():
            d = d.parent
        root = d if (d / "Cargo.toml").exists() else base_dir
        # Try src/ prefix first (most common), then root
        for prefix in ["src", ""]:
            r = root / prefix
            if not r.exists():
                continue
            path = r.joinpath(*parts)
            for candidate in [path.with_suffix(".rs"), path / "mod.rs"]:
                if candidate.is_file():
                    return str(candidate)
        return None
    # super::X → ../x.rs
    if spec.startswith("super::"):
        parts = spec[7:].split("::")
        d = base_dir
        for _ in range(parts.count("super") + 1 if "super" in spec else 1):
            d = d.parent
        path = d.joinpath(*[p for p in parts if p != "super"])
        for candidate in [path.with_suffix(".rs"), path / "mod.rs"]:
            if candidate.is_file():
                return str(candidate)
    return None


# ── Go import extraction ──────────────────────────────────────────────────

_GO_EXTS = frozenset({".go"})


def _parse_go_imports(filepath: Path) -> set[str]:
    """Extract local package imports from a .go file."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    imports: set[str] = set()
    # import "pkg/path"  (single line)
    # import ( "a" ; "b" )  (multi-line block)
    for m in _re.finditer(r'"([^"]+)"', text):
        pkg = m.group(1)
        # Skip stdlib (no dots in first segment) and external URLs
        if "." not in pkg.split("/")[0] and "/" in pkg:
            imports.add(pkg)
    return imports


def _resolve_go_local(base_dir: Path, spec: str) -> str | None:
    """Resolve a Go import path to a directory (go packages are dirs, not files)."""
    # Walk up to find go.mod, extract module path
    d = base_dir
    mod_root = None
    mod_path = ""
    while d != d.parent:
        gomod = d / "go.mod"
        if gomod.exists():
            mod_root = d
            # Extract module name from go.mod first line
            first = gomod.read_text(encoding="utf-8", errors="replace").split("\n")[0]
            if first.startswith("module "):
                mod_path = first[7:].strip()
            break
        d = d.parent
    if not mod_root or not mod_path:
        return None
    # If spec starts with module path, strip it
    if spec.startswith(mod_path):
        rel = spec[len(mod_path) :].lstrip("/")
        target = mod_root / rel
        if target.is_dir():
            return str(target)
    # Try relative to module root directly
    target = mod_root / spec
    if target.is_dir():
        return str(target)
    return None


# ── Java import extraction ────────────────────────────────────────────────

_JAVA_EXTS = frozenset({".java"})


def _parse_java_imports(filepath: Path) -> set[str]:
    """Extract local package imports from a .java file."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    imports: set[str] = set()
    for m in _re.finditer(r"^import\s+((?:static\s+)?[\w.]+)", text, _re.MULTILINE):
        imports.add(m.group(1))
    return imports


def _resolve_java_local(base_dir: Path, spec: str, target_root: Path) -> str | None:
    """Resolve a Java import to a .java file path."""
    # Strip 'static ' prefix if present
    pkg = spec.replace("static ", "", 1)
    parts = pkg.split(".")
    # Walk up to find a source root (where the package tree starts)
    # Common source roots: src/main/java, src, .
    for src_candidate in [
        base_dir,  # relative to current file
        target_root / "src" / "main" / "java",
        target_root / "src",
        target_root,
    ]:
        if not src_candidate.exists():
            continue
        candidate = src_candidate.joinpath(*parts).with_suffix(".java")
        if candidate.is_file():
            return str(candidate)
    return None


# ── C# import extraction ─────────────────────────────────────────────────

_CSHARP_EXTS = frozenset({".cs"})


import os as _os


def _parse_csharp_imports(filepath: Path) -> set[str]:
    """Extract using directives from a .cs file."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    imports: set[str] = set()
    for m in _re.finditer(r"^using\s+((?:static\s+)?[\w.]+)", text, _re.MULTILINE):
        imports.add(m.group(1))
    return imports


def _resolve_csharp_local(base_dir: Path, spec: str, target_root: Path) -> str | None:
    """Resolve a C# using directive to a .cs file."""
    ns = spec.replace("static ", "", 1)
    name = ns.split(".")[-1]  # last segment is usually the class name
    # Search for matching .cs file
    for dirpath, dirnames, filenames in _os.walk(_os.fspath(target_root)):
        dirnames[:] = [
            d for d in dirnames if d not in {".git", "bin", "obj", "node_modules"}
        ]
        for fn in filenames:
            if fn == f"{name}.cs" and fn.endswith(".cs"):
                return str(Path(dirpath) / fn)
    return None


# ── Swift import extraction ───────────────────────────────────────────────

_SWIFT_EXTS = frozenset({".swift"})


def _parse_swift_imports(filepath: Path) -> set[str]:
    """Extract import ModuleName from .swift files."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    imports: set[str] = set()
    for m in _re.finditer(r"^import\s+(\w+)", text, _re.MULTILINE):
        imports.add(m.group(1))
    return imports


def _resolve_swift_local(base_dir: Path, spec: str, target_root: Path) -> str | None:
    """Resolve a Swift module name to a .swift file by name matching."""
    for dirpath, dirnames, filenames in _os.walk(_os.fspath(target_root)):
        dirnames[:] = [
            d for d in dirnames if d not in {".git", "build", ".build", "DerivedData"}
        ]
        for fn in filenames:
            if fn == f"{spec}.swift":
                return str(Path(dirpath) / fn)
    return None


# ── PHP import extraction ────────────────────────────────────────────────

_PHP_EXTS = frozenset({".php", ".phtml"})


def _parse_php_imports(filepath: Path) -> set[str]:
    """Extract require/include + use Namespace from .php files."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    imports: set[str] = set()
    # require 'file.php', include 'file.php', require_once, include_once
    for m in _re.finditer(r"(?:require|include)(?:_once)?\s*['\"]([^'\"]+)['\"]", text):
        p = m.group(1)
        if p.endswith(".php") and "/" in p:
            imports.add(p)
    # use Namespace\Class;
    for m in _re.finditer(r"^use\s+([\w\\]+)", text, _re.MULTILINE):
        imports.add(m.group(1))
    return imports


def _resolve_php_local(base_dir: Path, spec: str, target_root: Path) -> str | None:
    """Resolve PHP require/include or use to a file path."""
    # Relative path (require './foo.php')
    if spec.endswith(".php") and ("/" in spec or spec.startswith(".")):
        candidate = (base_dir / spec).resolve()
        if candidate.is_file():
            return str(candidate)
    # use Namespace\Class → Namespace/Class.php
    if "\\" in spec:
        parts = spec.replace("\\", "/").split("/")
        for dirpath, dirnames, filenames in _os.walk(_os.fspath(target_root)):
            dirnames[:] = [
                d for d in dirnames if d not in {".git", "vendor", "node_modules"}
            ]
            for fn in filenames:
                if fn == f"{parts[-1]}.php":
                    return str(Path(dirpath) / fn)
    return None


# ── C/C++ import extraction ───────────────────────────────────────────────

_CEXTS = frozenset({".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx"})


def _parse_c_imports(filepath: Path) -> set[str]:
    """Extract local #include directives from a C/C++ file."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    imports: set[str] = set()
    for m in _re.finditer(r'#include\s+"([^"]+)"', text):
        imports.add(m.group(1))
    return imports


def _resolve_c_local(base_dir: Path, spec: str) -> str | None:
    """Resolve a #include "file.h" to an absolute path."""
    candidate = (base_dir / spec).resolve()
    if candidate.is_file():
        return str(candidate)
    return None


# ── dispatch tables ───────────────────────────────────────────────────────


_IMPORT_EXTRACTORS = {
    ".py": _parse_imports,
    ".js": _parse_js_imports,
    ".jsx": _parse_js_imports,
    ".mjs": _parse_js_imports,
    ".cjs": _parse_js_imports,
    ".ts": _parse_js_imports,
    ".tsx": _parse_js_imports,
    ".rs": _parse_rust_imports,
    ".go": _parse_go_imports,
    ".java": _parse_java_imports,
    ".kt": _parse_java_imports,
    ".kts": _parse_java_imports,
    ".swift": _parse_swift_imports,
    ".php": _parse_php_imports,
    ".phtml": _parse_php_imports,
    ".cs": _parse_csharp_imports,
    ".c": _parse_c_imports,
    ".h": _parse_c_imports,
    ".cpp": _parse_c_imports,
    ".hpp": _parse_c_imports,
    ".cc": _parse_c_imports,
    ".hh": _parse_c_imports,
}

_ALL_GRAPH_EXTS = set(_IMPORT_EXTRACTORS)


# ── cross-language edge detection ─────────────────────────────────────────

import re as _re

# (pattern, edge_type, target_language_hint)
_CROSS_PATTERNS: list[tuple[str, str, str]] = [
    # Python → others (list arg: capture 2nd element = script)
    (
        r"\bsubprocess\.(?:run|call|Popen|check_output)\s*\(\s*\[[\"'][^\"']+[\"']\s*,\s*[\"']([^\"']+)",
        "subprocess",
        "",
    ),
    # Python → others (string arg: capture the command)
    (
        r"\bsubprocess\.(?:run|call|Popen|check_output)\s*\(\s*[\"']([^\"']+)",
        "subprocess",
        "",
    ),
    (r"\bos\.system\s*\(\s*[\"']([^\"']+)", "subprocess", ""),
    (r"\bctypes\.CDLL\s*\(|ctypes\.cdll\.", "ffi", "c"),
    (r"\bcffi\.FFI\s*\(|cffi\.dlopen\s*\(", "ffi", "c"),
    # JS/TS → others: capture the command (first arg after exec/spawn)
    (
        r"\bchild_process\.(?:exec|spawn|fork|execSync|spawnSync)\s*\(\s*[\"']([^\"']+)",
        "subprocess",
        "",
    ),
    # JS/TS → others: bare exec/spawn (destructured from child_process)
    (r"\b(?:exec|spawn|fork)\s*\(\s*[\"']([^\"']+)", "subprocess", ""),
    # Go → others
    (r"\bexec\.Command\s*\(\s*[\"']([^\"']+)", "subprocess", ""),
    # Rust → C (FFI)
    (r"\bextern\s+\"C\"", "ffi", "c"),
    (r"#\[no_mangle\]", "ffi", "c"),
    # C# → native
    (r"\[DllImport\s*\([\"']([^\"']+)", "ffi", "c"),
    (r"\bProcess\.Start\s*\(\s*[\"']([^\"']+)", "subprocess", ""),
    # Java → native
    (r"\bSystem\.loadLibrary\s*\(", "ffi", "c"),
]


def _lang_from_ext(ext: str) -> str:
    """Map file extension to language identifier."""
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".cs": "csharp",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".hpp": "cpp",
    }.get(ext, "")


def _add_cross_language_edges(
    root: Path,
    graph: dict[str, set[str]],
    edge_types: dict[tuple[str, str], str],
    skip_dirs: set[str],
    all_exts: set[str],
) -> None:
    """Second pass: detect subprocess/FFI edges between files of different languages."""
    import os

    # Extensions to scan for cross-language patterns (wider than the main graph walk)
    cross_exts = all_exts | {".rs", ".c", ".h", ".go", ".java", ".cs", ".cpp", ".hpp"}

    # Build lookup: filename (stem) → [relative paths]
    file_index: dict[str, list[str]] = {}
    for rel_path in graph:
        stem = Path(rel_path).stem
        file_index.setdefault(stem, []).append(rel_path)
        name = Path(rel_path).name
        file_index.setdefault(name, []).append(rel_path)

    # Also index files not in the graph (Rust, C, Go, etc.)
    for dirpath, dirnames, filenames in os.walk(os.fspath(root)):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            ext = Path(fname).suffix
            if ext in cross_exts:
                full = Path(dirpath) / fname
                try:
                    rp = str(full.relative_to(root))
                except ValueError:
                    continue
                if rp not in graph:
                    graph.setdefault(rp, set())
                stem = Path(rp).stem
                file_index.setdefault(stem, []).append(rp)
                file_index.setdefault(Path(rp).name, []).append(rp)

    for dirpath, dirnames, filenames in os.walk(os.fspath(root)):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            ext = Path(fname).suffix
            if ext not in cross_exts:
                continue
            full = Path(dirpath) / fname
            try:
                rel = str(full.relative_to(root))
            except ValueError:
                continue

            src_lang = _lang_from_ext(ext)
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for pattern, edge_type, hint_lang in _CROSS_PATTERNS:
                for m in _re.finditer(pattern, text):
                    found = None
                    if m.groups():
                        raw_cmd = m.group(1)
                        tokens = raw_cmd.split()
                        for token in tokens:
                            t = Path(token).name
                            if not t:
                                continue
                            for candidate in file_index.get(t, []):
                                tgt_lang = _lang_from_ext(Path(candidate).suffix)
                                if tgt_lang and tgt_lang != src_lang:
                                    found = candidate
                                    break
                            if found:
                                break
                            stem = Path(t).stem
                            for candidate in file_index.get(stem, []):
                                tgt_lang = _lang_from_ext(Path(candidate).suffix)
                                if tgt_lang and tgt_lang != src_lang:
                                    found = candidate
                                    break
                            if found:
                                break

                    if found:
                        graph.setdefault(rel, set()).add(found)
                        edge_types[(rel, found)] = edge_type
                    elif hint_lang:
                        virtual = f"[{src_lang}→{hint_lang}]"
                        graph.setdefault(rel, set()).add(virtual)
                        edge_types[(rel, virtual)] = edge_type


# ── graph builder ──────────────────────────────────────────────────────────


def build_graph(
    target_root: Path,
) -> tuple[dict[str, set[str]], dict[tuple[str, str], str]]:
    """Build adjacency and edge types.
    Returns (graph, edge_types) where edge_types maps (from, to) → 'import'|'subprocess'|'ffi'.
    """

    graph: dict[str, set[str]] = {}
    edge_types: dict[tuple[str, str], str] = {}
    skip = {".venv", "venv", "__pycache__", ".git", "node_modules", "dist", "build"}

    for dirpath, dirnames, filenames in _os.walk(_os.fspath(target_root)):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fname in filenames:
            ext = Path(fname).suffix
            if ext not in _ALL_GRAPH_EXTS:
                continue
            full = Path(dirpath) / fname

            try:
                rel = str(full.relative_to(target_root))
            except ValueError:
                continue

            extractor = _IMPORT_EXTRACTORS.get(ext)
            if extractor is None:
                continue

            if ext == ".py":
                resolved = extractor(full)
            elif ext in _JS_EXTENSIONS:
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_js_local(base, spec)
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            elif ext == ".rs":
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_rust_local(base, spec)
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            elif ext == ".go":
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_go_local(base, spec)
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            elif ext == ".java":
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_java_local(base, spec, target_root)
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            elif ext == ".cs":
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_csharp_local(base, spec, target_root)
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            elif ext in (".kt", ".kts"):
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_java_local(base, spec, target_root)
                    if found is None:
                        # Kotlin: same package structure, different extension
                        pkg = spec.replace("static ", "", 1)
                        parts = pkg.split(".")
                        for src_cand in [
                            base,
                            target_root / "src" / "main" / "kotlin",
                            target_root / "src",
                            target_root,
                        ]:
                            if not src_cand.exists():
                                continue
                            c = src_cand.joinpath(*parts)
                            for ext_try in (".kt", ".kts"):
                                cf = c.with_suffix(ext_try)
                                if cf.is_file():
                                    found = str(cf)
                                    break
                            if found:
                                break
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            elif ext == ".swift":
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_swift_local(base, spec, target_root)
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            elif ext in (".php", ".phtml"):
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_php_local(base, spec, target_root)
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            elif ext in _CEXTS:
                raw_specs = extractor(full)
                resolved = set()
                base = full.resolve().parent
                for spec in raw_specs:
                    found = _resolve_c_local(base, spec)
                    if found:
                        try:
                            resolved.add(str(Path(found).relative_to(target_root)))
                        except ValueError:
                            continue
            else:
                resolved = set()

            graph[rel] = resolved
            for tgt in resolved:
                edge_types[(rel, tgt)] = "import"

    # Cross-language edges (subprocess, FFI)
    _add_cross_language_edges(target_root, graph, edge_types, skip, _ALL_GRAPH_EXTS)

    return graph, edge_types


# ── traversal ──────────────────────────────────────────────────────────────


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
            prefix = (
                "    " * (dist - 1) + "┌── "
                if dist == 1
                else "    " * (dist - 1) + "├── "
            )
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
            prefix = (
                "    " * (dist - 1) + "└── "
                if dist == 1
                else "    " * (dist - 1) + "├── "
            )
            lines.append(f"{prefix}{name}")

    return "\n".join(lines)


# ── main entry ─────────────────────────────────────────────────────────────


def run(
    target_root: Path,
    module: str = "",
    forward: int = 2,
    back: int = 2,
    json_out: bool = False,
) -> AuditResult:
    """Main entry point for audit-test graph command."""

    graph, edge_types = build_graph(target_root)
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
                    etype = edge_types.get((src, tgt), "import")
                    edges.append({"from": src, "to": tgt, "type": etype})
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
