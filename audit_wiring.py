#!/usr/bin/env python3
"""
audit_wiring.py — deep wiring audit for the Agent Build Engine.

WHY THIS EXISTS
===============
The graph-based audit (graphify-out/wiring_audit.py) mines graph.json, but the
graphify extractor misses attribute calls (`decision.select_action(...)`),
dict-key config reads (`limits.limits["MAX_X"]`), and string-based dispatch
(`run_tool("verify_code", ...)`). Result: ~50% false positives. Every finding
needed manual grep verification.

This script reimplements the audit directly against the source tree using
Python's `ast` module, encoding the SAME checks that were done by hand:

  CHECK 1 - DEAD SYMBOLS
    A function/method/class is flagged only if its NAME appears nowhere else
    in the codebase in ANY form: direct call `foo()`, attribute call/load
    `x.foo`, bare reference `foo` (callbacks, decorators, aiohttp routes),
    or string literal "foo" (dynamic dispatch via registries / getattr).
    Because *any* appearance counts as alive, a DEAD flag is high-confidence.
    The tradeoff: shared names hide dead code (false negatives, never false
    positives). Findings you can act on > findings you must re-verify.

  CHECK 2 - TEST-ONLY SYMBOLS (the gold category)
    Referenced ONLY from tests/. This is how check_mcp_call, record_mcp_call,
    add_read_path, mark_done, Settings.agent_config were found: the component
    is built and unit-tested green, but no production line ever calls it.
    Unit tests make these *look* covered — that is exactly the trap.

  CHECK 3 - DEAD CONFIG KEYS (exact-quote matching)
    For each key in limits.json / model_rules.json / agent_models.json /
    providers.json / hook_rules.json: a consumer is a production .py file
    containing the key AS A QUOTED STRING ("KEY" or 'KEY'). Quoted matching
    kills the substring false positive that the naive sweep had
    (_MAX_FILE_READ_BYTES contains MAX_FILE_READ_BYTES).
    v2: for limits.json, only the _default_limits() SPAN of settings.py is
    excluded (that function mirrors the JSON — defining a default is not
    consuming). v1 excluded the whole file, which false-positived
    MAX_SUBAGENT_ITERATIONS and MAX_SUBAGENT_DEPTH — genuinely consumed by
    Settings.agent_config() and re-exposed to every agent as lowercase keys.
    Dead keys are now grouped by blast radius: approval gates and limits
    that cannot fire are a safety hole, not config hygiene.

  CHECK 4 - cfg-KEY FLOW (lowercase run-default dialect)
    The engine has a second config dialect: lowercase keys read via
    cfg.get("benchmark_target"). This check collects every cfg.get("k") /
    cfg["k"] read in production and every lowercase key defined in
    config/settings.py dict literals, then reports both directions:
      defined-but-never-read  -> dead default (e.g. worker_hard_role)
      read-but-never-defined  -> hidden knob with a hardcoded fallback
                                 (e.g. premium_exec_role) that the settings
                                 UI and switch_rules cannot see.

  CHECK 5 - PAIR SYMMETRY
    Conventional pairs (record_X/check_X, save/load, set_X/get_X,
    bind/unbind, subscribe/unsubscribe) defined in the same module where one
    side has production references and the other has none. Catches the
    "counter is read by the gate but nothing ever increments it" class
    (record_mcp_call vs the mcp_call_limit gate).
    v2: same-file references count as wired. v1 excluded the defining file,
    which false-positived check_llm_call (called at run_loop.py:758/816, same
    file as its def) and get_log_chat (called inside _log.py's own log()).

  CHECK 6 - OVERRIDE PARITY (fork drift)
    For each method that overrides a same-repo base-class method, diff the
    set of "interesting" identifiers (defined functions, ALL_CAPS settings
    keys, quoted config strings) referenced by parent vs child bodies.
    Parent-only identifiers = features lost in the fork. Catches
    InstrumentedHermes.run missing MAX_TOOL_RESULT_TOKENS truncation and the
    iteration-budget injection that only legacy Hermes.run has.
    v2: dict/builtin method names (get, items, pop, ...) no longer count as
    "lost features" — `cfg.get(...)` in the parent is not a feature.

  CHECK 7 - SHADOWED CONFIG (v2; the "knob exists but code ignores it" class)
    Correlates CHECK 3's dead config keys against hardcoded constants in
    production: module-level ALL_CAPS numeric assigns and constant-valued
    dict keys in config/settings.py, matched by name-token subset/overlap.
    Catches MAX_KEYS_PER_PROVIDER = 5 hardcoded in providers.py while
    limits.json defines the same key, and _run_defaults() hardcoding
    "benchmark_target": 85 while limits.json's BENCHMARK_TARGET rots unread.
    This is the actionable form of a dead key: the wiring point is known.

  CHECK 8 - TRANSITIVELY DEAD CONFIG (v2; automates "verify consumer enforces")
    A key can have a consumer that is itself dead: MAX_MCP_CALLS_PER_TOOL is
    read only inside check_mcp_call(), which CHECK 2 proves is test-only —
    so the limit can never fire. For every live key, locate the enclosing
    function of each quoted read; if ALL enclosing functions are dead or
    test-only, the key is configured-but-unenforced. This closes the loop
    that v1 left to the human ("single-consumer keys: verify consumer
    enforces").

  CHECK 9 - STDOUT PURITY (v2; the #1 engine rule, previously unchecked)
    Rust parses `__EVENT__<json>` / `__RESULT__...__END__` from the agent
    process's stdout; ANY stray print() corrupts the stream. This check
    builds the import graph (including function-level imports — they execute
    in-process), BFS-reaches every module loadable from the agent entry
    points (run_entry, run_loop, agents.*), and flags bare print() calls or
    sys.stdout touches. A stdout write is sanctioned when its payload carries
    the __EVENT__/__RESULT__ marker (that IS the protocol — e.g. agent_manager
    .flush_events) or when the whole module is a protocol writer (run_entry,
    agents.orchestrator, dll._dll_server — the last owns its own subprocess
    pipe). Expected to report none today; it exists so the next print()
    debugging session never reaches main.

KNOWN LIMITATIONS (read before trusting silence)
================================================
- Name-global resolution: two defs sharing a name shadow each other; if either
  is alive, both look alive. Dead code behind a common name (e.g. `.run`) is
  NOT detected. Silence is not proof of life.
- Semantic deadness is out of scope: a dict field that is produced, stored,
  and logged but never *acted on* (the manager's "hard" flag) references fine
  textually. Pair symmetry and cfg-flow catch some of these; code review
  catches the rest.
- Dynamic frameworks: aiohttp/pytest reference handlers by object, which we
  count via bare-Name loads — so route handlers won't false-positive, but a
  handler registered through config strings we don't scan could.

USAGE
=====
  python audit_wiring.py            # full report
  python audit_wiring.py --strict   # exit 1 if any HIGH-confidence finding
"""

import ast
import collections
import json
import re
import sys
from pathlib import Path

from audit_shared import SKIP_PARTS

ROOT = Path(__file__).parent.parent
# Allow --path override for audit-code wrapper
for _i, _a in enumerate(sys.argv):
    if _a == "--path" and _i + 1 < len(sys.argv):
        ROOT = Path(sys.argv[_i + 1]).resolve()
        break

# Names too generic to classify (would drown in shared-name collisions),
# plus entry points invoked from outside the scanned tree.
NAME_SKIP = {"main", "cli", "run", "wrapper", "inner", "decorator"}

CONFIG_FILES = [
    "limits.json",
    "model_rules.json",
    "agent_models.json",
    "providers.json",
    "hook_rules.json",
]

PAIR_CONVENTIONS = [
    ("record_", "check_"),
    ("set_", "get_"),
    ("bind", "unbind"),
    ("subscribe", "unsubscribe"),
    ("save", "load"),
]

# CHECK 6: identifiers that are dict/builtin method names — referencing
# `cfg.get(...)` in a parent method is not a "feature" the child lost.
ATTR_NOISE = {
    "get",
    "set",
    "items",
    "keys",
    "values",
    "append",
    "add",
    "update",
    "pop",
    "copy",
    "close",
    "join",
    "split",
}

# CHECK 9: modules that run inside the Tauri-spawned agent process.
AGENT_ENTRY_MODULES = {"run_entry", "run_loop", "agents.agent_manager", "agents.hermes"}
# Modules allowed to touch stdout: the __RESULT__/__EVENT__ protocol writers,
# plus _dll_server which is a separate subprocess with its own stdout pipe.
SANCTIONED_STDOUT = {"run_entry", "agents.orchestrator", "dll._dll_server"}


def is_test(path: Path) -> bool:
    s = str(path).replace("\\", "/")
    return (
        "/tests/" in s
        or path.name.startswith(("test_", "tests_"))
        or path.name == "conftest.py"
    )


def collect_files():
    prod, test = {}, {}
    for p in ROOT.rglob("*.py"):
        # the audit suite itself (root-level audit_*.py + runner) is not the engine
        if p.parent == ROOT and (
            p.name.startswith("audit_") or p.name == "run_all_audits.py"
        ):
            continue
        if any(part in SKIP_PARTS for part in p.parts):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        (test if is_test(p) else prod)[p] = txt
    return prod, test


# ──────────────────────────────────────────────────────────────────
# Phase 1+2: definition index and reference index
# ──────────────────────────────────────────────────────────────────


class Refs(ast.NodeVisitor):
    """Collect every form a symbol can be referenced in."""

    def __init__(self):
        self.names = set()  # identifiers referenced (call, load, attr)
        self.strings = set()  # identifier-shaped string literals

    def visit_Attribute(self, node):
        self.names.add(node.attr)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)
        self.generic_visit(node)

    def visit_Constant(self, node):
        v = node.value
        if (
            isinstance(v, str)
            and 3 < len(v) < 60
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v)
        ):
            self.strings.add(v)
        self.generic_visit(node)

    # Imports are references too: `from engine.redteam import attack as
    # redteam_attack` keeps `attack` alive even though the call site uses
    # the alias. Missing this was the v1 false-positive on attack().
    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.names.add(alias.name)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.names.add(alias.name.split(".")[-1])
        self.generic_visit(node)


def parse_all(files):
    trees = {}
    for p, txt in files.items():
        try:
            trees[p] = ast.parse(txt)
        except SyntaxError as e:
            print(f"  [warn] cannot parse {p}: {e}")
    return trees


def index_defs(trees):
    """{name: [(path, qualname, lineno, kind)]} for functions, methods, classes."""
    defs = collections.defaultdict(list)
    for p, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                defs[node.name].append((p, node.name, node.lineno, "class"))
                for ch in node.body:
                    if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defs[ch.name].append(
                            (p, f"{node.name}.{ch.name}", ch.lineno, "method")
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # module-level only; methods were handled above. Detect by
                # checking col_offset == 0 (cheap and good enough).
                if node.col_offset == 0:
                    defs[node.name].append((p, node.name, node.lineno, "function"))
    return defs


def framework_wired_methods(trees):
    """{method_name: set(paths)} for public methods of classes whose base is
    NOT defined in this repo.

    An external base class is the caller of the methods it dispatches by name
    (HTMLParser.handle_starttag, Thread.run, unittest hooks) — zero in-repo
    references is expected, not dead code. Same rationale as counting imports
    as references. Private (_-prefixed) methods stay eligible for CHECK 1:
    frameworks dispatch public API names.
    """
    local_classes = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                local_classes.add(node.name)
    wired = collections.defaultdict(set)
    for p, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not node.bases:
                continue
            base_names = set()
            for b in node.bases:
                if isinstance(b, ast.Name):
                    base_names.add(b.id)
                elif isinstance(b, ast.Attribute):
                    base_names.add(b.attr)
            external = any(b not in local_classes and b != "object" for b in base_names)
            if not external:
                continue
            for ch in node.body:
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not ch.name.startswith("_"):
                        wired[ch.name].add(p)
    return wired


def index_refs(trees):
    """{name: set(paths that reference it in any form)}"""
    refs = collections.defaultdict(set)
    for p, tree in trees.items():
        v = Refs()
        v.visit(tree)
        for n in v.names | v.strings:
            refs[n].add(p)
    return refs


def classify_defs(defs, refs_prod, refs_test, prod_files):
    dead, test_only = [], []
    for name, sites in defs.items():
        if name.startswith("__") or name in NAME_SKIP:
            continue
        prod_sites = [s for s in sites if s[0] in prod_files]
        if not prod_sites:
            continue  # defined only in tests — not our problem
        def_files = {s[0] for s in prod_sites}
        prod_refs = (
            refs_prod.get(name, set()) - def_files
        )  # refs OUTSIDE defining file(s)
        same_file_refs = refs_prod.get(name, set()) & def_files
        test_refs = refs_test.get(name, set())
        ambiguous = len(sites) > 1
        if not prod_refs and not test_refs and not same_file_refs:
            dead.append((name, prod_sites, ambiguous))
        elif not prod_refs and not same_file_refs and test_refs:
            test_only.append(
                (
                    name,
                    prod_sites,
                    sorted(str(t.relative_to(ROOT)) for t in test_refs)[:3],
                    ambiguous,
                )
            )
    return dead, test_only


# ──────────────────────────────────────────────────────────────────
# Phase 3: config keys (exact-quote matching)
# ──────────────────────────────────────────────────────────────────

# Keys under these parents are DATA consumed by iterating the dict
# (e.g. router loops over prefix_routing.items()) — auditing them by name
# would be a guaranteed false positive.
DATA_PARENTS = {"prefix_routing", "providers"}


def iter_keys(obj, parent=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and not k.startswith("_"):
                if parent not in DATA_PARENTS:
                    yield k
                yield from iter_keys(v, k)


def blank_mirror_span(prod):
    """Return prod text with settings.py's _default_limits() span blanked.

    That one function re-declares limits.json — defining a default is not
    consuming. Everything ELSE in settings.py (agent_config, _run_defaults)
    reads limits keys for real and must keep its consumption credit.
    """
    out = {}
    for p, t in prod.items():
        if p.name != "settings.py":
            out[p] = t
            continue
        try:
            tree = ast.parse(t)
        except SyntaxError:
            out[p] = t
            continue
        lines = t.splitlines()
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_default_limits"
            ):
                for i in range(node.lineno - 1, node.end_lineno or node.lineno):
                    lines[i] = ""
        out[p] = "\n".join(lines)
    return out


def audit_config_keys(prod):
    """{config_file: (dead_keys, single_consumer, {key: [(path, line), ...]})}"""
    results = {}
    blanked = blank_mirror_span(prod)
    for cf in CONFIG_FILES:
        path = ROOT / cf
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        consumers_text = blanked if cf == "limits.json" else prod
        dead, single, consumers = [], [], {}
        for key in sorted(set(iter_keys(data))):
            pat = re.compile(r"""["']{}["']""".format(re.escape(key)))
            hits = []
            for p, t in consumers_text.items():
                for m in pat.finditer(t):
                    hits.append((p, t.count("\n", 0, m.start()) + 1))
            if not hits:
                dead.append(key)
                continue
            consumers[key] = hits
            files = {p for p, _ in hits}
            if len(files) == 1:
                single.append((key, str(next(iter(files)).relative_to(ROOT))))
        results[cf] = (dead, single, consumers)
    return results


# ──────────────────────────────────────────────────────────────────
# Phase 4: cfg.get() flow (lowercase dialect)
# ──────────────────────────────────────────────────────────────────


def audit_cfg_flow(prod, trees):
    read_keys = collections.defaultdict(set)  # key -> files that read it
    cfg_read = re.compile(r"""cfg(?:\.get\(\s*|\[\s*)["']([a-z_][a-z0-9_]*)["']""")
    for p, t in prod.items():
        for m in cfg_read.finditer(t):
            read_keys[m.group(1)].add(p)

    defined = set()
    settings_path = next((p for p in trees if p.name == "settings.py"), None)
    if settings_path:
        for node in ast.walk(trees[settings_path]):
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if (
                        isinstance(k, ast.Constant)
                        and isinstance(k.value, str)
                        and re.fullmatch(r"[a-z][a-z0-9_]{3,}", k.value)
                    ):
                        defined.add(k.value)

    # A defined key only counts as never-read if its quoted form appears in
    # NO other production file at all — settings.py contains paths/model
    # dicts (data_dir, bulk_code, ...) consumed via other access patterns,
    # and those must not be flagged.
    never_read = []
    for k in sorted(defined):
        if k in read_keys:
            continue
        pat = re.compile(r"""["']{}["']""".format(re.escape(k)))
        elsewhere = any(pat.search(t) for p, t in prod.items() if p != settings_path)
        if not elsewhere:
            never_read.append(k)
    hidden = sorted(
        (k, sorted(str(f.relative_to(ROOT)) for f in fs))
        for k, fs in read_keys.items()
        if k not in defined
    )
    return never_read, hidden


# ──────────────────────────────────────────────────────────────────
# Phase 5: pair symmetry
# ──────────────────────────────────────────────────────────────────


def audit_pairs(defs, refs_prod, prod_files):
    findings = []
    by_file = collections.defaultdict(set)
    for name, sites in defs.items():
        for p, _, _, _ in sites:
            if p in prod_files:
                by_file[p].add(name)
    for p, names in by_file.items():
        for a_pre, b_pre in PAIR_CONVENTIONS:
            for n in names:
                if not n.startswith(a_pre):
                    continue
                suffix = n[len(a_pre) :]
                partner = (
                    b_pre + suffix
                    if a_pre.endswith("_")
                    else (b_pre if n == a_pre else None)
                )
                if not partner or partner not in names:
                    continue
                # Same-file references count: run_loop calls its own
                # check_llm_call in the loop body — that IS production wiring.
                # (A def alone adds no ref, so no self-exclusion is needed.)
                a_live = bool(refs_prod.get(n, set()))
                b_live = bool(refs_prod.get(partner, set()))
                if a_live != b_live:
                    deadside = partner if a_live else n
                    liveside = n if a_live else partner
                    findings.append((str(p.relative_to(ROOT)), deadside, liveside))
    return findings


# ──────────────────────────────────────────────────────────────────
# Phase 6: override parity (fork drift)
# ──────────────────────────────────────────────────────────────────


def interesting(names, defs):
    out = set()
    for n in names:
        if n in ATTR_NOISE:
            continue
        if n.isupper() and len(n) > 6:
            out.add(n)
        elif n in defs and not n.startswith("_"):
            out.add(n)
    return out


def _super_calls(method):
    """Method names invoked via super().<name>(...) inside this method.
    super().run() means the parent body executes verbatim — anything the
    parent referenced is inherited, not lost."""
    out = set()
    for n in ast.walk(method):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Call)
            and isinstance(n.func.value.func, ast.Name)
            and n.func.value.func.id == "super"
        ):
            out.add(n.func.attr)
    return out


def _class_refs(cnode):
    """Every identifier referenced ANYWHERE in the class body. A child that
    splits the parent's monolithic run() into overridden helpers (_build_system,
    _toolset, _exec_tool) still 'has' those identifiers — just not in the method
    that textually overrides the parent. Counting the whole class kills that
    false positive. Tradeoff: an identifier reused in an unrelated child method
    hides genuine drift (false negative) — acceptable vs. flooding false alarms."""
    v = Refs()
    v.visit(cnode)
    return v.names | v.strings


def audit_overrides(trees, defs, prod_files):
    classes = {}
    for p, tree in trees.items():
        if p not in prod_files:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes[node.name] = (p, node)
    findings = []
    for cname, (p, node) in classes.items():
        child_refs = _class_refs(node)
        for base in node.bases:
            bname = (
                base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
            )
            if bname not in classes or bname == cname:
                continue
            bp, bnode = classes[bname]
            base_methods = {
                m.name: m
                for m in bnode.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for m in node.body:
                if (
                    isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and m.name in base_methods
                ):
                    # super().<m>() → parent body runs as-is, nothing dropped.
                    if m.name in _super_calls(m):
                        continue
                    pv, cv = Refs(), Refs()
                    pv.visit(base_methods[m.name])
                    cv.visit(m)
                    parent_ids = pv.names | pv.strings
                    # Child "has" an identifier if it's in this method OR
                    # anywhere else in the class (relocated to a helper).
                    child_ids = (cv.names | cv.strings) | child_refs
                    lost = interesting(parent_ids - child_ids, defs)
                    if lost:
                        findings.append(
                            (
                                f"{cname}.{m.name}",
                                f"{bname}.{m.name}",
                                str(p.relative_to(ROOT)),
                                m.lineno,
                                sorted(lost),
                            )
                        )
    return findings


# ──────────────────────────────────────────────────────────────────
# Phase 7: shadowed config (dead key <-> hardcoded constant)
# ──────────────────────────────────────────────────────────────────


def _tokens(name):
    return {t for t in name.upper().lstrip("_").split("_") if t}


def audit_shadowed_config(dead_keys, prod):
    """Match dead config keys to hardcoded constants by name-token overlap.

    Two constant sources: module-level ALL_CAPS numeric assigns anywhere in
    production, and constant-valued dict keys in config/settings.py (the
    _run_defaults() dialect: "benchmark_target": 85).
    """
    consts = []  # (display_name, tokens, path, line, value)
    # Parse from mirror-blanked text: _default_limits() re-declares every
    # limits.json key, and matching a dead key against its own mirror echo
    # is not a finding.
    blanked = {}
    for p, t in blank_mirror_span(prod).items():
        try:
            blanked[p] = ast.parse(t)
        except SyntaxError:
            pass
    for p, tree in blanked.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                nm = node.targets[0].id
                if (
                    nm.isupper()
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, (int, float))
                    and not isinstance(node.value.value, bool)
                ):
                    consts.append((nm, _tokens(nm), p, node.lineno, node.value.value))
            if p.name == "settings.py" and isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (
                        isinstance(k, ast.Constant)
                        and isinstance(k.value, str)
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, (int, float))
                        and not isinstance(v.value, bool)
                    ):
                        consts.append(
                            (f'"{k.value}"', _tokens(k.value), p, k.lineno, v.value)
                        )

    findings = []
    for key in dead_keys:
        kt = _tokens(key)
        matches = []
        for nm, ct, p, line, val in consts:
            small, big = (kt, ct) if len(kt) <= len(ct) else (ct, kt)
            subset = len(small) >= 2 and small <= big
            jaccard = len(kt & ct) / len(kt | ct) if kt | ct else 0
            if subset or jaccard >= 0.6:
                matches.append((jaccard, nm, val, str(p.relative_to(ROOT)), line))
        # best match first; cap at 2 so one fuzzy key can't flood the report
        for jac, nm, val, f, line in sorted(matches, reverse=True)[:2]:
            findings.append((key, nm, val, f, line))
    return findings


# ──────────────────────────────────────────────────────────────────
# Phase 8: transitively dead config (consumer exists but never runs)
# ──────────────────────────────────────────────────────────────────


def audit_transitive_config(config_results, trees, dead_names, testonly_names):
    """Keys whose every quoted read sits inside a dead/test-only function."""
    spans = {}  # path -> [(start, end, name)]
    for p, tree in trees.items():
        spans[p] = [
            (n.lineno, n.end_lineno or n.lineno, n.name)
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

    def enclosing(p, line):
        best = None
        for s, e, nm in spans.get(p, ()):
            if s <= line <= e and (best is None or s > best[0]):
                best = (s, nm)
        return best[1] if best else None  # None = module level (runs at import)

    unwired = set(dead_names) | set(testonly_names)
    findings = []
    for cf, (_, _, consumers) in config_results.items():
        for key, hits in consumers.items():
            encl = [(enclosing(p, line), p, line) for p, line in hits]
            fns = {e for e, _, _ in encl}
            if fns and None not in fns and fns <= unwired:
                where = ", ".join(
                    sorted({f"{p.relative_to(ROOT)}:{e}()" for e, p, _ in encl})
                )
                findings.append((cf, key, where))
    return findings


# ──────────────────────────────────────────────────────────────────
# Phase 9: stdout purity (the __EVENT__ stream protocol)
# ──────────────────────────────────────────────────────────────────


def audit_stdout_purity(trees):
    def modname(p):
        parts = list(p.relative_to(ROOT).parts)
        parts[-1] = parts[-1][:-3]
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    mods = {modname(p): p for p in trees}
    edges = collections.defaultdict(set)
    for p, tree in trees.items():
        src = modname(p)
        # ast.walk, not tree.body: function-level imports execute in-process.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in mods:
                    edges[src].add(node.module)
                for a in node.names:
                    cand = f"{node.module}.{a.name}"
                    if cand in mods:
                        edges[src].add(cand)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in mods:
                        edges[src].add(a.name)

    reach, stack = set(), [m for m in AGENT_ENTRY_MODULES if m in mods]
    while stack:
        m = stack.pop()
        if m in reach:
            continue
        reach.add(m)
        stack.extend(edges.get(m, ()))

    def is_protocol_write(call):
        """sys.stdout.write(...) whose payload carries the protocol markers."""
        if not call.args:
            return False
        a = call.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            parts = [a.value]
        elif isinstance(a, ast.JoinedStr):
            parts = [
                v.value
                for v in a.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
        else:
            return False
        return any(m in s for s in parts for m in ("__EVENT__", "__RESULT__"))

    findings = []
    for m in sorted(reach - SANCTIONED_STDOUT):
        p = mods[m]
        # First pass: bless the sys.stdout attributes inside legitimate
        # protocol writes (__EVENT__/__RESULT__ payloads) and .flush() calls.
        blessed = set()
        for node in ast.walk(trees[p]):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "stdout"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "sys"
            ):
                if node.func.attr == "flush" or (
                    node.func.attr == "write" and is_protocol_write(node)
                ):
                    blessed.add(id(node.func.value))
        for node in ast.walk(trees[p]):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
                and not any(kw.arg == "file" for kw in node.keywords)
            ):
                findings.append(
                    (
                        str(p.relative_to(ROOT)),
                        node.lineno,
                        "bare print() - corrupts the __EVENT__ stream",
                    )
                )
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "stdout"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
                and id(node) not in blessed
            ):
                findings.append(
                    (
                        str(p.relative_to(ROOT)),
                        node.lineno,
                        "sys.stdout write without __EVENT__/__RESULT__ marker",
                    )
                )
    return findings, len(reach)


# ──────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────


def key_class(k):
    """Blast radius of a dead config key."""
    if k.startswith("REQUIRE_APPROVAL_"):
        return "SAFETY"
    if re.search(
        r"^MAX_|_MAX|^MIN_|TIMEOUT|LIMIT|TARGET|DELTA|THRESHOLD|CONFIDENCE|COOLDOWN|BACKOFF",
        k,
    ):
        return "LIMIT"
    return "cosmetic"


def main():
    strict = "--strict" in sys.argv
    prod, test = collect_files()
    print(f"scanned: {len(prod)} production files, {len(test)} test files\n")
    trees_prod = parse_all(prod)
    trees_test = parse_all(test)
    defs = index_defs(trees_prod)
    refs_prod = index_refs(trees_prod)
    refs_test = index_refs(trees_test)
    for name, paths in framework_wired_methods(trees_prod).items():
        refs_prod[name] |= paths

    high_findings = 0

    dead, test_only = classify_defs(defs, refs_prod, refs_test, set(prod))
    print("=" * 72)
    print("CHECK 1 - DEAD SYMBOLS (zero references anywhere; HIGH confidence)")
    print("=" * 72)
    for name, sites, amb in sorted(dead):
        for p, qual, line, kind in sites:
            tag = " [shared-name]" if amb else ""
            print(f"  {kind:8} {qual:42} {p.relative_to(ROOT)}:{line}{tag}")
            high_findings += 1
    if not dead:
        print("  none")

    print()
    print("=" * 72)
    print("CHECK 2 - TEST-ONLY SYMBOLS (built+tested, never wired; HIGH value)")
    print("=" * 72)
    for name, sites, tfiles, amb in sorted(test_only):
        for p, qual, line, kind in sites:
            tag = " [shared-name]" if amb else ""
            print(f"  {kind:8} {qual:42} {p.relative_to(ROOT)}:{line}{tag}")
            print(f"           tested by: {', '.join(tfiles)}")
            high_findings += 1
    if not test_only:
        print("  none")

    print()
    print("=" * 72)
    print("CHECK 3 - DEAD CONFIG KEYS (quoted-exact; _default_limits span excluded)")
    print("=" * 72)
    config_results = audit_config_keys(prod)
    for cf, (dead_keys, single, _) in config_results.items():
        if dead_keys:
            groups = collections.defaultdict(list)
            for k in dead_keys:
                groups[key_class(k)].append(k)
                high_findings += 1
            print(f"\n  {cf}: {len(dead_keys)} dead keys")
            if groups.get("SAFETY"):
                print(
                    "    [SAFETY] approval gates that exist only in config - "
                    "nothing reads them, so no approval is ever required:"
                )
                for k in groups["SAFETY"]:
                    print(f"      {k}")
            if groups.get("LIMIT"):
                print("    [LIMIT] brakes/limits that cannot fire (no reader):")
                for k in groups["LIMIT"]:
                    print(f"      {k}")
            if groups.get("cosmetic"):
                print("    [cosmetic]:")
                for k in groups["cosmetic"]:
                    print(f"      {k}")
        if single:
            print(f"  {cf}: single-consumer keys (CHECK 8 verifies the consumer runs):")
            for k, f in single:
                print(f"      {k:38} -> {f}")

    print()
    print("=" * 72)
    print("CHECK 4 - cfg-KEY FLOW (lowercase dialect)")
    print("=" * 72)
    never_read, hidden = audit_cfg_flow(prod, trees_prod)
    print("  defined in settings.py, never read via cfg.get()/cfg[] :")
    for k in never_read:
        print(f"      {k}")
    print("  read via cfg.get() but NOT defined in settings.py (hidden knobs):")
    for k, files in hidden:
        print(f"      {k:38} read in {', '.join(files)}")

    print()
    print("=" * 72)
    print("CHECK 5 - PAIR ASYMMETRY (one side wired, partner dead)")
    print("=" * 72)
    pairs = audit_pairs(defs, refs_prod, set(prod))
    for f, deadside, liveside in sorted(set(pairs)):
        print(f"  {f}: {liveside} is wired but {deadside} has no production caller")
        high_findings += 1
    if not pairs:
        print("  none")

    print()
    print("=" * 72)
    print("CHECK 6 - OVERRIDE PARITY (features lost in subclass forks)")
    print("=" * 72)
    overrides = audit_overrides(trees_prod, defs, set(prod))
    for child, parent, f, line, lost in overrides:
        print(f"  {child} ({f}:{line}) lost vs {parent}:")
        print(f"      {', '.join(lost)}")
    if not overrides:
        print("  none")

    print()
    print("=" * 72)
    print("CHECK 7 - SHADOWED CONFIG (dead key has a hardcoded twin - wire it)")
    print("=" * 72)
    dead_keys_all = [k for _, (dk, _, _) in config_results.items() for k in dk]
    shadows = audit_shadowed_config(dead_keys_all, prod)
    for key, nm, val, f, line in sorted(shadows):
        print(f"  {key:38} shadowed by {nm} = {val}  ({f}:{line})")
        high_findings += 1
    if not shadows:
        print("  none")

    print()
    print("=" * 72)
    print("CHECK 8 - TRANSITIVELY DEAD CONFIG (only consumer is dead/test-only code)")
    print("=" * 72)
    dead_names = {name for name, _, _ in dead}
    testonly_names = {name for name, _, _, _ in test_only}
    transitive = audit_transitive_config(
        config_results, trees_prod, dead_names, testonly_names
    )
    for cf, key, where in sorted(transitive):
        print(f"  {cf}: {key}")
        print(f"      read only inside unwired code: {where}")
        high_findings += 1
    if not transitive:
        print("  none")

    print()
    print("=" * 72)
    print("CHECK 9 - STDOUT PURITY (agent-process modules; __EVENT__ protocol)")
    print("=" * 72)
    stdout_findings, n_reach = audit_stdout_purity(trees_prod)
    for f, line, msg in sorted(stdout_findings):
        print(f"  {f}:{line}  {msg}")
        high_findings += 1
    if not stdout_findings:
        print(f"  none ({n_reach} agent-process modules verified clean)")

    print(f"\n{'=' * 72}\nHIGH-confidence findings: {high_findings}")
    if strict and high_findings:
        sys.exit(1)


if __name__ == "__main__":
    main()
