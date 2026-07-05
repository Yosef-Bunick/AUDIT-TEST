"""Shared constants for the audit suite — one source of truth for skip/exclude sets.

Reads .audit-test-ignore from the project root.  Supports #only section:
  #only
  src/foo.py
  tests/test_bar.py
  #only
When a #only block is present, ONLY those paths are audited; everything else is
skipped.  Without #only, normal ignore behaviour applies.
"""

from pathlib import Path

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


def _read_ignore_file() -> tuple[set[str], set[Path] | None]:
    """Read .audit-test-ignore, return (ignore_entries, only_paths_or_None)."""
    ignore_file = Path.cwd() / ".audit-test-ignore"
    if not ignore_file.exists():
        return set(), None
    entries: set[str] = set()
    only_paths: set[Path] = set()
    in_only = False
    try:
        for line in ignore_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().lower()
            if stripped == "#only":
                in_only = not in_only
                continue
            if stripped.startswith("#") or not stripped:
                continue
            if in_only:
                only_paths.add(Path(stripped))
            else:
                entries.add(stripped)
    except OSError:
        pass
    return entries, (only_paths or None)


_custom, _only_paths = _read_ignore_file()

SKIP_PARTS = _SKIP_DEFAULTS | _custom
EXCLUDE_DIRS = _EXCLUDE_DEFAULTS | _custom
ONLY_PATHS = _only_paths


def should_audit(path: Path, *, relative_to: Path | None = None) -> bool:
    """Return True if *path* should be audited.

    Honours SKIP_PARTS.  If the ignore file has a #only block, only those
    paths pass.  Callers that previously looked up SKIP_PARTS directly can
    call this instead.
    """
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    if ONLY_PATHS is not None:
        rel = path.relative_to(relative_to) if relative_to else path
        return any(only == rel or str(only) in str(rel) for only in ONLY_PATHS)
    return True
