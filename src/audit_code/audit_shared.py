"""Shared constants for the audit suite — one source of truth for skip/exclude sets.

Reads .audit-test-ignore from the project root.  Supports named #only groups:
  #only
  fast=[main.py,cli.py]
  slow=[src/quality.py] /mnt/c/other  | full quality sweep
  #only
Format per line: name=[file,...] [/path] [| description]
"""

import os
import sys
from collections import namedtuple
from pathlib import Path

Group = namedtuple("Group", "files path description")


def force_utf8_streams() -> None:
    """Make stdout AND stderr emit UTF-8 (replace on error), on every platform.

    Windows consoles and pipes default to cp1252/cp437, which raise
    UnicodeEncodeError on the audit's glyphs (🐇, ═, ✓, ✨).  macOS/Linux are
    almost always UTF-8 already, so this is a harmless no-op there.  Guarded
    because captured/replaced streams (pytest, StringIO) lack reconfigure().
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


# Environment additions that force a spawned Python worker to start with UTF-8
# stdio regardless of the host locale — the child sets its pipe encoding at
# interpreter startup, before any reconfigure() call can run.
UTF8_ENV = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def utf8_subprocess_env(base: dict | None = None) -> dict:
    """Return a copy of *base* (default os.environ) with UTF-8 stdio forced."""
    return {**(os.environ if base is None else base), **UTF8_ENV}


# ── Built-in defaults ────────────────────────────────────────────────────────

_SKIP_DEFAULTS = {
    "graphify-out",
    "bunick-ai-desktop",
    "__pycache__",
    ".venv",
    "venv",
    "sandbox",
    "logs",
    "eval_results",
    "golden_tasks",
    "fixes and info",
    ".git",
    "node_modules",
}

_EXCLUDE_DEFAULTS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "graphify-out",
    "sandbox",
    ".ruff_cache",
    ".pytest_cache",
    "scratch",
    "dist",
    "build",
    ".eggs",
}


def _parse_group(
    line: str, default_path: str
) -> tuple[str, Group] | None:  # audit: ok (parse helper)
    """Parse 'fast=[main.py,cli.py] /x  | desc' into (name, Group)."""
    line = line.strip()
    # description (strip first so it can't corrupt the file-list terminator)
    desc = ""
    if "|" in line:
        line, desc = line.split("|", 1)
        desc = desc.strip()
    # name=[...]
    if "=[" not in line:
        return None
    name, rest = line.split("=[", 1)
    name = name.strip()
    # Everything up to the closing ']' is the file list; whatever follows is the
    # optional path, kept verbatim so a leading '/' or Windows 'C:' survives.
    if "]" not in rest:
        return None
    files_str, after = rest.split("]", 1)
    files = [f.strip() for f in files_str.split(",") if f.strip()]
    path = after.strip() or default_path
    return name, Group(tuple(files), path, desc)


def _read_ignore_file() -> tuple[set[str], str, dict[str, Group]]:
    """Read .audit-test-ignore, return (skip_entries, default_path, groups)."""
    ignore_file = Path.cwd() / ".audit-test-ignore"
    if not ignore_file.exists():
        return set(), "", {}
    entries: set[str] = set()
    default_path = ""
    groups: dict[str, Group] = {}
    in_only = False
    try:
        for line in ignore_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            lower = stripped.lower()
            if in_only:
                if lower == "#only":
                    in_only = False
                    continue
                if not stripped or lower.startswith("#"):
                    continue
                parsed = _parse_group(stripped, default_path)
                if parsed:
                    groups[parsed[0]] = parsed[1]
            else:
                if lower == "#only":
                    in_only = True
                    continue
                if lower.startswith("#path"):
                    default_path = (
                        stripped.split(None, 1)[1].strip() if " " in stripped else ""
                    )
                    continue
                if stripped.startswith("#") or not stripped:
                    continue
                entries.add(stripped)
    except OSError:
        pass
    return entries, default_path, groups


_custom, _default_path, _groups = _read_ignore_file()

SKIP_PARTS = _SKIP_DEFAULTS | _custom
EXCLUDE_DIRS = _EXCLUDE_DEFAULTS | _custom
GROUPS = _groups
DEFAULT_PATH = _default_path


# Active focus — set by the 'focus' command via env var.
# Read at CALL time, not import time: the CLI sets AUDIT_FOCUS_GROUP inside
# _focus_run(), which runs long after this module is imported (cli -> runner ->
# quality/suite pull audit_shared in at startup).  Caching it at import made
# focus a silent no-op for every in-process consumer.
def _active_paths() -> set[str] | None:
    """Return filenames of the active group, or None if no focus."""
    active = os.environ.get("AUDIT_FOCUS_GROUP", "")
    if not active or active not in GROUPS:
        return None
    return set(GROUPS[active].files)


def should_audit(
    path: Path, *, relative_to: Path | None = None
) -> bool:  # audit: ok (used by audit modules)
    """Return True if *path* should be audited.

    Honours SKIP_PARTS.  If an active focus group is set, only its files pass.
    """
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    paths = _active_paths()
    if paths is not None:
        return path.name in paths
    return True
