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

Examples:
  surgeon replace quality.py 170:170 "(AttributeError, OSError)"
  surgeon insert quality.py 8 "import shutil"
  surgeon batch fixes.json
"""

import argparse
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


def main():
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
