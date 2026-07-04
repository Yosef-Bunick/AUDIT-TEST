"""Package configuration — constants + TOML reader for audit-code.toml."""

from pathlib import Path

# ── Tool constants (imported by quality.py, suite.py) ──

TOOL_TIMEOUT = 600
DOC_THRESHOLD_PCT = 0
MIN_FLAG_BODY_LINES = 2
MAX_PER_FILE_CHECKS = 400
FULL_SUITE_TIMEOUT = 900
SOLO_TIMEOUT = 180
MAX_SOLO_RERUNS = 10

# Directories never scanned by any audit.
EXCLUDE_DIRS = {
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

# Additional build-output dirs skipped by language adapters (kept separate so
# quality.py's Python scan behavior is unchanged — e.g. a Python bin/ script
# dir must still be scanned there).
ADAPTER_EXCLUDE_DIRS = EXCLUDE_DIRS | {
    "target",  # rust / maven
    "vendor",  # go
    "bin",  # dotnet
    "obj",  # dotnet
    "out",
    ".gradle",
    ".tox",
    ".mypy_cache",
    ".idea",
    ".vscode",
}

# ── Project config defaults ──

DEFAULTS = {
    "audit": {
        # Empty list = auto-detect every supported language in the target.
        # Set explicitly (e.g. ["python", "go"]) to restrict the audit.
        "languages": [],
        "profiles": [],
    },
    "paths": {
        "source": ["src"],
        "tests": ["tests"],
        "exclude": [".git", ".venv", "node_modules", "dist", "build"],
    },
    "gate": {
        "mutation_kill_percent": 60,
        "require_changed_line_coverage": True,
        "baseline": "HEAD",
    },
    "reporting": {
        "json": "",
        "sarif": "",
        "junit": "",
    },
}


def load_project_config(target_root: Path, config_path: str = "") -> dict:
    """Load audit-code.toml from the target project, merged with defaults."""
    cfg = _deep_copy(DEFAULTS)
    path = Path(config_path) if config_path else target_root / "audit-code.toml"
    if not path.exists():
        return cfg
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return cfg
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    _merge(cfg, data)
    return cfg


def _merge(base: dict, overrides: dict) -> None:
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _deep_copy(d: dict) -> dict:
    import copy

    return copy.deepcopy(d)
