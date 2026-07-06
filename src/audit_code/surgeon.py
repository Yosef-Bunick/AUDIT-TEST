#!/usr/bin/env python3
"""
surgeon — surgical file editor for agent workflows.

Sits between "found the problem" (audit-test) and "knows the fix" (PHL DLL).
Applies precise edits by line number — no text matching, no escaping fragility.

Commands:
  surgeon replace <file> <start>[:<end>] <content>    Replace lines
  surgeon insert <file> <line> <content>              Insert after line
  surgeon batch <file> <fixes.json>                   Batch apply fixes
  surgeon dry-run <file> <start>[:<end>] <content>    Preview without writing
  surgeon copy <src> <start>:<end> <dest> <after>     Copy lines across files
  surgeon replace-cross <src> <s>:<e> <dest> <s>:<e>  Overwrite dest range w/ src
  surgeon port <src> <dest> <function>                Port a function + imports

Examples:
  surgeon replace quality.py 170:170 "(AttributeError, OSError)"
  surgeon insert quality.py 8 "import shutil"
  surgeon batch fixes.json
  surgeon copy old.py 750:800 new.py 527
  surgeon port makeup.3.28/engine.py makeup.233am/engine.py _gen_eyeshadow
"""

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional


def parse_range(spec: str) -> tuple[int, Optional[int]]:
    """Parse '15' or '15:20' into (start, end). 1-indexed."""
    if ":" in spec:
        a, b = spec.split(":", 1)
        return int(a), int(b)
    return int(spec), int(spec)


def replace_lines(
    path: Path,
    start: int,
    end: Optional[int],
    content: str,
    *,
    dry_run: bool = False,
) -> str:
    """Replace lines start..end with content. 1-indexed, inclusive."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    end = end or start

    if start < 1 or start > len(lines):
        raise ValueError(f"start={start} out of range (file has {len(lines)} lines)")
    if end < start:
        raise ValueError(f"end={end} < start={start}")

    # Capture target line's indentation for auto-indenting
    target_indent = "".join(lines[start - 1 : start]) if lines else ""

    # Build replacement lines
    if content.endswith("\n"):
        content = content[:-1]
    new_lines = content.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    # Auto-indent to match target if content is plain text (no indentation)
    if all(
        not ln.strip() or ln[0] not in (" ", "\t") for ln in new_lines if ln.strip()
    ):
        new_lines = [
            (
                target_indent[: len(target_indent) - len(target_indent.lstrip())] + ln
                if ln.strip()
                else ln
            )
            for ln in new_lines
        ]

    before = lines[: start - 1]
    after = lines[end:]

    if dry_run:
        old_block = "".join(lines[start - 1 : end])
        new_block = "".join(new_lines)
        return f"--- {path}:{start}-{end}\n-{old_block}+{new_block}"

    result = before + new_lines + after
    path.write_text("".join(result), encoding="utf-8")
    return f"replaced {path}:{start}-{end} ({len(new_lines)} lines)"


def insert_after(
    path: Path,
    line: int,
    content: str,
    *,
    dry_run: bool = False,
) -> str:
    """Insert content after line. 0 = insert at top."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    if line < 0 or line > len(lines):
        raise ValueError(f"line={line} out of range (file has {len(lines)} lines)")

    if content.endswith("\n"):
        content = content[:-1]
    new_lines = content.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    if dry_run:
        return f"--- {path}:{line}\n+{''.join(new_lines)}"

    result = lines[:line] + new_lines + lines[line:]
    path.write_text("".join(result), encoding="utf-8")
    return f"inserted after {path}:{line} ({len(new_lines)} lines)"


def apply_fixes(path: Path, fixes: list[dict], *, dry_run: bool = False) -> list[str]:
    """Apply multiple fixes to the same file. Each fix: {start, end?, content}."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    results = []

    # Apply from bottom to top so line numbers stay valid
    for fix in sorted(fixes, key=lambda f: -f["start"]):
        start = fix["start"]
        end = fix.get("end", start)
        content = fix["content"]

        if dry_run:
            old = "".join(lines[start - 1 : end])
            results.append(f"--- {path}:{start}-{end}\n-{old}+{content}")
            continue

        if content.endswith("\n"):
            content = content[:-1]
        new_lines = content.splitlines(keepends=True)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        lines[start - 1 : end] = new_lines
        results.append(f"  {path}:{start}-{end} → {len(new_lines)} lines")

    if not dry_run:
        path.write_text("".join(lines), encoding="utf-8")

    return results


# ── cross-file operations (Surgeon v2) ───────────────────────────────────────


def copy_lines(
    src: Path,
    start: int,
    end: int,
    dest: Path,
    after_line: int,
    *,
    dry_run: bool = False,
) -> str:
    """Copy lines start..end (1-indexed, inclusive) from *src* into *dest*.

    The block is inserted after *after_line* in dest (0 = insert at top).
    """
    src_lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    if start < 1 or end > len(src_lines) or end < start:
        raise ValueError(
            f"range {start}:{end} out of bounds (src has {len(src_lines)} lines)"
        )
    block = src_lines[start - 1 : end]
    if block and not block[-1].endswith("\n"):
        block[-1] += "\n"

    dest_lines = dest.read_text(encoding="utf-8").splitlines(keepends=True)
    if after_line < 0 or after_line > len(dest_lines):
        raise ValueError(
            f"after_line={after_line} out of range (dest has {len(dest_lines)} lines)"
        )
    if dry_run:
        return (
            f"--- would copy {src}:{start}-{end} → {dest}:{after_line + 1}\n"
            + "".join(block)
        )
    dest_lines[after_line:after_line] = block
    dest.write_text("".join(dest_lines), encoding="utf-8")
    return f"copied {src}:{start}-{end} → {dest}:{after_line + 1} ({len(block)} lines)"


def replace_lines_cross(
    src: Path,
    src_start: int,
    src_end: int,
    dest: Path,
    dest_start: int,
    dest_end: int,
    *,
    dry_run: bool = False,
) -> str:
    """Replace dest lines dest_start..dest_end with src lines src_start..src_end."""
    src_lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    if src_start < 1 or src_end > len(src_lines) or src_end < src_start:
        raise ValueError(
            f"src range {src_start}:{src_end} out of bounds "
            f"(src has {len(src_lines)} lines)"
        )
    block = src_lines[src_start - 1 : src_end]
    if block and not block[-1].endswith("\n"):
        block[-1] += "\n"

    dest_lines = dest.read_text(encoding="utf-8").splitlines(keepends=True)
    if dest_start < 1 or dest_end > len(dest_lines) or dest_end < dest_start:
        raise ValueError(
            f"dest range {dest_start}:{dest_end} out of bounds "
            f"(dest has {len(dest_lines)} lines)"
        )
    if dry_run:
        old = "".join(dest_lines[dest_start - 1 : dest_end])
        return f"--- {dest}:{dest_start}-{dest_end}\n-{old}+{''.join(block)}"
    dest_lines[dest_start - 1 : dest_end] = block
    dest.write_text("".join(dest_lines), encoding="utf-8")
    return (
        f"replaced {dest}:{dest_start}-{dest_end} with {src}:{src_start}-{src_end} "
        f"({len(block)} lines)"
    )


def _iter_py_files(src: Path):
    """Yield *.py under *src* (or just *src* if it is a file), skipping junk."""
    if src.is_file():
        yield src
        return
    skip = {"__pycache__", ".venv", "venv", ".git", "node_modules"}
    for p in src.rglob("*.py"):
        if not any(part in skip for part in p.parts):
            yield p


def _import_bindings(node) -> list[str]:
    """Names an import statement binds into scope (e.g. 'np', 'y')."""
    names: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            names.append(alias.asname or alias.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            names.append(alias.asname or alias.name)
    return names


def _names_used(func_node: ast.AST) -> set[str]:
    """Every bare name and attribute-root referenced inside a function body."""
    used: set[str] = set()
    for n in ast.walk(func_node):
        if isinstance(n, ast.Name):
            used.add(n.id)
        elif isinstance(n, ast.Attribute):
            root = n
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                used.add(root.id)
    return used


def find_function(src: Path, name: str) -> dict | None:
    """Locate a top-level function *name* under *src* (file or project dir).

    Returns a dict with the defining file, 1-indexed line span (decorators
    included), the exact source block, and the parsed source/AST — or None.
    """
    for f in _iter_py_files(src):
        text = f.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                start = node.lineno
                if node.decorator_list:
                    start = min(start, min(d.lineno for d in node.decorator_list))
                end = getattr(node, "end_lineno", node.lineno)
                lines = text.splitlines(keepends=True)
                block = "".join(lines[start - 1 : end])
                return {
                    "file": f,
                    "name": name,
                    "start": start,
                    "end": end,
                    "block": block,
                    "text": text,
                    "tree": tree,
                    "node": node,
                }
    return None


def _needed_imports(found: dict, dest_text: str) -> list[str]:
    """Import statements the ported function needs but dest lacks."""
    used = _names_used(found["node"])
    dest_bindings: set[str] = set()
    try:
        dest_tree = ast.parse(dest_text)
    except (SyntaxError, ValueError):
        dest_tree = None
    if dest_tree is not None:
        for node in ast.walk(dest_tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                dest_bindings.update(_import_bindings(node))

    needed: list[str] = []
    seen: set[str] = set()
    for node in found["tree"].body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        bindings = _import_bindings(node)
        if not any(b in used for b in bindings):
            continue
        if all(b in dest_bindings for b in bindings):
            continue
        segment = ast.get_source_segment(found["text"], node)
        if segment and segment not in seen:
            needed.append(segment)
            seen.add(segment)
    return needed


def _import_insert_line(dest_text: str) -> int:
    """0-indexed line to insert new imports after (last import, else docstring)."""
    try:
        tree = ast.parse(dest_text)
    except (SyntaxError, ValueError):
        return 0
    after = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            after = getattr(node, "end_lineno", node.lineno)
        elif (
            after == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            after = getattr(node, "end_lineno", node.lineno)
    return after


def port_feature(
    src: Path,
    dest: Path,
    function_name: str,
    *,
    dry_run: bool = False,
    fmt: bool = True,
) -> dict:
    """Copy *function_name* (and any imports it needs) from *src* into *dest*.

    *src* may be a single file or a project directory to search.  Imports the
    function references that are missing in *dest* are inserted near the top;
    the function body is appended at the end.  Returns a summary dict.
    """
    found = find_function(src, function_name)
    if found is None:
        return {"error": f"function {function_name!r} not found under {src}"}

    dest_text = dest.read_text(encoding="utf-8")
    imports = _needed_imports(found, dest_text)

    if dry_run:
        return {
            "function": function_name,
            "source_file": str(found["file"]),
            "lines": f"{found['start']}-{found['end']}",
            "imports_needed": imports,
            "dry_run": True,
        }

    dest_lines = dest_text.splitlines(keepends=True)
    if imports:
        at = _import_insert_line(dest_text)
        block = [line + "\n" for line in imports]
        dest_lines[at:at] = block

    body = found["block"]
    if not body.endswith("\n"):
        body += "\n"
    tail = dest_lines[-1] if dest_lines else ""
    sep = "\n\n" if tail.endswith("\n") else "\n\n\n"
    dest_lines.append(sep + body)
    dest.write_text("".join(dest_lines), encoding="utf-8")

    if fmt:
        _run_formatter(dest)

    return {
        "function": function_name,
        "source_file": str(found["file"]),
        "lines": f"{found['start']}-{found['end']}",
        "imports_added": imports,
        "dest": str(dest),
    }


def _run_formatter(path: Path) -> bool:
    """Run black + isort if available. Returns True if formatted."""
    formatted = False
    for cmd in (
        ["black", "--quiet", str(path)],
        ["ruff", "format", str(path)],
        ["ruff", "check", "--fix", "--select", "I", str(path)],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                formatted = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return formatted


def _force_utf8_output() -> None:
    """Emit UTF-8 on stdout/stderr so status glyphs (→, ✨) never crash cp1252."""
    from audit_code.audit_shared import force_utf8_streams

    force_utf8_streams()


def main():
    _force_utf8_output()
    ap = argparse.ArgumentParser(description="surgical file editor")
    ap.add_argument("--no-format", action="store_true", help="skip auto-formatting")
    sp = ap.add_subparsers(dest="cmd")

    # fix (alias for replace — simpler name for agent usage)
    fp = sp.add_parser("fix")
    fp.add_argument("file")
    fp.add_argument("range", help="line or start:end (1-indexed)")
    fp.add_argument("content")

    # replace
    rp = sp.add_parser("replace")
    rp.add_argument("file")
    rp.add_argument("range", help="start:end or start (1-indexed)")
    rp.add_argument("content")

    # insert
    ip = sp.add_parser("insert")
    ip.add_argument("file")
    ip.add_argument("line", type=int)
    ip.add_argument("content")

    # batch — each fix has {file, start, end?, content}
    bp = sp.add_parser("batch")
    bp.add_argument(
        "fixes_json", help="JSON file with fixes [{file, start, end?, content}]"
    )

    # dry-run
    dp = sp.add_parser("dry-run")
    dp.add_argument("file")
    dp.add_argument("range")
    dp.add_argument("content", nargs="?")

    # copy — cross-file line copy
    cp = sp.add_parser("copy")
    cp.add_argument("src")
    cp.add_argument("range", help="src start:end (1-indexed)")
    cp.add_argument("dest")
    cp.add_argument("after_line", type=int, help="insert after this dest line (0=top)")

    # replace-cross — overwrite a dest range with a src range
    xp = sp.add_parser("replace-cross")
    xp.add_argument("src")
    xp.add_argument("src_range", help="src start:end (1-indexed)")
    xp.add_argument("dest")
    xp.add_argument("dest_range", help="dest start:end to overwrite")

    # port — copy a function + the imports it needs across files/projects
    pp = sp.add_parser("port")
    pp.add_argument("src", help="source file or project directory to search")
    pp.add_argument("dest", help="destination file")
    pp.add_argument("function")
    pp.add_argument(
        "--dry-run", action="store_true", help="preview without writing dest"
    )

    args = ap.parse_args()

    try:
        if args.cmd in ("fix", "replace", "dry-run"):
            dry = args.cmd == "dry-run"
            path = Path(args.file)
            start, end = parse_range(args.range)
            content = args.content or sys.stdin.read()
            result = replace_lines(path, start, end, content, dry_run=dry)
            print(result)

        elif args.cmd == "insert":
            path = Path(args.file)
            result = insert_after(path, args.line, args.content)
            print(result)

        elif args.cmd == "batch":
            fixes_path = Path(args.fixes_json)
            data = json.loads(fixes_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("fixes", data.get("changes", []))
            if isinstance(data, str):
                data = json.loads(data)
            # Group fixes by file
            by_file: dict[str, list[dict]] = {}
            for fix in data:
                fp = fix["file"]
                by_file.setdefault(fp, []).append(fix)
            total = 0
            for fpath, fixes in by_file.items():
                results = apply_fixes(Path(fpath), fixes)
                for r in results:
                    print(r)
                total += len(results)
                # Auto-format
                if not args.no_format:
                    formatted = _run_formatter(Path(fpath))
                    if formatted:
                        print(f"  formatted {Path(fpath).name}")
            print(f"applied {total} fixes across {len(by_file)} files")

        elif args.cmd == "copy":
            start, end = parse_range(args.range)
            result = copy_lines(
                Path(args.src), start, end, Path(args.dest), args.after_line
            )
            print(result)
            if not args.no_format and _run_formatter(Path(args.dest)):
                print(f"  formatted {Path(args.dest).name}")

        elif args.cmd == "replace-cross":
            ss, se = parse_range(args.src_range)
            ds, de = parse_range(args.dest_range)
            result = replace_lines_cross(
                Path(args.src), ss, se, Path(args.dest), ds, de
            )
            print(result)
            if not args.no_format and _run_formatter(Path(args.dest)):
                print(f"  formatted {Path(args.dest).name}")

        elif args.cmd == "port":
            result = port_feature(
                Path(args.src),
                Path(args.dest),
                args.function,
                dry_run=args.dry_run,
                fmt=not args.no_format,
            )
            if "error" in result:
                print(f"error: {result['error']}", file=sys.stderr)
                sys.exit(1)
            print(json.dumps(result, indent=2))

    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    # Auto-format after single-file edits
    if not args.no_format and args.cmd in ("fix", "replace", "insert"):
        path = Path(args.file)
        formatted = _run_formatter(path)
        if formatted:
            print(f"  formatted {path.name}")


if __name__ == "__main__":
    main()
