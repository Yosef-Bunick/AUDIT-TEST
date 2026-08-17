"""testpaths.py — where does this project's test suite actually live?

The audit used to hardcode ``tests/``.  That is only a guess, and it is wrong
for every repo that keeps its suite somewhere else — a monorepo whose real
suite is ``ledger-core-pkg/backend/tests`` while a stale root ``tests/`` holds
one unrelated file will make S1/S2 triage the WRONG nine errors and make Q5
report "0 executed (0.0%)", which is not a coverage statement at all.

pytest already answers the question, in the project's own config:

    # pyproject.toml
    [tool.pytest.ini_options]
    testpaths = ["ledger-core-pkg/backend/tests", "test_data"]

    # pytest.ini / tox.ini
    [pytest]
    testpaths = tests integration

    # setup.cfg
    [tool:pytest]
    testpaths =
        tests
        integration

So read it.  Precedence follows pytest's own rootdir search order
(pytest.ini wins outright, then pyproject.toml, then tox.ini, then setup.cfg);
the hardcoded ``tests/`` guess is the last resort, not the first.
"""

from __future__ import annotations

import configparser
from pathlib import Path

# Fallback used when a project declares nothing — the historical guess.
DEFAULT_TESTS_DIR = "tests"

# pytest's own config-file precedence for reading ini options at the rootdir.
# (pytest.ini always wins, even when empty; the rest are only consulted when
# they actually carry a pytest section.)
_INI_SOURCES = (
    ("pytest.ini", "pytest"),
    ("pyproject.toml", "tool.pytest.ini_options"),
    ("tox.ini", "pytest"),
    ("setup.cfg", "tool:pytest"),
)


def _split_ini_list(raw: str) -> list[str]:
    """pytest ini 'args' values are whitespace/newline separated."""
    return [tok for tok in raw.replace("\n", " ").split() if tok]


def _from_toml(path: Path) -> list[str] | None:
    """testpaths from [tool.pytest.ini_options]; None if the table is absent."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - py<3.11
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ini = ((data.get("tool") or {}).get("pytest") or {}).get("ini_options")
    if not isinstance(ini, dict):
        return None
    raw = ini.get("testpaths")
    if raw is None:
        return []
    if isinstance(raw, str):
        return _split_ini_list(raw)
    return [str(x) for x in raw]


def _from_ini(path: Path, section: str) -> list[str] | None:
    """testpaths from an ini/cfg section; None if the section is absent."""
    parser = configparser.ConfigParser()
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, configparser.Error):
        return None
    if not parser.has_section(section):
        return None
    return _split_ini_list(parser.get(section, "testpaths", fallback=""))


def declared_testpaths(root: Path) -> list[str]:
    """Raw ``testpaths`` entries declared by *root*'s pytest config.

    Returns [] both when the project declares a pytest config carrying no
    testpaths and when it declares nothing at all; either way the caller falls
    back to ``tests/``.  Entries are returned verbatim (relative to root, as
    pytest interprets them); existence is NOT checked here.
    """
    for filename, section in _INI_SOURCES:
        path = root / filename
        if not path.exists():
            continue
        vals = (
            _from_toml(path)
            if filename == "pyproject.toml"
            else _from_ini(path, section)
        )
        if vals is None:
            continue  # file exists but carries no pytest section — keep looking
        return vals
    return []


def discover_test_dirs(root: Path, override: str | None = None) -> list[Path]:
    """Resolved directories holding *root*'s tests.

    ``override`` (the ``--tests`` flag) short-circuits discovery.  Otherwise the
    project's pytest ``testpaths`` wins; only when nothing is declared — or
    nothing declared exists on disk — do we fall back to ``root/tests``.
    Entries that point at a single file resolve to that file's directory, and
    entries that do not exist are dropped (a stale testpath must not make the
    audit run pytest against a missing target).
    """
    root = Path(root).resolve()
    if override:
        return [(root / override).resolve()]

    dirs: list[Path] = []
    for entry in declared_testpaths(root):
        target = (root / entry).resolve()
        if not target.exists():
            continue
        if target.is_file():
            target = target.parent
        if target not in dirs:
            dirs.append(target)
    if dirs:
        return dirs
    return [(root / DEFAULT_TESTS_DIR).resolve()]


def pytest_targets(root: Path, override: str | None = None) -> list[str]:
    """Path arguments to hand pytest, relative to *root* where possible.

    Relative targets keep pytest's rootdir/inifile detection anchored on the
    project (an absolute path outside the cwd can shift rootdir), and keep the
    audit's reported command line readable.
    """
    root = Path(root).resolve()
    out: list[str] = []
    for d in discover_test_dirs(root, override):
        try:
            out.append(str(d.relative_to(root)).replace("\\", "/"))
        except ValueError:
            out.append(str(d))
    return out or [DEFAULT_TESTS_DIR]
