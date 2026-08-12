"""Shared constants for the audit suite — one source of truth for skip/exclude sets.

Reads .audit-test-ignore from the project root.  Supports named #only groups:
  #only
  fast=[main.py,cli.py]
  slow=[src/quality.py] /mnt/c/other  | full quality sweep
  #only
Format per line: name=[file,...] [/path] [| description]

Also supports a project-level source-encoding declaration:
  #encoding utf-8
used by the `check` command to verify every file decodes under that codec.
"""

import codecs
import io
import os
import sys
import threading
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path

Group = namedtuple("Group", "files path description")

# Encoding a scanned project's source is expected to be in.  Overridable per
# target via a `#encoding <name>` line in that project's .audit-test-ignore.
DEFAULT_ENCODING = "utf-8"


def normalize_encoding(raw: str) -> str:
    """Validate and canonicalize an encoding name (e.g. 'UTF-16' -> 'utf-16').

    Accepts spaces so multi-word CLI args like 'GB 18030' resolve to 'gb18030'.
    Raises LookupError if the name is not a known codec.
    """
    candidate = raw.strip()
    try:
        return codecs.lookup(candidate).name
    except LookupError:
        return codecs.lookup(candidate.replace(" ", "")).name


def configured_encoding(root: Path | None = None) -> str:
    """Return the `#encoding` declared in <root>/.audit-test-ignore, else utf-8.

    Read from the *target* project's ignore file (default cwd) so scanning an
    external repo honours that repo's declared source encoding.
    """
    base = Path.cwd() if root is None else root
    try:
        for line in (
            (base / ".audit-test-ignore").read_text(encoding="utf-8").splitlines()
        ):
            s = line.strip()
            if s.lower().startswith("#encoding"):
                parts = s.split(None, 1)
                if len(parts) > 1 and parts[1].strip():
                    return parts[1].strip()
    except OSError:
        pass
    return DEFAULT_ENCODING


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


class _ThreadStdoutRouter(io.TextIOBase):
    """sys.stdout proxy that routes writes to a per-thread target.

    contextlib.redirect_stdout swaps the PROCESS-GLOBAL sys.stdout, so when a
    background thread's capture exits it restores the console while another
    thread's capture is still active — that thread's output silently leaks to
    the console and its buffer stays empty (the intermittent "phd/wiring CRASH,
    no SUMMARY, 0 bytes captured" flake: deps runs concurrently with the deep
    audits). Routing per-thread makes captures independent.
    """

    def __init__(self, base):
        self._base = base
        self._local = threading.local()

    def _target(self):
        return getattr(self._local, "target", None) or self._base

    def write(self, s):
        return self._target().write(s)

    def flush(self):
        try:
            return self._target().flush()
        except ValueError:  # target closed between write and flush
            pass

    def writable(self):
        return True

    @property
    def encoding(self):
        return getattr(self._base, "encoding", "utf-8")

    def reconfigure(self, **kwargs):
        try:
            self._base.reconfigure(**kwargs)
        except (AttributeError, OSError):
            pass

    def isatty(self):
        try:
            return self._base.isatty()
        except ValueError:
            return False


@contextmanager
def thread_capture_stdout():
    """Capture THIS thread's stdout into a StringIO; other threads unaffected.

    The drop-in replacement for contextlib.redirect_stdout inside the audit
    wrappers — see _ThreadStdoutRouter for why the global swap is unsafe here.
    """
    if not isinstance(sys.stdout, _ThreadStdoutRouter):
        sys.stdout = _ThreadStdoutRouter(sys.stdout)
    router = sys.stdout
    buf = io.StringIO()
    prev = getattr(router._local, "target", None)
    router._local.target = buf
    try:
        yield buf
    finally:
        router._local.target = prev


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


def iter_py_files(root: Path):
    """Yield every auditable .py file under *root* — the fast, canonical walk.

    Equivalent to ``root.rglob("*.py")`` + :func:`should_audit`, but prunes
    SKIP_PARTS directories *during* the walk instead of enumerating and then
    discarding them — on a tree with a .venv/node_modules this is orders of
    magnitude fewer stat calls. Focus groups are still honoured.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            if should_audit(p):
                yield p


# path str -> (text_hash, text, tree|None, syntax_error|None). Keyed on
# content, not mtime, so same-size rewrites within timestamp resolution can
# never serve a stale tree. wiring→phd→runtime run in one process and parse
# the same files — this collapses three reads+parses into one.
_PARSE_CACHE: dict = {}


def read_and_parse(p: Path) -> tuple[str | None, object | None, str | None]:
    """Cached (text, ast_tree, syntax_error) for a source file.

    text is None if unreadable. tree is None when the file does not parse,
    with syntax_error carrying the message. Trees are shared objects —
    callers may attach analysis attributes but must not restructure them.
    """

    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, None
    tree, err = parse_text(p, txt)
    return txt, tree, err


def parse_text(p: Path, txt: str) -> tuple[object | None, str | None]:
    """Cached (ast_tree, syntax_error) for text the caller already read.

    The fast path for the wiring→phd→runtime pipeline: each module holds the
    source text from its collect step, so only the parse is deduplicated —
    no second disk read just to revalidate the cache.
    """
    import ast
    import warnings

    key = str(p)
    h = hash(txt)
    hit = _PARSE_CACHE.get(key)
    if hit is not None and hit[0] == h:
        return hit[2], hit[3]
    tree, err = None, None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(txt, filename=str(p))
    except SyntaxError as e:
        err = str(e)
    _PARSE_CACHE[key] = (h, txt, tree, err)
    return tree, err
