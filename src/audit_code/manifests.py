"""manifests.py — which packages does this project actually declare?

Used by Q4 (CVE scan) to scope findings to the project's own dependencies.
Get this wrong and the audit falls back to scanning the whole interpreter,
reporting every unrelated package in a shared environment (keras, torch, vllm,
jupyterlab...) as a HIGH finding against the repo under audit.

The old logic took the NEAREST manifest and stopped. In a monorepo whose root
``pyproject.toml`` is tool-config only —

    [tool.mypy]
    [tool.ruff]
    [tool.pytest.ini_options]

— that yields ZERO dependencies, which is indistinguishable from "no manifest"
and triggers the environment fallback. The real manifests were one directory
down, in the packages.

So: find the nearest manifest, and if it declares nothing, keep looking
downward through the sub-packages. Fall back to the environment only when the
tree genuinely declares no dependencies anywhere.

Sources understood: ``[project]`` dependencies + optional-dependencies and
``[tool.poetry.dependencies]``/``group.*.dependencies`` in pyproject.toml,
requirements*.txt, ``[options] install_requires`` + ``[options.extras_require]``
in setup.cfg, and ``[packages]``/``[dev-packages]`` in a Pipfile.
"""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

# How far below the manifest root to look for sub-package manifests.
_MAX_SUBPACKAGE_DEPTH = 3

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "build",
    "dist",
    "site-packages",
    ".eggs",
    "graphify-out",
}

MANIFEST_NAMES = ("pyproject.toml", "setup.cfg", "Pipfile")


def normalize_pkg(name: str) -> str:
    """PEP 503 normalization: lowercase, runs of -_. collapse to -."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _add_spec(names: set[str], spec) -> None:
    m = _NAME_RE.match(str(spec))
    if m:
        names.add(normalize_pkg(m.group(1)))


# ── individual manifest readers (each returns the names it declares) ──────────


def _from_pyproject(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        import tomllib
    except ImportError:  # pragma: no cover - py<3.11
        return names
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return names
    proj = data.get("project") or {}
    specs = list(proj.get("dependencies") or [])
    for extra in (proj.get("optional-dependencies") or {}).values():
        specs.extend(extra or [])
    for spec in specs:
        _add_spec(names, spec)
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for name in poetry.get("dependencies") or {}:
        if name.lower() != "python":
            names.add(normalize_pkg(name))
    for group in (poetry.get("group") or {}).values():
        for name in (group or {}).get("dependencies") or {}:
            if name.lower() != "python":
                names.add(normalize_pkg(name))
    return names


def _from_requirements(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return names
    for line in lines:
        line = line.strip()
        # "-r other.txt", "--index-url ..." are directives, not packages
        if not line or line.startswith(("#", "-")):
            continue
        _add_spec(names, line)
    return names


def _from_setup_cfg(path: Path) -> set[str]:
    names: set[str] = set()
    parser = configparser.ConfigParser()
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, configparser.Error):
        return names
    blocks = []
    if parser.has_option("options", "install_requires"):
        blocks.append(parser.get("options", "install_requires"))
    if parser.has_section("options.extras_require"):
        blocks.extend(v for _k, v in parser.items("options.extras_require"))
    for block in blocks:
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                _add_spec(names, line)
    return names


def _from_pipfile(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        import tomllib
    except ImportError:  # pragma: no cover - py<3.11
        return names
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return names
    for section in ("packages", "dev-packages"):
        for name in data.get(section) or {}:
            names.add(normalize_pkg(name))
    return names


def deps_in_dir(directory: Path) -> set[str]:
    """Every dependency declared by manifests sitting directly in *directory*."""
    names: set[str] = set()
    py = directory / "pyproject.toml"
    if py.exists():
        names |= _from_pyproject(py)
    cfg = directory / "setup.cfg"
    if cfg.exists():
        names |= _from_setup_cfg(cfg)
    pip = directory / "Pipfile"
    if pip.exists():
        names |= _from_pipfile(pip)
    for req in sorted(directory.glob("requirements*.txt")):
        names |= _from_requirements(req)
    return names


def has_manifest(directory: Path) -> bool:
    """True if *directory* holds any dependency manifest file (even an empty one)."""
    if any((directory / n).exists() for n in MANIFEST_NAMES):
        return True
    return any(directory.glob("requirements*.txt"))


# ── discovery ────────────────────────────────────────────────────────────────


def manifest_root(root: Path) -> Path:
    """Nearest manifest-bearing ancestor of *root* (stopping at a .git boundary).

    Audits often target a package subdirectory while the manifests live at the
    project root.
    """
    cur = Path(root).resolve()
    for _ in range(4):
        if has_manifest(cur):
            return cur
        if (cur / ".git").exists() or cur.parent == cur:
            break
        cur = cur.parent
    return Path(root).resolve()


def _subpackage_manifest_dirs(base: Path, max_depth: int) -> list[Path]:
    """Directories at most *max_depth* below *base* that hold a manifest."""
    base = base.resolve()
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        here = Path(dirpath)
        depth = len(here.relative_to(base).parts)
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRS and not d.endswith(".egg-info")
        ]
        if depth >= max_depth:
            dirnames[:] = []
        if here == base:
            continue
        names = set(filenames)
        if names & set(MANIFEST_NAMES) or any(
            f.startswith("requirements") and f.endswith(".txt") for f in names
        ):
            found.append(here)
    return sorted(found)


def declared_dependencies(
    root: Path, max_depth: int = _MAX_SUBPACKAGE_DEPTH
) -> set[str]:
    """Every top-level dependency name (PEP 503 normalized) the project declares.

    Nearest manifest first. If that declares nothing — the tool-config-only
    ``pyproject.toml`` case — sub-package manifests below it are searched too.
    An empty result means the tree genuinely declares no dependencies, and only
    then should a caller fall back to scanning the installed environment.
    """
    base = manifest_root(root)
    names = deps_in_dir(base)
    if names:
        return names
    for sub in _subpackage_manifest_dirs(base, max_depth):
        names |= deps_in_dir(sub)
    return names


def manifest_sources(root: Path, max_depth: int = _MAX_SUBPACKAGE_DEPTH) -> list[Path]:
    """The manifest files that :func:`declared_dependencies` actually drew from.

    Reporting aid — Q4 prints these so "declared dependencies" is auditable
    rather than a claim.
    """
    base = manifest_root(root)
    dirs = (
        [base]
        if deps_in_dir(base)
        else [base, *_subpackage_manifest_dirs(base, max_depth)]
    )
    out: list[Path] = []
    for d in dirs:
        for n in MANIFEST_NAMES:
            if (d / n).exists() and deps_in_dir(d):
                out.append(d / n)
        out.extend(sorted(d.glob("requirements*.txt")))
    return out
