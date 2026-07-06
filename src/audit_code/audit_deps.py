"""Dependency scanner — auto-generates .requirements from the codebase.

Run this from any project root and it updates .requirements in-place:
  - Adds new third-party packages found in imports
  - Removes entries whose imports no longer exist
  - Preserves manual entries (Python version, system deps, comments)

Fast (~50ms for 60 files). No AST — just text scanning.

Usage:
    python audit/deps.py               # update .requirements
    python audit/deps.py --print       # print to stdout
"""

import os
import sys
from pathlib import Path

# ── Auto-detect project root (parent of the directory containing this script) ──
ROOT = Path(__file__).resolve().parent.parent.parent
# Allow --path override for audit-code wrapper
for _i, _a in enumerate(sys.argv):
    if _a == "--path" and _i + 1 < len(sys.argv):
        ROOT = Path(sys.argv[_i + 1]).resolve()
        break

# ── Known pip package name overrides (Python import name → pip install name) ──
_PIP_NAMES = {
    "aiohttp_cors": "aiohttp-cors",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "ddgs": "duckduckgo_search",
    "dotenv": "python-dotenv",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}

# ── Internal project packages (top-level dirs / modules — never third-party) ──
# Auto-detected: any directory in ROOT that contains an __init__.py or is a known
# project source dir. You can manually add entries here for false positives.
_INTERNAL = {
    "agents",
    "agent_logging",
    "audit",
    "config",
    "dll",
    "engine",
    "harness",
    "memory",
    "observability",
    "prompts",
    "runtime",
    "safety",
    "tools",
    # Single-file modules at project root (no __init__.py)
    "run_entry",
    "run_loop",
    "router",
    "sandbox",
    "checkpoint",
    "conftest",
    "hash_lookup",
}

# Reverse: pip name → import name (for display)
_IMPORT_OF = {v: k for k, v in _PIP_NAMES.items()}

# ── Python stdlib (3.12) ──
_STDLIB = {
    "abc",
    "aifc",
    "argparse",
    "array",
    "ast",
    "asynchat",
    "asyncio",
    "asyncore",
    "atexit",
    "audioop",
    "base64",
    "bdb",
    "binascii",
    "binhex",
    "bisect",
    "builtins",
    "bz2",
    "calendar",
    "cgi",
    "cgitb",
    "chunk",
    "cmath",
    "cmd",
    "code",
    "codecs",
    "codeop",
    "collections",
    "colorsys",
    "compileall",
    "concurrent",
    "configparser",
    "contextlib",
    "contextvars",
    "copy",
    "copyreg",
    "cProfile",
    "crypt",
    "csv",
    "ctypes",
    "curses",
    "dataclasses",
    "datetime",
    "dbm",
    "decimal",
    "difflib",
    "dis",
    "distutils",
    "doctest",
    "email",
    "encodings",
    "enum",
    "errno",
    "faulthandler",
    "fcntl",
    "filecmp",
    "fileinput",
    "fnmatch",
    "formatter",
    "fractions",
    "ftplib",
    "functools",
    "gc",
    "getopt",
    "getpass",
    "gettext",
    "glob",
    "grp",
    "gzip",
    "hashlib",
    "heapq",
    "hmac",
    "html",
    "http",
    "idlelib",
    "imaplib",
    "imghdr",
    "imp",
    "importlib",
    "inspect",
    "io",
    "ipaddress",
    "itertools",
    "json",
    "keyword",
    "lib2to3",
    "linecache",
    "locale",
    "logging",
    "lzma",
    "mailbox",
    "mailcap",
    "marshal",
    "math",
    "mimetypes",
    "mmap",
    "modulefinder",
    "multiprocessing",
    "netrc",
    "nis",
    "nntplib",
    "numbers",
    "operator",
    "optparse",
    "os",
    "ossaudiodev",
    "parser",
    "pathlib",
    "pdb",
    "pickle",
    "pickletools",
    "pipes",
    "pkgutil",
    "platform",
    "plistlib",
    "poplib",
    "posix",
    "posixpath",
    "pprint",
    "profile",
    "pstats",
    "pty",
    "pwd",
    "py_compile",
    "pyclbr",
    "pydoc",
    "queue",
    "quopri",
    "random",
    "re",
    "readline",
    "reprlib",
    "resource",
    "rlcompleter",
    "runpy",
    "sched",
    "secrets",
    "select",
    "selectors",
    "shelve",
    "shlex",
    "shutil",
    "signal",
    "site",
    "smtpd",
    "smtplib",
    "sndhdr",
    "socket",
    "socketserver",
    "sqlite3",
    "ssl",
    "stat",
    "statistics",
    "string",
    "stringprep",
    "struct",
    "subprocess",
    "sunau",
    "symtable",
    "sys",
    "sysconfig",
    "syslog",
    "tabnanny",
    "tarfile",
    "telnetlib",
    "tempfile",
    "termios",
    "test",
    "textwrap",
    "threading",
    "time",
    "timeit",
    "tkinter",
    "token",
    "tokenize",
    "trace",
    "traceback",
    "tracemalloc",
    "tty",
    "turtle",
    "turtledemo",
    "types",
    "typing",
    "unicodedata",
    "unittest",
    "urllib",
    "uu",
    "uuid",
    "venv",
    "warnings",
    "wave",
    "weakref",
    "webbrowser",
    "winreg",
    "winsound",
    "wsgiref",
    "xdrlib",
    "xml",
    "xmlrpc",
    "zipapp",
    "zipfile",
    "zipimport",
    "zlib",
    "_thread",
    "__future__",
}

# ── Auto-detect internal packages from project layout ──
_AUTO_INTERNAL = {
    _entry.name
    for _entry in ROOT.iterdir()
    if _entry.is_dir() and (_entry / "__init__.py").exists()
}
_INTERNAL = _INTERNAL | _AUTO_INTERNAL


def _is_external(mod: str) -> bool:
    top = mod.split(".")[0].split("[")[0]  # strip extras like "foo[bar]"
    return bool(
        top
        and top.isidentifier()
        and top not in _STDLIB
        and top not in _INTERNAL
        and not top.startswith("test")
    )


def _collect_imports() -> dict[str, list[str]]:
    """Scan all .py files, return {pip_name: [file, ...]}."""
    found: dict[str, set[str]] = {}
    skip = {
        "__pycache__",
        ".venv",
        "venv",
        ".git",
        "node_modules",
        "graphify-out",
        "dist",
        "build",
        ".hermes",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "eggs",
        "*.egg-info",
    }

    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Prune skip dirs in-place so we never descend into them
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]

        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = Path(dirpath) / fname
            rel = str(full.relative_to(ROOT))

            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for line in text.splitlines():
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("@"):
                    continue

                if s.startswith("import "):
                    for part in s[7:].split("#")[0].split(","):
                        mod = part.strip().split(" as ")[0].split(".")[0]
                        if _is_external(mod):
                            pkg = _PIP_NAMES.get(mod, mod)
                            found.setdefault(pkg, set()).add(rel)

                elif s.startswith("from "):
                    rest = s[5:].split("#")[0].strip()
                    if " import " not in rest:
                        continue
                    mod = rest.split(" import ")[0].strip().lstrip(".").split(".")[0]
                    if _is_external(mod):
                        pkg = _PIP_NAMES.get(mod, mod)
                        found.setdefault(pkg, set()).add(rel)

    return {k: sorted(v) for k, v in found.items()}


def _read_requirements() -> list[str]:
    """Read existing .requirements, preserving manual entries."""
    path = ROOT / ".requirements"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _write_requirements(scanned: dict[str, list[str]], preserved: list[str]):
    """Write .requirements: manual header + auto-scanned pip section."""
    lines = []

    # ── Preserved lines (manual entries, comments, headers) ──
    in_pip_section = False
    for line in preserved:
        s = line.strip()
        if s == "# --- pip packages (auto-generated by audit/deps.py) ---":
            in_pip_section = True
            continue
        if s == "# --- end pip ---":
            in_pip_section = False
            continue
        if in_pip_section:
            continue  # skip old auto-generated entries
        lines.append(line)

    # ── Ensure there's a trailing newline before the pip section ──
    if lines and lines[-1] != "":
        lines.append("")

    # ── Auto-generated pip section ──
    lines.append("# --- pip packages (auto-generated by audit/deps.py) ---")
    if scanned:
        for pkg in sorted(scanned):
            files = scanned[pkg]
            imp = _IMPORT_OF.get(pkg, pkg)  # import name (what you write in Python)
            # The PIP name leads the line — this file must be installable
            # (`pip install -r`); writing the import name (`PIL`, `dotenv`)
            # would install the wrong packages. Import name goes in the comment.
            hint = f"  # import: {imp};" if imp != pkg else "  #"
            lines.append(
                f"{pkg}{hint} used by: {', '.join(files[:4])}"
                f"{' +' + str(len(files)-4) + ' more' if len(files) > 4 else ''}"
            )
    else:
        lines.append("# (none — all imports are stdlib or internal)")
    lines.append("# --- end pip ---")

    (ROOT / ".requirements").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():  # audit: ok (CLI entry point)
    scanned = _collect_imports()
    existing = _read_requirements()

    if "--print" in sys.argv:
        print(f"Python {sys.version_info.major}.{sys.version_info.minor}")
        for pkg in sorted(scanned):
            print(f"  {pkg}")
        return

    _write_requirements(scanned, existing)

    print(f"[deps] .requirements updated ({len(scanned)} pip packages)")
    for pkg in sorted(scanned):
        files = scanned[pkg]
        print(f"  {pkg:<30} {len(files)} file{'s' if len(files) > 1 else ''}")


if __name__ == "__main__":
    main()
