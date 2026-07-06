"""profiler.py — single-pass project profiler for cross-project comparison.

Answers "what is this project, how big, how heavy, what can it do?" by reading
and AST-parsing every source file exactly once.  It folds four separate
concerns into one walk so a judger can rank sibling projects:

  * structure     — LOC, functions, classes, imports, public API surface
  * cost          — pure ``ast.parse`` time as a "lighter engine" proxy
  * capabilities  — CONFIG-DRIVEN keyword buckets (no hardcoded domain terms)
  * performance   — external-library call intensity + optional marker hints

Nothing here is hardcoded to any product domain.  Domain vocabulary lives in
an optional ``[profile]`` table in ``audit-code.toml``; with no config the
profiler reports only generic, language-level signals.  File discovery honours
the same ``should_audit`` / skip rules as every other audit, and every file is
read with the target's declared ``#encoding`` (default UTF-8, errors replaced).
"""

import ast
import re
import time
from pathlib import Path

from audit_code.audit_shared import (
    SKIP_PARTS,
    configured_encoding,
    should_audit,
)

# ── General-purpose defaults (all overridable via [profile] config) ──────────
# These are language/ecosystem-level defaults, not domain vocabulary.  A caller
# scanning a different kind of project overrides them in audit-code.toml.

# Modules whose calls count as "heavy" compute.  General scientific/ML stack —
# override with [profile] heavy_libs = [...] for other domains.
_DEFAULT_HEAVY_LIBS = (
    "np",
    "numpy",
    "cv2",
    "torch",
    "tf",
    "tensorflow",
    "jax",
    "scipy",
    "pd",
    "pandas",
    "sklearn",
)

# Generic software action verbs used to bucket public functions into pipeline
# stages.  Override with [profile] pipeline_verbs = [...].
_DEFAULT_PIPELINE_VERBS = (
    "apply",
    "render",
    "process",
    "generate",
    "build",
    "create",
    "run",
    "compute",
    "transform",
    "parse",
    "load",
    "save",
    "draw",
    "detect",
)

# Regex fragments (case-insensitive) probed against raw source for optional
# performance hints.  Empty/omitted config disables the corresponding hint.
_DEFAULT_SIZE_MARKER = r"(?:SIZE|WIDTH|HEIGHT|RESOLUTION|DIM)\s*=\s*(\d+)"
_DEFAULT_ROI_MARKERS = ("roi", "region_of_interest", "crop", "bbox")
_DEFAULT_RESOLUTION_MARKERS = ("shape[0]", "shape[1]", ".width", ".height")


class ProfileConfig:
    """Resolved, non-hardcoded knobs for one profiling run.

    Built from the ``[profile]`` table of a loaded audit-code config (or an
    empty dict).  Every field falls back to a general-purpose default, so the
    profiler works with zero configuration and specialises only when asked.
    """

    def __init__(self, section: dict | None = None):
        section = section or {}
        self.heavy_libs = frozenset(section.get("heavy_libs", _DEFAULT_HEAVY_LIBS))
        self.pipeline_verbs = tuple(
            v.lower() for v in section.get("pipeline_verbs", _DEFAULT_PIPELINE_VERBS)
        )
        # capabilities: {group_name: [keyword, ...]} — no built-in defaults, so
        # nothing domain-specific is assumed unless the project declares it.
        raw_caps = section.get("capabilities", {}) or {}
        self.capabilities = {
            group: tuple(k.lower() for k in kws)
            for group, kws in raw_caps.items()
            if kws
        }
        self.roi_markers = tuple(
            m.lower() for m in section.get("roi_markers", _DEFAULT_ROI_MARKERS)
        )
        self.resolution_markers = tuple(
            section.get("resolution_markers", _DEFAULT_RESOLUTION_MARKERS)
        )
        # Optional name fragments that mark a dead symbol as a harmless
        # utility/helper rather than a missing feature.  No default — nothing is
        # assumed about a symbol unless the project opts in.
        self.utility_markers = tuple(
            m.lower() for m in section.get("utility_markers", ())
        )
        size_marker = section.get("size_marker", _DEFAULT_SIZE_MARKER)
        self._size_re = re.compile(size_marker) if size_marker else None

    def size_values(self, text: str) -> list[int]:
        """Extract declared buffer/resolution constants from *text*."""
        if self._size_re is None:
            return []
        out: list[int] = []
        for m in self._size_re.finditer(text):
            try:
                out.append(int(m.group(1)))
            except (ValueError, IndexError):
                continue
        return out


def profile_config_from(config: dict | None) -> ProfileConfig:
    """Pull the ``[profile]`` section out of a loaded audit-code config."""
    section = (config or {}).get("profile", {}) if config else {}
    return ProfileConfig(section)


def iter_source_files(root: Path) -> list[Path]:
    """Every auditable ``*.py`` under *root*, honouring the skip rules."""
    return [
        p
        for p in root.rglob("*.py")
        if should_audit(p) and not any(part in SKIP_PARTS for part in p.parts)
    ]


def _read_source(path: Path, encoding: str) -> str | None:
    """Read *path* as text under *encoding*; None if it cannot be read."""
    try:
        return path.read_text(encoding=encoding, errors="replace")
    except (OSError, ValueError):
        return None


def _call_root_name(node: ast.Call) -> str | None:
    """Return the leftmost Name of an attribute-call chain (e.g. ``np`` in
    ``np.linalg.norm(x)``), or None if the call is not attribute-rooted."""
    func = node.func
    while isinstance(func, ast.Attribute):
        func = func.value
    return func.id if isinstance(func, ast.Name) else None


class _Accumulator:
    """Mutable tallies collected across the single file walk."""

    def __init__(self, cfg: ProfileConfig):
        self.cfg = cfg
        self.loc = 0
        self.total_functions = 0
        self.loops = 0
        self.compute_ops = 0
        self.parse_seconds = 0.0
        self.files_scanned = 0
        self.files_skipped = 0
        self.public_functions: list[dict] = []
        self.classes: dict[str, list[str]] = {}
        self.imports: set[str] = set()
        self.pipeline_stages: set[str] = set()
        self.capabilities: dict[str, set[str]] = {g: set() for g in cfg.capabilities}
        self.buffer_sizes: list[int] = []
        self.uses_roi = False
        self.resolution_dependent = False

    def add_capability_hits(self, text_lower: str) -> None:
        for group, keywords in self.cfg.capabilities.items():
            for kw in keywords:
                if kw in text_lower:
                    self.capabilities[group].add(kw)

    def classify_stage(self, name: str) -> None:
        low = name.lower()
        if any(verb in low for verb in self.cfg.pipeline_verbs):
            self.pipeline_stages.add(name)


def _walk_file(acc: _Accumulator, tree: ast.AST, rel_path: str) -> None:
    """Fold one parsed file's structural signals into *acc* (single walk)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            acc.total_functions += 1
            if not node.name.startswith("_"):
                acc.public_functions.append(
                    {
                        "name": node.name,
                        "file": rel_path,
                        "line": node.lineno,
                        "args": len(node.args.args),
                    }
                )
                acc.classify_stage(node.name)
        elif isinstance(node, ast.ClassDef):
            methods = [
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")
            ]
            acc.classes[node.name] = methods
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            acc.loops += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                acc.imports.add(node.module.split(".")[0])
            else:
                for alias in node.names:
                    acc.imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.Call):
            root = _call_root_name(node)
            if root in acc.cfg.heavy_libs:
                acc.compute_ops += 1


def _scan_markers(acc: _Accumulator, text: str) -> None:
    """Fold optional, config-gated performance hints from raw source."""
    acc.buffer_sizes.extend(acc.cfg.size_values(text))
    low = text.lower()
    if not acc.uses_roi and any(m in low for m in acc.cfg.roi_markers):
        acc.uses_roi = True
    if not acc.resolution_dependent and any(
        m in text for m in acc.cfg.resolution_markers
    ):
        acc.resolution_dependent = True


def _estimate_speed(acc: _Accumulator) -> str:
    """Coarse fast/medium/slow label from structural signals only."""
    small_buffer = bool(acc.buffer_sizes) and min(acc.buffer_sizes) <= 512
    if acc.uses_roi and small_buffer:
        return "fast"
    if acc.uses_roi or acc.compute_ops < 200:
        return "medium"
    return "slow"


def _is_monolith(loc: int, public_fn_count: int) -> bool:
    """Few large public functions carrying a lot of code = monolith-leaning."""
    if public_fn_count == 0:
        return False
    return (loc / public_fn_count) > 120


def _build_report(acc: "_Accumulator") -> dict:
    """Assemble the JSON-serialisable profile from collected tallies."""
    public_fn_count = len(acc.public_functions)
    monolith = _is_monolith(acc.loc, public_fn_count)
    return {
        "structure": {
            "file_count": acc.files_scanned,
            "files_skipped": acc.files_skipped,
            "loc": acc.loc,
            "function_count": acc.total_functions,
            "public_function_count": public_fn_count,
            "class_count": len(acc.classes),
            "loop_count": acc.loops,
            "public_functions": sorted(
                acc.public_functions, key=lambda f: (f["file"], f["line"])
            ),
            "classes": {k: sorted(v) for k, v in sorted(acc.classes.items())},
            "imports": sorted(acc.imports),
        },
        "metrics": {"parse_seconds": round(acc.parse_seconds, 4)},
        "architecture": {
            "is_monolith": monolith,
            "pipeline_separated": not monolith and public_fn_count >= 5,
            "pipeline_stages": sorted(acc.pipeline_stages),
            "pipeline_count": len(acc.pipeline_stages),
        },
        "performance": {
            "compute_ops": acc.compute_ops,
            "min_buffer_size": min(acc.buffer_sizes) if acc.buffer_sizes else None,
            "uses_roi": acc.uses_roi,
            "resolution_dependent": acc.resolution_dependent,
            "estimated_speed": _estimate_speed(acc),
        },
        "capabilities": {
            group: sorted(hits) for group, hits in sorted(acc.capabilities.items())
        },
    }


def profile_project(
    root: Path | str,
    *,
    config: dict | None = None,
    encoding: str | None = None,
) -> dict:
    """Profile a single project in one read+parse+walk pass per file.

    Args:
        root: project directory to profile.
        config: a loaded audit-code config dict (its ``[profile]`` table drives
            the non-hardcoded vocabulary); None uses general defaults.
        encoding: override the source encoding; None reads the target's
            declared ``#encoding`` (default UTF-8).

    Returns:
        A JSON-serialisable dict with ``structure``, ``metrics``,
        ``architecture``, ``performance`` and (config-driven) ``capabilities``.
    """
    root = Path(root)
    cfg = profile_config_from(config)
    enc = encoding or configured_encoding(root)
    acc = _Accumulator(cfg)

    for path in iter_source_files(root):
        text = _read_source(path, enc)
        if text is None:
            acc.files_skipped += 1
            continue
        t0 = time.monotonic()
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            acc.files_skipped += 1
            continue
        acc.parse_seconds += time.monotonic() - t0

        acc.files_scanned += 1
        acc.loc += len(text.splitlines())
        try:
            rel_path = str(path.relative_to(root))
        except ValueError:
            rel_path = path.name
        _walk_file(acc, tree, rel_path)
        acc.add_capability_hits(text.lower())
        _scan_markers(acc, text)

    return _build_report(acc)


def _normalize_skip(skip) -> set[str]:
    """Accept a list, a comma/space string, or None; return a name set."""
    if skip is None:
        return set()
    if isinstance(skip, str):
        skip = re.split(r"[,\s]+", skip)
    return {s.strip() for s in skip if s and s.strip()}


def audit_high_counts(project: Path | str) -> dict:
    """Run the wiring + phd audits on *project* and return HIGH-finding counts.

    Imported lazily so the fast, pure-AST profiling path never pays the cost of
    (or depends on) the subprocess-driven audit modules unless asked.
    """
    from audit_code import phd, wiring

    project = Path(project)
    w = wiring.run(project)
    p = phd.run(project)
    return {
        "wiring_high": w.high,
        "phd_high": p.high,
        "total_high": w.high + p.high,
        "wiring_status": w.status.value,
        "phd_status": p.status.value,
    }


def compare_projects(
    root: Path | str,
    skip=None,
    *,
    config: dict | None = None,
    include_audit: bool = False,
) -> dict[str, dict]:
    """Profile every immediate subdirectory of *root* for side-by-side ranking.

    Args:
        root: parent directory whose child folders are sibling projects.
        skip: dir names to ignore (list, or comma/space-separated string).
        config: audit-code config applied to every project (see
            :func:`profile_project`).
        include_audit: also run the wiring + phd audits per subproject and
            attach their HIGH-finding counts under an ``"audit"`` key. Off by
            default because it is far slower than the pure-AST profile.

    Returns:
        ``{project_name: profile_dict}`` for each profiled subdirectory.
    """
    root = Path(root)
    skip_names = _normalize_skip(skip) | SKIP_PARTS
    results: dict[str, dict] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in skip_names:
            continue
        prof = profile_project(child, config=config)
        if include_audit:
            prof["audit"] = audit_high_counts(child)
        results[child.name] = prof
    return results


def classify_dead_symbols(
    project: Path | str,
    dead_symbols: list[dict],
    *,
    config: dict | None = None,
) -> dict:
    """Sort wiring's dead symbols into *critical* / *utility* / *other* buckets.

    Wiring says a symbol is dead; this says whether that matters. A dead symbol
    whose name matches a configured pipeline verb is a **missing feature** (code
    exists but never runs); one matching a configured utility marker is likely a
    harmless helper; the rest are left for manual review. Both keyword sets are
    config-driven (``[profile] pipeline_verbs`` / ``utility_markers``) so nothing
    domain-specific is baked in.

    Args:
        project: the project the symbols belong to (for the reference re-check).
        dead_symbols: ``[{name, file, line, ...}]`` — e.g. from
            ``wiring.collect_dead_symbols``.
        config: audit-code config supplying the vocabulary.

    Returns:
        ``{critical, utility, other, accuracy_impact, summary}``.
    """
    project = Path(project)
    cfg = profile_config_from(config)
    enc = configured_encoding(project)
    texts = {p: (_read_source(p, enc) or "") for p in iter_source_files(project)}

    critical: list[dict] = []
    utility: list[dict] = []
    other: list[dict] = []
    for sym in dead_symbols:
        name = sym.get("name", "")
        if not name:
            continue
        def_file = (project / sym["file"]) if sym.get("file") else None
        word = re.compile(rf"\b{re.escape(name)}\b")
        # Guard against false positives: a reference in any *other* file means
        # the symbol is wired after all, so drop it.
        if any(p != def_file and word.search(text) for p, text in texts.items()):
            continue
        entry = {"name": name, "file": sym.get("file"), "line": sym.get("line")}
        low = name.lower()
        if any(v in low for v in cfg.pipeline_verbs):
            entry["reason"] = "pipeline/feature function defined but never called"
            critical.append(entry)
        elif cfg.utility_markers and any(u in low for u in cfg.utility_markers):
            entry["reason"] = "utility/helper — may be future or import-time use"
            utility.append(entry)
        else:
            entry["reason"] = "unclassified dead symbol — verify manually"
            other.append(entry)

    return {
        "critical": critical,
        "utility": utility,
        "other": other,
        "accuracy_impact": bool(critical),
        "summary": (
            f"{len(critical)} dead features, {len(utility)} dead utilities, "
            f"{len(other)} other"
        ),
    }
