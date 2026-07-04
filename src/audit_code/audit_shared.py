"""Shared constants for the audit suite — one source of truth for skip/exclude sets.

Reads .audit-test-ignore from the project root if it exists, merging its
entries with the built-in defaults.
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


def _load_ignore_file() -> set[str]:
    """Read .audit-test-ignore from the project root, return custom entries."""
    ignore_file = Path.cwd() / ".audit-test-ignore"
    if not ignore_file.exists():
        return set()
    entries = set()
    try:
        for line in ignore_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            entries.add(stripped)
    except OSError:
        pass
    return entries


_custom = _load_ignore_file()

SKIP_PARTS = _SKIP_DEFAULTS | _custom
EXCLUDE_DIRS = _EXCLUDE_DEFAULTS | _custom
