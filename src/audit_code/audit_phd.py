#!/usr/bin/env python3
"""
audit_phd.py - PhD-standard static review of the Agent Build Engine.  v2

Companion to audit_wiring.py (which finds DISCONNECTED things). This one
finds code that would fail the PhD review rubric: the 5-dimension standard
from prompts/*_phd.md plus the concrete defect patterns from the
architecture audit. It encodes every pattern that is statically checkable;
the rest (semantic invariants, "would a senior approve this") still needs
the judger and human eyes.

WHAT CHANGED IN v2 (precision fixes + coverage)
===============================================
Precision (false positives removed):
  - C4 no longer flags `open(path, "w").close()` - that's the touch-file
    idiom, not a leak. Only unclosed handles and read/write chains flag.
  - F3 no longer flags code under `if __name__ == "__main__":` - that is
    NOT import time.
  - C5 (TOCTOU) skips removes already guarded by try/except OSError -
    the race is handled; only unguarded check-then-act flags.
  - C2 downgrades to INFO when the try body is pure logging/emit -
    swallowing a failed log line is best-effort, not data loss.
  - F2 tags lazy-init caches (`global X` + `if X is None:`) separately
    from genuinely shared mutable state.
  - D4 treats config/settings.py as config layer (defaults tables allowed).

New coverage:
  - SEC1 subprocess shell=True            (audit SEC-02: RCE class)   HIGH
  - SEC2 eval()/exec()/exec_module/pickle (audit S10-04)              HIGH
  - B1   mutable default arguments        (silent shared-state bugs)  HIGH
  - B2   requests/session calls w/o timeout=  (hangs forever)         MEDIUM
  - B3   daemon threads (uncancellable - need cooperative cancel)     INFO
  - G1   hardcoded tuning knobs outside config (audit S2-01/02/03:
         *_TRIGGER/_THRESHOLD/_COOLDOWN/_RATIO/MAX_*/... = numbers
         that should live in settings)                                MEDIUM
  - G2   module-level mutable containers mutated from code; tagged
         "unbounded growth?" when no pop/clear/eviction exists in the
         file (audit S6-04: _loggers grows forever)                   MEDIUM
  - T1   prod modules never referenced by any test (Dimension 2
         proxy: an invariant can't be proven by tests that don't
         import the module)                                           MEDIUM
  - DG1  god functions (>120 lines), god files (>900 lines), god
         classes (>25 methods) (Dimension 4 / audit standard 4)       MEDIUM

WHAT CHANGED IN v3 (prompt-rubric sync)
=======================================
  - T2   public defs referenced by NO test (per-def version of T1;
         system_phd.md #2/#4: "unrun code is not done")               MEDIUM
  - T3   defs that ARE tested but only happy-path: no referencing
         test function uses pytest.raises / assertRaises or feeds a
         None / "" / <=0 / empty-container / negative argument
         (judger.md hard cap: happy-path-only tests -> at most 70)    MEDIUM
  - E1i  prompt getters wrapped in @cache/@lru_cache: frozen for the
         process lifetime, prompt_loader's mtime hot-reload never
         re-fires after the first call (E1's runtime-shaped twin)     INFO
  - KNOB_NAME_RE: name-final _MAX now matches (was _MAX_ only,
         asymmetric with _MIN).

WHAT CHANGED IN v3.1 (gap batch: the 3am failure modes)
=======================================================
  - SEC3 hardcoded credentials in source: token shapes (sk-/AKIA/ghp_/
         xox/AIza/PRIVATE KEY) + secret-named assignments with literal
         values. R7 (runtime) only covered secrets reaching LOGS.       HIGH
  - C6   bare ["key"] indexing on dicts parsed from LLM output
         (parse_json/_parse_json/json.loads of model text; file reads
         excluded) - an omitted or hallucinated field is a mid-run
         KeyError instead of a graceful fallback                        MEDIUM
  - T4   assertion-free tests: no assert / pytest.raises / mock
         assert_* / asserting same-file helper (one hop). Green
         forever, proves nothing - and load-bearing for T2/T3, since
         an assertion-free test still "covers" a def name-wise         MEDIUM
  - T5   monkeypatch.setattr / mock.patch targets that do not exist at
         the target module's top level (the _path -> _db_path drift
         class this repo hit). Resolves import aliases and
         _imp()/import_module() wrappers; modules defining __getattr__
         are skipped (dynamic attrs); class targets are skipped        MEDIUM

WHAT CHANGED IN v3.2 (roadmap checklist batch)
==============================================
  - SEC6 SQL built with f-string/%/.format/concat passed to execute();
         only strings containing an SQL keyword flag, so non-DB
         .execute() methods stay quiet                                  HIGH
  - SEC7 DEBUG = True in a settings module (dev/local/test settings
         variants exempt)                                               MEDIUM
  - B5   assert used for runtime validation - python -O strips
         asserts; isinstance/is-not-None narrowing idioms exempt        MEDIUM
  - R10  logging.basicConfig() called more than once project-wide -
         only the first call takes effect                               MEDIUM

Workflow:
  - Inline suppression: put  # audit: ok  on the flagged line.
  - --json  emits machine-readable findings (for CI diffing).
  - --strict exits 1 if any HIGH finding survives suppression.

CHECK -> RUBRIC MAP
===================
Dimension 1 - Correctness ("no silent wrong answers"):
  C1 bare except | C2 except:pass | C3 silent fallback returns
  C4 open() discipline | C5 unguarded TOCTOU | C6 LLM-dict bare indexing
Dimension 2 - Invariant coverage (proxy):
  T1 untested modules | T2 untested public defs | T3 happy-path-only defs
  T4 assertion-free tests | T5 patch-target drift
Dimension 3 - Failure handling / concurrency:
  F1 unacquired locks | F2 global state | F3 import-time side effects
  F4 bare cfg[...]/environ[...] indexing | B3 daemon threads
Dimension 4 - Design quality / drift:
  D1 duplicate functions | D2 import cycles | D3 flat imports
  D4 hardcoded models | D5 scattered env reads | DG1 god units
  G1 hardcoded tuning knobs | G2 shared mutable module state
Dimension 5 - Documentation:
  DOC public defs without docstrings
Performance (audit Phase 4):
  P1 imports in loops | P2 imports in functions | P3 re.compile in fn
  P4 settings lookups in loops
Security (audit Phase 1/2):
  SEC1 shell=True | SEC2 dynamic code execution | B2 no-timeout HTTP
  SEC3 hardcoded credentials | SEC6 string-built SQL | SEC7 DEBUG=True
Engine regressions:
  E1 prompts frozen at import | E2 hook prompts missing {task}

KNOWN LIMITS: name-level analysis (renamed handles can fool F1); cannot
verify an invariant HAS a test, only that the module is test-touched (T1)
and the def's NAME appears in a test (T2/T3 — indirect coverage through a
public entry point is not attributed, and shared names look tested);
semantic dead fields and gold-plating remain human/judger work.
"""

import ast
import collections
import json
import re
import sys
from pathlib import Path

from audit_code.audit_shared import should_audit

ROOT = Path(__file__).resolve().parent.parent.parent
# Allow --path override for audit-code wrapper
for _i, _a in enumerate(sys.argv):
    if _a == "--path" and _i + 1 < len(sys.argv):
        ROOT = Path(sys.argv[_i + 1]).resolve()
        break
SELF_NAMES = {
    "audit_phd.py",
    "audit_wiring.py",
    "audit_runtime.py",
    "run_all_audits.py",
    "audit_config.py",
    "config.py",
    "audit_gate.py",
    "audit_deps.py",
}

STRICT_INDEX_RECEIVERS = {"cfg", "environ"}
ENV_ALLOWED_FILES = {"settings.py", "providers.py", "run_entry.py"}
MODEL_ALLOWED_FILES = {"providers.py", "tracker.py", "settings.py"}  # config layer

MODEL_STRING_RE = re.compile(
    r"""["'](deepseek-[\w.\-]+|claude-[\w.\-]+|gpt-[\w.\-]+|gemini-[\w.\-]+|"""
    r"""grok-[\w.\-]+|o[13]-[\w.\-]+|sonar[\w.\-]*|mistral-[\w.\-]+)["']"""
)

KNOB_NAME_RE = re.compile(
    r"(TRIGGER|THRESHOLD|WEIGHT|COOLDOWN|RATIO|WINDOW|DELTA|TIMEOUT|"
    r"^MAX_|_MAX\b|^MIN_|_MIN\b|LIMIT)"
)

# SEC3: known token shapes + secret-named assignment with a literal value.
SECRET_TOKEN_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|"
    r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}|"
    r"AIza[0-9A-Za-z_\-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"okta_[A-Za-z0-9_\-]{20,}"  # Okta API tokens
)
SECRET_ASSIGN_RE = re.compile(
    r"""(?i)\b\w*(?:api_?key|secret|password|passwd|auth_token|access_token|"""
    r"""okta_token|okta_secret|okta_client_id)\w*"""
    r"""["']?\s*[:=]\s*["']([^"']{8,})["']"""
)
SECRET_PLACEHOLDER_RE = re.compile(
    r"(?i)your|xxx|<|\{|\benv\b|dummy|example|placeholder|fake|test|sample|"
    r"redact|mask|\*\*\*|\s|://|^[/\\]|^[A-Za-z]:[\\/]"
)  # last 3: URL/path, not a secret

LOG_CALL_NAMES = {
    "log",
    "warning",
    "warn",
    "error",
    "exception",
    "debug",
    "print",
    "emit",
    "info",
    "critical",
}
EMITTY = LOG_CALL_NAMES | {
    "trace",
    "event",
    "write_agent_log",
    "write_prompt_audit",
    "flush",
}

IO_CALL_NAMES = {
    "open",
    "mkdir",
    "makedirs",
    "read_text",
    "write_text",
    "subscribe",
    "connect",
    "Popen",
    "load_plugins",
    "listdir",
}

HTTP_RECEIVERS = {"requests", "session", "_session", "client", "http"}
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "request"}

GUARD_EXC = {
    "OSError",
    "FileNotFoundError",
    "PermissionError",
    "Exception",
    "IOError",
    "BaseException",
}

SUPPRESS_RE = re.compile(r"#\s*audit:\s*ok")


# ──────────────────────────────────────────────────────────────────────────
# collection / annotation
# ──────────────────────────────────────────────────────────────────────────


def is_test(p: Path) -> bool:
    s = str(p).replace("\\", "/")
    return (
        "/tests/" in s
        or p.name.startswith(("test_", "tests_"))
        or p.name == "conftest.py"
    )


def collect():
    prod, test = {}, {}
    for p in ROOT.rglob("*.py"):
        if p.name in SELF_NAMES or not should_audit(p):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        (test if is_test(p) else prod)[p] = txt
    return prod, test


def rel(p):
    return str(p.relative_to(ROOT))


def annotate(tree):
    """Attach .parent / .fn_depth / .loop_depth to every node."""
    tree.fn_depth = 0
    tree.loop_depth = 0
    for node in ast.walk(tree):
        fd = getattr(node, "fn_depth", 0)
        ld = getattr(node, "loop_depth", 0)
        for ch in ast.iter_child_nodes(node):
            ch.parent = node
            ch.fn_depth = fd + (
                1
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                else 0
            )
            ch.loop_depth = ld + (
                1 if isinstance(node, (ast.For, ast.AsyncFor, ast.While)) else 0
            )


def call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def receiver_name(node):
    """Base name of x in x.method(...) - one level."""
    f = node.func
    if isinstance(f, ast.Attribute):
        v = f.value
        if isinstance(v, ast.Name):
            return v.id
        if isinstance(v, ast.Attribute):
            return v.attr
    return ""


def ancestors(node):
    cur = getattr(node, "parent", None)
    while cur is not None:
        yield cur
        cur = getattr(cur, "parent", None)


def guarded_against_oserror(node):
    """True if node sits inside a Try whose handlers catch OSError-ish."""
    for anc in ancestors(node):
        if isinstance(anc, ast.Try):
            for h in anc.handlers:
                if h.type is None:
                    return True
                names = []
                if isinstance(h.type, ast.Name):
                    names = [h.type.id]
                elif isinstance(h.type, ast.Tuple):
                    names = [e.id for e in h.type.elts if isinstance(e, ast.Name)]
                if any(n in GUARD_EXC for n in names):
                    return True
    return False


def handler_has_logging(handler):
    for n in ast.walk(handler):
        if isinstance(n, ast.Call) and call_name(n) in LOG_CALL_NAMES:
            return True
        if isinstance(n, ast.Raise):
            return True
    return False


def try_body_is_besteffort(handler):
    """True when the guarded try-body is pure logging/emit - swallowing is OK."""
    tr = getattr(handler, "parent", None)
    if not isinstance(tr, ast.Try):
        return False
    for st in tr.body:
        for n in ast.walk(st):
            if (
                isinstance(n, ast.Call)
                and call_name(n) not in EMITTY
                and call_name(n)
                not in (
                    "get_logger",
                    "get_active_trace",
                    "json",
                    "dumps",
                    "str",
                    "round",
                    "len",
                )
            ):
                return False
    return True


def in_with_items(node):
    cur, _ = getattr(node, "parent", None), node
    while cur is not None:
        if isinstance(cur, (ast.With, ast.AsyncWith)):
            for item in cur.items:
                if any(n is node for n in ast.walk(item.context_expr)):
                    return True
        _, cur = cur, getattr(cur, "parent", None)
    return False


def is_main_guard(node):
    """if __name__ == "__main__": - its body is not import-time."""
    if not isinstance(node, ast.If):
        return False
    t = node.test
    return (
        isinstance(t, ast.Compare)
        and isinstance(t.left, ast.Name)
        and t.left.id == "__name__"
    )


def names_in(node):
    """Every identifier a subtree REFERENCES: name loads, attrs, id-shaped
    strings, and import targets. Store-context Names are bindings, not
    references - a test-local `allocate = 5` must not bless a prod def -
    and `from x import f as g` must keep `f` alive even when only `g` is
    called."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            if isinstance(n.ctx, ast.Load):
                out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add(a.name.split(".")[-1])
        elif (
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and 2 < len(n.value) < 60
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n.value)
        ):
            out.add(n.value)
    return out


def reads_file(call):
    """json.loads(<file read>) is config parsing, not LLM output - skip C6."""
    for n in ast.walk(call):
        if isinstance(n, ast.Attribute) and n.attr in ("read_text", "read"):
            return True
        if isinstance(n, ast.Call) and call_name(n) == "open":
            return True
    return False


def fn_asserts(fn, helper_names=frozenset()):
    """True when a test function proves something: assert statement,
    pytest.raises/warns, unittest/mock assert_*, or a call to a same-file
    helper known to assert (one hop)."""
    for s in ast.walk(fn):
        if isinstance(s, ast.Assert):
            return True
        if isinstance(s, ast.Attribute) and (
            s.attr in ("raises", "warns") or s.attr.startswith("assert")
        ):
            return True
        if isinstance(s, ast.Call):
            cn = call_name(s)
            if (
                cn in ("raises", "warns", "fail")
                or cn.startswith("assert")
                or cn in helper_names
                or cn.startswith(("skip", "xfail", "importorskip"))
            ):
                return True
    return False


def has_edge_signal(fn):
    """True when a test function exercises a failure/edge path: pytest.raises /
    assertRaises, or ANY call fed None / "" / <=0 / negative / empty container.
    Generous on purpose - one edge-ish input blesses every name the test touches,
    so T3 only fires on defs with NO edge exposure at all."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Attribute) and n.attr == "raises":
            return True
        if not isinstance(n, ast.Call):
            continue
        if call_name(n) in ("raises", "assertRaises", "assertRaisesRegex"):
            return True
        for a in list(n.args) + [kw.value for kw in n.keywords]:
            if isinstance(a, ast.Constant):
                v = a.value
                if (
                    v is None
                    or v == ""
                    or (
                        isinstance(v, (int, float))
                        and not isinstance(v, bool)
                        and v <= 0
                    )
                ):
                    return True
            elif isinstance(a, (ast.List, ast.Tuple, ast.Set)) and not a.elts:
                return True
            elif isinstance(a, ast.Dict) and not a.keys:
                return True
            elif isinstance(a, ast.UnaryOp) and isinstance(a.op, ast.USub):
                return True
    return False


# ──────────────────────────────────────────────────────────────────────────
# finding sink with inline suppression
# ──────────────────────────────────────────────────────────────────────────


class Sink:
    def __init__(self):
        self.data = collections.defaultdict(list)
        self.lines = {}  # relpath -> [source lines]
        self.suppressed = 0

    def register(self, relpath, text):
        self.lines[relpath] = text.splitlines()

    def add(self, cid, f, line, msg):
        src = self.lines.get(f, [])
        if 0 < line <= len(src) and SUPPRESS_RE.search(src[line - 1]):
            self.suppressed += 1
            return
        self.data[cid].append((f, line, msg))


def main():
    strict = "--strict" in sys.argv
    as_json = "--json" in sys.argv
    # --min-severity HIGH: suppress MEDIUM/INFO, only report HIGH
    min_sev = next(
        (a.split("=")[1] for a in sys.argv if a.startswith("--min-severity=")), None
    )
    prod, tests = collect()
    trees = {}
    sink = Sink()
    for p, txt in prod.items():
        sink.register(rel(p), txt)
        try:
            t = ast.parse(txt)
            annotate(t)
            trees[p] = t
        except SyntaxError as e:
            print(f"[warn] cannot parse {rel(p)}: {e}")

    # ── node-level checks ────────────────────────────────────────────────
    for p, tree in trees.items():
        f = rel(p)
        txt = prod[p]
        for node in ast.walk(tree):

            # C1 / C2 / C3 - exception discipline
            if isinstance(node, ast.ExceptHandler):
                bare = node.type is None
                broad = isinstance(node.type, ast.Name) and node.type.id in (
                    "Exception",
                    "BaseException",
                )
                body_is_pass = all(isinstance(b, ast.Pass) for b in node.body)
                if bare:
                    sink.add("C1", f, node.lineno, "bare `except:`")
                if (bare or broad) and body_is_pass:
                    if try_body_is_besteffort(node):
                        sink.add(
                            "C2i",
                            f,
                            node.lineno,
                            "swallowed, but try-body is pure logging (best-effort)",
                        )
                    else:
                        sink.add(
                            "C2", f, node.lineno, "exception swallowed with `pass`"
                        )
                elif not body_is_pass and not handler_has_logging(node):
                    if any(isinstance(b, ast.Return) for b in node.body) and (
                        bare or broad
                    ):
                        sink.add(
                            "C3",
                            f,
                            node.lineno,
                            "handler returns fallback, never logs (silent wrong answer)",
                        )

            # C4 - open() discipline (touch idiom `open(...).close()` is fine)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"
            ):
                parent = getattr(node, "parent", None)
                if isinstance(parent, ast.Attribute):
                    if parent.attr != "close":
                        sink.add(
                            "C4",
                            f,
                            node.lineno,
                            f"open(...).{parent.attr}() - fd leak on error path",
                        )
                elif not in_with_items(node):
                    sink.add("C4", f, node.lineno, "open() outside `with`")

            # F2 - global statements (lazy-init caches tagged separately)
            if isinstance(node, ast.Global):
                fn = next(
                    (
                        a
                        for a in ancestors(node)
                        if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ),
                    None,
                )
                lazy = False
                if fn is not None:
                    for sub in ast.walk(fn):
                        if isinstance(sub, ast.If):
                            d = ast.dump(sub.test)
                            if any(n in d for n in node.names) and (
                                "Is()" in d or "IsNot()" in d or "Not()" in d
                            ):
                                lazy = True
                                break
                cid = "F2i" if lazy else "F2"
                tag = " (lazy-init cache)" if lazy else ""
                sink.add(cid, f, node.lineno, f"global {', '.join(node.names)}{tag}")

            # F4 - bare config indexing
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
                and isinstance(node.ctx, ast.Load)
            ):
                v = node.value
                recv = (
                    v.id
                    if isinstance(v, ast.Name)
                    else (v.attr if isinstance(v, ast.Attribute) else "")
                )
                if recv in STRICT_INDEX_RECEIVERS:
                    sink.add(
                        "F4",
                        f,
                        node.lineno,
                        f'{recv}["{node.slice.value}"] - use .get()',
                    )

            # P1 / P2 - inline imports
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.fn_depth > 0:
                mod = getattr(node, "module", None) or ",".join(
                    a.name for a in node.names
                )
                if node.loop_depth > 0:
                    sink.add("P1", f, node.lineno, f"import {mod} INSIDE LOOP")
                else:
                    sink.add("P2", f, node.lineno, f"import {mod}")

            # P3 - re.compile in functions
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "compile"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
                and node.fn_depth > 0
            ):
                sink.add("P3", f, node.lineno, "re.compile() inside function")

            # P4 - settings lookups in loops
            if (
                isinstance(node, ast.Call)
                and call_name(node) in ("get_settings", "load_sandbox_config")
                and node.loop_depth > 0
            ):
                sink.add("P4", f, node.lineno, f"{call_name(node)}() inside loop")

            # E1 - prompts frozen at import
            if isinstance(node, ast.Assign) and node.fn_depth == 0:
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Call) and call_name(sub) in (
                        "prompt_for_role",
                        "prompt",
                        "_load_prompt",
                    ):
                        sink.add(
                            "E1",
                            f,
                            node.lineno,
                            "prompt frozen at import - defeats hot-reload",
                        )
                        break

            # E1i - @cache/@lru_cache prompt getters: frozen after the FIRST
            # call, so prompt_loader's mtime hot-reload never re-fires. Same
            # regression class as E1, one call later.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decs = set()
                for d in node.decorator_list:
                    t = d.func if isinstance(d, ast.Call) else d
                    decs.add(
                        t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", "")
                    )
                if decs & {"cache", "lru_cache", "cached_property"}:
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call) and call_name(sub) in (
                            "prompt_for_role",
                            "prompt",
                            "_load_prompt",
                        ):
                            sink.add(
                                "E1i",
                                f,
                                node.lineno,
                                f"@cache on {node.name}() freezes the prompt for "
                                f"the process lifetime - .md edits never reload",
                            )
                            break

            # E2 - hook prompts missing {task}
            if (
                isinstance(node, ast.Call)
                and call_name(node) == "register_raw_prompt"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and "{task}" not in node.args[1].value
            ):
                sink.add("E2", f, node.lineno, "hook prompt has no {task} placeholder")

            # D5 - env reads outside config layer
            if p.name not in ENV_ALLOWED_FILES:
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getenv"
                ):
                    sink.add("D5", f, node.lineno, "os.getenv() outside config layer")
                if (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "environ"
                    and isinstance(node.ctx, ast.Load)
                ):
                    sink.add(
                        "D5", f, node.lineno, "os.environ[...] outside config layer"
                    )

            # SEC1 - shell=True
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        sink.add(
                            "SEC1",
                            f,
                            node.lineno,
                            f"{call_name(node)}(shell=True) - command injection surface",
                        )

            # SEC2 - dynamic code execution
            if isinstance(node, ast.Call):
                cn = call_name(node)
                if isinstance(node.func, ast.Name) and cn in ("eval", "exec"):
                    sink.add("SEC2", f, node.lineno, f"{cn}() - dynamic code execution")
                elif cn == "exec_module":
                    sink.add(
                        "SEC2",
                        f,
                        node.lineno,
                        "exec_module() - plugin code runs unsandboxed",
                    )
                elif cn == "load" and receiver_name(node) == "pickle":
                    sink.add(
                        "SEC2",
                        f,
                        node.lineno,
                        "pickle.load() - arbitrary object execution",
                    )

            # B1 - mutable default arguments
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in list(node.args.defaults) + list(node.args.kw_defaults):
                    if d is None:
                        continue
                    if isinstance(d, (ast.Dict, ast.List, ast.Set)) or (
                        isinstance(d, ast.Call)
                        and call_name(d) in ("dict", "list", "set")
                    ):
                        sink.add(
                            "B1",
                            f,
                            node.lineno,
                            f"{node.name}() has a mutable default argument",
                        )
                        break

            # B2 - HTTP calls without timeout
            if (
                isinstance(node, ast.Call)
                and call_name(node) in HTTP_METHODS
                and receiver_name(node) in HTTP_RECEIVERS
            ):
                if not any(kw.arg == "timeout" for kw in node.keywords):
                    sink.add(
                        "B2",
                        f,
                        node.lineno,
                        f"{receiver_name(node)}.{call_name(node)}() without timeout= (can hang forever)",
                    )

            # B3 - daemon threads
            if isinstance(node, ast.Call) and call_name(node) == "Thread":
                for kw in node.keywords:
                    if (
                        kw.arg == "daemon"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        sink.add(
                            "B3",
                            f,
                            node.lineno,
                            "daemon thread - cannot be joined/cancelled; needs cooperative stop",
                        )

            # G1 - hardcoded tuning knobs outside config/
            if (
                isinstance(node, ast.Assign)
                and node.fn_depth == 0
                and not str(p)
                .replace("\\", "/")
                .startswith(str(ROOT / "config").replace("\\", "/"))
            ):
                tgt = node.targets[0]
                if (
                    isinstance(tgt, ast.Name)
                    and KNOB_NAME_RE.search(tgt.id)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, (int, float))
                    and not isinstance(node.value.value, bool)
                ):
                    sink.add(
                        "G1",
                        f,
                        node.lineno,
                        f"{tgt.id} = {node.value.value} - tuning knob hardcoded (move to settings)",
                    )

        # ── file-level checks ────────────────────────────────────────────

        # F1 - locks never acquired (same file)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and call_name(node.value) in ("Lock", "RLock")
            ):
                tgt = node.targets[0]
                name = None
                if isinstance(tgt, ast.Attribute):
                    name = (
                        f"self.{tgt.attr}"
                        if isinstance(tgt.value, ast.Name) and tgt.value.id == "self"
                        else tgt.attr
                    )
                elif isinstance(tgt, ast.Name):
                    name = tgt.id
                if not name:
                    continue
                short = name.split(".")[-1]
                if not (
                    f"with {name}" in txt
                    or f"{short}.acquire" in txt
                    or f"with self.{short}" in txt
                    or f"with {short}" in txt
                ):
                    sink.add(
                        "F1",
                        f,
                        node.lineno,
                        f"{name} = Lock() - never acquired in this file",
                    )

        # F5 - lock ordering (same function, different branches)
        # Detects: branch A acquires X then Y, branch B acquires Y then X → deadlock
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Collect lock acquisition sequences per branch
            acquires: list[list[str]] = []  # each branch = ordered list of lock names
            for sub in ast.walk(node):
                if isinstance(sub, ast.With):
                    for item in sub.items:
                        ctx = item.context_expr
                        name = None
                        if isinstance(ctx, ast.Name):
                            name = ctx.id
                        elif isinstance(ctx, ast.Attribute):
                            name = ctx.attr
                        if name:
                            acquires.append([name])
                elif isinstance(sub, ast.Call) and call_name(sub) == "acquire":
                    rcv = receiver_name(sub)
                    if rcv:
                        acquires.append([rcv])
                elif isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Call):
                    cn = call_name(sub.value)
                    if cn == "acquire":
                        rcv = receiver_name(sub.value)
                        if rcv:
                            acquires.append([rcv])

            if len(acquires) < 2:
                continue
            # Check for ordering conflicts: any pair (X,Y) where X before Y in one
            # branch and Y before X in another
            seen_pairs: set[tuple[str, str]] = set()
            for i in range(len(acquires) - 1):
                a, b = acquires[i][0], acquires[i + 1][0]
                if a == b:
                    continue
                if (b, a) in seen_pairs:
                    sink.add(
                        "F5",
                        f,
                        node.lineno,
                        f"potential deadlock: locks acquired in inconsistent order "
                        f"({a}→{b} vs {b}→{a}) in function {node.name}()",
                    )
                    break
                seen_pairs.add((a, b))

        # R9 - broken structured logging (log.info(msg, key=val))
        LOG_LEVELS = frozenset(
            ("debug", "info", "warning", "warn", "error", "critical", "exception")
        )
        VALID_KWARGS = frozenset(("exc_info", "stack_info", "stacklevel", "extra"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            cn = call_name(node)
            if cn not in LOG_LEVELS:
                continue
            rcv = receiver_name(node)
            if not rcv or rcv not in ("log", "logger", "LOGGER", "_log"):
                if not rcv:
                    continue
            for kw in node.keywords:
                if kw.arg not in VALID_KWARGS:
                    sink.add(
                        "R9",
                        f,
                        node.lineno,
                        f"log.{cn}(..., {kw.arg}=) — {kw.arg} is not a valid logging keyword; "
                        f"this will raise TypeError at runtime. Use extra={{{kw.arg}: ...}}",
                    )
                    break

        # SEC5 - SQLite without FK enforcement
        has_sqlite = "sqlite" in txt.lower()
        has_fk = "PRAGMA foreign_keys" in txt or "foreign_keys" in txt.lower()
        if has_sqlite and not has_fk and "create_engine" in txt:
            sink.add(
                "SEC5",
                f,
                1,
                "SQLite engine without PRAGMA foreign_keys=ON — FK constraints silently ignored",
            )

        # F3 - import-time side effects (main-guard excluded)
        def top_stmts(body):
            for st in body:
                if is_main_guard(st):
                    continue
                yield st
                if isinstance(st, (ast.If, ast.Try)):
                    yield from top_stmts(st.body)
                    for h in getattr(st, "handlers", []):
                        yield from top_stmts(h.body)
                    yield from top_stmts(getattr(st, "orelse", []))
                    yield from top_stmts(getattr(st, "finalbody", []))

        for st in top_stmts(tree.body):
            if isinstance(
                st,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Import,
                    ast.ImportFrom,
                ),
            ):
                continue
            for sub in ast.walk(st):
                if isinstance(sub, ast.Call) and call_name(sub) in IO_CALL_NAMES:
                    sink.add(
                        "F3", f, st.lineno, f"import-time call: {call_name(sub)}()"
                    )
                    break

        # C5 - TOCTOU (unguarded only)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                exists_at = {}
                uses = []
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and sub.args:
                        cn = call_name(sub)
                        key = ast.dump(sub.args[0])
                        if cn == "exists":
                            exists_at[key] = sub.lineno
                        elif cn in ("remove", "unlink", "rmdir"):
                            uses.append((key, sub))
                for key, use in uses:
                    if key in exists_at and not guarded_against_oserror(use):
                        sink.add(
                            "C5",
                            f,
                            exists_at[key],
                            f"exists()@{exists_at[key]} then {call_name(use)}()@{use.lineno} unguarded - TOCTOU",
                        )

        # C6 - LLM-parsed dicts indexed bare. The model was only ASKED to
        # return the field; a missing/hallucinated key is a KeyError mid-run.
        c6_seen = set()  # dedupe: same-line repeats + nested-def double-walk
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            tainted = set()
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Assign)
                    and isinstance(sub.value, ast.Call)
                    and len(sub.targets) == 1
                    and isinstance(sub.targets[0], ast.Name)
                ):
                    cn = call_name(sub.value)
                    if cn in ("parse_json", "_parse_json") or (
                        cn == "loads"
                        and receiver_name(sub.value) in ("json", "")
                        and not reads_file(sub.value)
                    ):
                        tainted.add(sub.targets[0].id)
            if not tainted:
                continue
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Subscript)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id in tainted
                    and isinstance(sub.ctx, ast.Load)
                    and isinstance(sub.slice, ast.Constant)
                    and isinstance(sub.slice.value, str)
                ):
                    hit = (sub.lineno, sub.value.id, sub.slice.value)
                    if hit in c6_seen:
                        continue
                    c6_seen.add(hit)
                    sink.add(
                        "C6",
                        f,
                        sub.lineno,
                        f'{sub.value.id}["{sub.slice.value}"] on an LLM-parsed '
                        f"dict - use .get() or validate the schema first",
                    )

        # G2 - shared mutable module state (+ unbounded growth tag)
        for st in tree.body:
            tgt = None
            val = None
            if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name):
                tgt, val = st.targets[0].id, st.value
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                tgt, val = st.target.id, st.value
            if tgt is None or val is None or tgt == "__all__":
                continue
            if isinstance(val, (ast.Dict, ast.List, ast.Set)) or (
                isinstance(val, ast.Call)
                and call_name(val)
                in ("dict", "list", "set", "defaultdict", "deque", "Counter")
            ):
                mutated = re.search(
                    rf"\b{re.escape(tgt)}\s*\[[^\]]+\]\s*=|"
                    rf"\b{re.escape(tgt)}\.(append|add|update|setdefault|extend|insert)\b",
                    txt,
                )
                if mutated:
                    evicted = re.search(
                        rf"\b{re.escape(tgt)}\.(pop|clear|popitem)\b|"
                        rf"\bdel\s+{re.escape(tgt)}\[|maxlen",
                        txt,
                    )
                    tag = "" if evicted else "  [unbounded growth?]"
                    sink.add(
                        "G2",
                        f,
                        st.lineno,
                        f"module-level mutable `{tgt}` mutated from code{tag}",
                    )

        # D4 - hardcoded model strings
        if p.name not in MODEL_ALLOWED_FILES:
            for m in MODEL_STRING_RE.finditer(txt):
                line = txt.count("\n", 0, m.start()) + 1
                sink.add("D4", f, line, f"hardcoded model {m.group(1)!r}")

        # SEC3 - hardcoded credentials (R7 covers secrets reaching logs;
        # this covers secrets living in source)
        for m in SECRET_TOKEN_RE.finditer(txt):
            line = txt.count("\n", 0, m.start()) + 1
            sink.add(
                "SEC3", f, line, f"credential-shaped literal {m.group(0)[:24]!r}..."
            )
        for m in SECRET_ASSIGN_RE.finditer(txt):
            if SECRET_PLACEHOLDER_RE.search(m.group(1)):
                continue
            line = txt.count("\n", 0, m.start()) + 1
            sink.add(
                "SEC3",
                f,
                line,
                f"secret-named binding with a literal value: {m.group(0)[:48]!r}",
            )

        # LANG1 — ChatOpenAI / AzureChatOpenAI without temperature=.
        # LLM calls without explicit temperature default to the model's
        # built-in default (often 0.7-1.0), making outputs nondeterministic.
        # Flag as MEDIUM — deliberate creativity is valid, but absence is
        # worth surfacing.
        LLM_CTOR_NAMES = {
            "ChatOpenAI",
            "AzureChatOpenAI",
            "ChatAnthropic",
            "ChatGoogleGenerativeAI",
            "ChatVertexAI",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            cn = call_name(node)
            if cn not in LLM_CTOR_NAMES:
                continue
            if any(kw.arg == "temperature" for kw in node.keywords):
                continue
            sink.add(
                "LANG1",
                f,
                node.lineno,
                f"{cn}() instantiated without temperature= — "
                "outputs will be nondeterministic in production. "
                "Set temperature=0 for deterministic/auditable responses, "
                "or suppress with # audit: ok if creativity is intentional",
            )

        # BOTTLE1 — await in a for/while loop. Sequential awaits kill
        # throughput when the calls are independent. Use asyncio.gather()
        # for parallelism unless ordering is required.
        for node in ast.walk(tree):
            if not isinstance(node, (ast.For, ast.While)):
                continue
            # Walk loop body looking for Await
            has_await = False
            for child in ast.walk(node):
                if isinstance(child, ast.Await):
                    has_await = True
                    break
            if not has_await:
                continue
            # Exclude loops where the await is inside asyncio.gather or
            # as_completed — those ARE the parallel pattern.
            gather_found = False
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and call_name(child) in ("gather", "as_completed")
                    and receiver_name(child) in ("asyncio", "")
                ):
                    gather_found = True
                    break
            if gather_found:
                continue
            sink.add(
                "BOTTLE1",
                f,
                node.lineno,
                "await inside a loop — sequential async calls kill "
                "throughput. Use asyncio.gather() for independent tasks, "
                "or suppress with # audit: ok if ordering is required",
            )

        # BOTTLE2 — sync blocking I/O inside async def. These block the
        # event loop, starving other coroutines. Use async equivalents
        # (aiofiles, httpx.AsyncClient, motor for MongoDB, etc.).
        BLOCKING_CALLS = {
            "time.sleep",
            "sleep",
            "requests.get",
            "requests.post",
            "requests.put",
            "requests.delete",
            "requests.patch",
            "requests.request",
            "urllib.request.urlopen",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            # Walk the async function body looking for sync blocking calls
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                cn = call_name(child)
                rcv = receiver_name(child)
                full = f"{rcv}.{cn}" if rcv else cn
                if full not in BLOCKING_CALLS and cn not in BLOCKING_CALLS:
                    continue
                # Exclude time.sleep(0) — deliberate yield pattern
                if cn in ("sleep", "time.sleep") and child.args:
                    arg0 = child.args[0]
                    if (
                        isinstance(arg0, ast.Constant)
                        and isinstance(arg0.value, (int, float))
                        and arg0.value == 0
                    ):
                        continue
                sink.add(
                    "BOTTLE2",
                    f,
                    child.lineno,
                    f"sync blocking call {full}() inside async def "
                    f"{node.name}() — blocks the event loop. Use async "
                    "equivalent (httpx.AsyncClient, aiofiles, motor)",
                )

        # DG1 - god functions / classes; god files below (cross-file)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = (node.end_lineno or node.lineno) - node.lineno
                if span > 120:
                    sink.add(
                        "DG1",
                        f,
                        node.lineno,
                        f"{node.name}() is {span} lines - decompose",
                    )
            if isinstance(node, ast.ClassDef):
                n_methods = sum(
                    isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for m in node.body
                )
                if n_methods > 25:
                    sink.add(
                        "DG1",
                        f,
                        node.lineno,
                        f"class {node.name} has {n_methods} methods - split concerns",
                    )
        n_lines = txt.count("\n") + 1
        if n_lines > 900:
            sink.add("DG1", f, 1, f"file is {n_lines} lines - god file")

        # C7 — rmtree without OSError guard
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and call_name(node) == "rmtree":
                if receiver_name(node) in ("shutil", ""):
                    if not guarded_against_oserror(node):
                        # ignore_errors=True already handles the error
                        if any(
                            kw.arg == "ignore_errors"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                            for kw in node.keywords
                        ):
                            continue
                        sink.add(
                            "C7",
                            f,
                            node.lineno,
                            "shutil.rmtree() without try/except OSError — "
                            "permission errors crash the process",
                        )

        # C8 — except: continue (silently discards errors)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if len(node.body) == 1 and isinstance(node.body[0], ast.Continue):
                    # Only flag bare except or except Exception (not specific types)
                    if node.type is None or (
                        isinstance(node.type, ast.Name) and node.type.id == "Exception"
                    ):
                        sink.add(
                            "C8",
                            f,
                            node.lineno,
                            "except: continue silently discards errors — "
                            "at minimum log the exception",
                        )

        # C9 — float equality comparison
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                if any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                    has_float = any(
                        isinstance(c, ast.Constant) and isinstance(c.value, float)
                        for c in ([node.left] + list(node.comparators))
                    )
                    if has_float:
                        sink.add(
                            "C9",
                            f,
                            node.lineno,
                            "float == comparison — floating-point rounding "
                            "makes equality unreliable (0.1+0.2 != 0.3)",
                        )

        # SEC4 — yaml.load() without SafeLoader
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and call_name(node) == "load":
                if receiver_name(node) in ("yaml", ""):
                    has_safeloader = any(
                        kw.arg == "Loader"
                        and isinstance(kw.value, ast.Attribute)
                        and kw.value.attr == "SafeLoader"
                        for kw in node.keywords
                    )
                    if not has_safeloader:
                        sink.add(
                            "SEC4",
                            f,
                            node.lineno,
                            "yaml.load() without Loader=yaml.SafeLoader — "
                            "arbitrary object deserialization (RCE risk)",
                        )

        # B4 — tempfile.mktemp() / os.tempnam() (TOCTOU race)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                cn = call_name(node)
                if cn == "mktemp" and receiver_name(node) in ("tempfile", ""):
                    sink.add(
                        "B4",
                        f,
                        node.lineno,
                        "tempfile.mktemp() is race-prone — use "
                        "tempfile.mkstemp() or TemporaryFile instead",
                    )
                elif cn == "tempnam" and receiver_name(node) in ("os", ""):
                    sink.add(
                        "B4",
                        f,
                        node.lineno,
                        "os.tempnam() is race-prone — use "
                        "tempfile.mkstemp() instead",
                    )

        # G3 — __init__ returning non-None (TypeError at runtime)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and sub.value is not None:
                        if not (
                            isinstance(sub.value, ast.Constant)
                            and sub.value.value is None
                        ):
                            sink.add(
                                "G3",
                                f,
                                sub.lineno,
                                "__init__ returns non-None — causes "
                                "TypeError at instantiation",
                            )

        # SEC6 — SQL statement built with f-string/%/.format/concat passed
        # to execute(). Only strings containing an SQL keyword flag, so a
        # generic .execute() method on a non-DB object stays quiet.
        SQL_WORDS = (
            "select ",
            "insert ",
            "update ",
            "delete ",
            "create ",
            "drop ",
            "exec ",
            "sp_executesql",
            "execute ",
            "merge ",
        )  # T-SQL additions

        def _sqlish(s):
            low = s.lower()
            return any(w in low for w in SQL_WORDS)

        def _built_sql(arg):
            if isinstance(arg, ast.JoinedStr):
                parts = "".join(
                    c.value
                    for c in arg.values
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)
                )
                return _sqlish(parts)
            if isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Mod, ast.Add)):
                return any(
                    isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)
                    and _sqlish(sub.value)
                    for sub in ast.walk(arg)
                )
            if (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "format"
                and isinstance(arg.func.value, ast.Constant)
                and isinstance(arg.func.value.value, str)
            ):
                return _sqlish(arg.func.value.value)
            return False

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and call_name(node) in ("execute", "executemany", "executescript")
                and node.args
                and _built_sql(node.args[0])
            ):
                sink.add(
                    "SEC6",
                    f,
                    node.lineno,
                    f"SQL built with f-string/format/concat passed to "
                    f"{call_name(node)}() — use parameterized queries (?, :name)",
                )

        # B5 — assert used for runtime validation. python -O strips asserts,
        # so the check silently vanishes in optimized runs. Type-narrowing
        # idioms (assert isinstance, assert x is not None) are accepted.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            t = node.test
            if isinstance(t, ast.Call) and call_name(t) == "isinstance":
                continue
            if (
                isinstance(t, ast.Compare)
                and len(t.ops) == 1
                and isinstance(t.ops[0], ast.IsNot)
                and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value is None
            ):
                continue
            sink.add(
                "B5",
                f,
                node.lineno,
                "assert used for validation — stripped under python -O; "
                "raise ValueError/TypeError instead",
            )

        # SEC7 — DEBUG = True committed in a settings module (framework
        # settings leak stack traces/secrets when this reaches production).
        # dev/local/test settings variants are exempt.
        stem = p.stem.lower()
        if ("settings" in stem or p.parent.name == "settings") and not any(
            k in stem for k in ("dev", "local", "test")
        ):
            for st in tree.body:
                if (
                    isinstance(st, ast.Assign)
                    and any(
                        isinstance(tg, ast.Name) and tg.id == "DEBUG"
                        for tg in st.targets
                    )
                    and isinstance(st.value, ast.Constant)
                    and st.value.value is True
                ):
                    sink.add(
                        "SEC7",
                        f,
                        st.lineno,
                        "DEBUG = True in a settings module — exposes stack "
                        "traces and secrets if it reaches production",
                    )

        # SEC8 — MongoDB $where/$eval/db.eval with dynamic input — NoSQL
        # injection. Same class of bug as SQL injection, different database.
        MONGO_EVAL_CALLS = {
            "find",
            "find_one",
            "findOne",
            "aggregate",
            "update_one",
            "update_many",
            "delete_one",
            "delete_many",
            "eval",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in MONGO_EVAL_CALLS and node.func.attr != "eval":
                continue
            # Check for $where or $eval in keywords/args
            for kw in node.keywords:
                if kw.arg in ("$where", "$eval", "where", "eval"):
                    sink.add(
                        "SEC8",
                        f,
                        node.lineno,
                        f"MongoDB {kw.arg}= dynamic input — NoSQL injection "
                        "risk, use $expr with parameterized aggregation",
                    )
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    for k in arg.keys:
                        if (
                            isinstance(k, ast.Constant)
                            and isinstance(k.value, str)
                            and k.value in ("$where", "$eval")
                        ):
                            sink.add(
                                "SEC8",
                                f,
                                node.lineno,
                                f"MongoDB {k.value} with dynamic input — "
                                "NoSQL injection risk",
                            )
                            break
            # Direct eval() on database/collection object
            rcv = receiver_name(node)
            if node.func.attr == "eval" and rcv in ("db", "database", "collection", ""):
                sink.add(
                    "SEC8",
                    f,
                    node.lineno,
                    "db.eval() with dynamic input — NoSQL injection risk, "
                    "use $expr with parameterized aggregation",
                )

        # AUTH1 — FastAPI route without auth guard. Flags @router.get/post/...
        # or @app.get/... decorators that don't include
        # dependencies=[Depends(...)] or a project-configured guard pattern.
        # Skips files in test directories and files marked with
        # "no-auth" in their stem or path.
        AUTH_ROUTE_METHODS = {
            "get",
            "post",
            "put",
            "delete",
            "patch",
            "options",
            "head",
            "websocket",
        }
        if not is_test(p) and "no_auth" not in stem and "no-auth" not in stem:
            for node in ast.walk(tree):
                if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
                    continue
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    # Match @router.method or @app.method
                    if not isinstance(dec.func, ast.Attribute):
                        continue
                    if dec.func.attr not in AUTH_ROUTE_METHODS:
                        continue
                    rcv = receiver_name(dec)
                    if rcv not in ("", "router", "app", "api", "bp", "blueprint"):
                        continue
                    # Check for dependencies keyword with any auth guard
                    has_guard = False
                    for kw in dec.keywords:
                        if kw.arg == "dependencies":
                            has_guard = True
                            break
                    if has_guard:
                        continue
                    # Also check if the function itself has a depends in its
                    # signature (FastAPI can inject via function params)
                    for fn_arg in node.args.args + node.args.kwonlyargs:
                        if fn_arg.arg in (
                            "current_user",
                            "user",
                            "token",
                            "auth",
                            "api_key",
                            "access_token",
                        ):
                            has_guard = True
                            break
                    if has_guard:
                        continue
                    sink.add(
                        "AUTH1",
                        f,
                        node.lineno,
                        f"@{rcv or 'router'}.{dec.func.attr} route without "
                        "auth guard — add dependencies=[Depends(get_current_user)] "
                        "or suppress with # audit: ok if public endpoint",
                    )

    # ── cross-file checks ────────────────────────────────────────────────

    # R10 - logging.basicConfig() called more than once: only the first call
    # takes effect, every later one is silently ignored.
    basicconfig_sites = []
    for p, tree in trees.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and call_name(node) == "basicConfig"
                and receiver_name(node) in ("logging", "")
            ):
                basicconfig_sites.append((rel(p), node.lineno))
    if len(basicconfig_sites) > 1:
        for fl, ln in basicconfig_sites:
            sink.add(
                "R10",
                fl,
                ln,
                f"logging.basicConfig() called {len(basicconfig_sites)}x across "
                "the project — only the first call takes effect",
            )

    # D1 - duplicate module-level functions
    by_name = collections.defaultdict(list)
    for p, tree in trees.items():
        for st in tree.body:
            if isinstance(
                st, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not st.name.startswith("__"):
                by_name[st.name].append((p, st))
    for name, sites in sorted(by_name.items()):
        if len(sites) < 2 or name in ("main", "run"):
            continue
        dumps = {ast.dump(s[1]) for s in sites}
        if len(dumps) > 1:  # only flag truly identical bodies
            continue
        tag = "IDENTICAL BODIES"
        where = ", ".join(f"{rel(p)}:{st.lineno}" for p, st in sites)
        sink.add("D1", where, 0, f"{name}() defined {len(sites)}x ({tag})")

    # D1b - cross-name duplicate bodies (different names, identical logic)
    _body_hashes: dict[str, list[tuple[str, int, str]]] = collections.defaultdict(list)

    for p, tree in trees.items():
        for st in tree.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if st.name.startswith("__"):
                    continue
                # Dump just the body, strip variable names for structure-only compare
                body_dump = "".join(ast.dump(s) for s in st.body)
                import re as _re

                body_dump = _re.sub(r"id='[^']+'", "id='_'", body_dump)
                body_dump = _re.sub(r"arg='[^']+'", "arg='_'", body_dump)
                _body_hashes[body_dump].append((st.name, st.lineno, rel(p)))
    for body_dump, sites in _body_hashes.items():
        names = {s[0] for s in sites}
        if len(names) < 2:
            continue
        where = ", ".join(f"{f}:{ln}" for _, ln, f in sites)
        sink.add(
            "D1",
            where,
            0,
            f"identical bodies, different names: {', '.join(sorted(names))}",
        )

    # D2 - circular imports (module level) — iterative Tarjan SCC
    def modname(p):
        parts = list(p.relative_to(ROOT).parts)
        parts[-1] = parts[-1][:-3]
        return ".".join(parts)

    mods = {modname(p): p for p in trees}
    edges = collections.defaultdict(set)
    for p, tree in trees.items():
        src = modname(p)
        for st in tree.body:
            if isinstance(st, ast.ImportFrom) and st.module and st.module in mods:
                edges[src].add(st.module)
            elif isinstance(st, ast.Import):
                for a in st.names:
                    if a.name in mods:
                        edges[src].add(a.name)

    idx: dict[str, int] = {}
    low: dict[str, int] = {}
    onstk: set[str] = set()
    stk: list[str] = []
    counter = 0

    for v in mods:
        if v in idx:
            continue
        # (node, next_child_index)
        stack: list[tuple[str, int]] = [(v, 0)]
        while stack:
            v, i = stack[-1]
            if i == 0:  # first visit
                idx[v] = low[v] = counter
                counter += 1
                stk.append(v)
                onstk.add(v)

            children = list(edges.get(v, ()))
            if i < len(children):
                w = children[i]
                stack[-1] = (v, i + 1)  # advance child pointer
                if w not in idx:
                    stack.append((w, 0))
                elif w in onstk:
                    low[v] = min(low[v], idx[w])
            else:
                # All children processed — pop and check SCC
                stack.pop()
                if stack:
                    pv, _ = stack[-1]
                    low[pv] = min(low[pv], low[v])
                if low[v] == idx[v]:
                    comp = []
                    while True:
                        w = stk.pop()
                        onstk.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        sink.add(
                            "D2",
                            " <-> ".join(sorted(comp)),
                            0,
                            "module-level import cycle",
                        )

    # D3 - flat sys.path-dependent imports
    submods = {p.stem: modname(p) for p in trees if len(p.relative_to(ROOT).parts) > 1}
    for p, tree in trees.items():
        for st in tree.body:
            if isinstance(st, ast.Import):
                for a in st.names:
                    if a.name in submods and a.name not in mods:
                        sink.add(
                            "D3",
                            rel(p),
                            st.lineno,
                            f"`import {a.name}` - actual module is {submods[a.name]}",
                        )

    # T1 - prod modules untouched by any test (Dimension 2 proxy)
    test_blob = "\n".join(tests.values())
    for p in sorted(trees):
        if p.name == "__init__.py":
            continue
        mn = modname(p)
        stem = p.stem
        pkg = mn.rsplit(".", 1)[0] if "." in mn else ""
        touched = (
            mn in test_blob
            or re.search(rf"import\s+{re.escape(stem)}\b", test_blob)
            or (
                pkg
                and re.search(
                    rf"from\s+{re.escape(pkg)}(\.{re.escape(stem)})?\s+import[^\n]*\b{re.escape(stem)}\b",
                    test_blob,
                )
            )
            or re.search(rf"""["']{re.escape(stem)}["']""", test_blob)
        )
        if not touched:
            sink.add("T1", rel(p), 1, f"module `{mn}` referenced by no test")

    # T2 / T3 - per-def coverage (system_phd #2/#4; judger.md happy-path cap).
    # T2: a public def whose NAME appears in no test file, in any form.
    # T3: the name IS test-referenced, but never from a test function that
    #     exercises an edge path (raises / None / empty / boundary input).
    test_trees = {}
    for tp, ttxt in tests.items():
        sink.register(rel(tp), ttxt)  # enables `# audit: ok` in tests too
        try:
            test_trees[tp] = ast.parse(ttxt)
        except SyntaxError:
            pass
    tested_any, tested_edge = set(), set()
    blob_names = set()
    for tt in test_trees.values():
        blob_names |= names_in(tt)
        for node in ast.walk(tt):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nm_set = names_in(node)
                tested_any |= nm_set
                if has_edge_signal(node):
                    tested_edge |= nm_set

    T_GENERIC = {
        "main",
        "run",
        "cli",
        "get",
        "close",
        "start",
        "stop",
        "name",
        "items",
        "keys",
        "values",
        "update",
        "add",
        "pop",
        "copy",
        "wrapper",
        "inner",
    }
    seen_defs = set()
    for p, tree in sorted(trees.items()):
        f = rel(p)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nm = node.name
            if nm.startswith("_") or nm in T_GENERIC or nm in seen_defs:
                continue
            seen_defs.add(nm)  # name-level: first def site carries the flag
            if nm not in blob_names:
                sink.add("T2", f, node.lineno, f"{nm}() referenced by no test")
            elif nm in tested_any and nm not in tested_edge:
                sink.add(
                    "T3",
                    f,
                    node.lineno,
                    f"{nm}() tested, but no referencing test uses raises/"
                    f"None/empty/boundary input (happy-path only)",
                )

    # T4 - assertion-free tests: pass green forever, prove nothing. Also
    # load-bearing for T2/T3, which count NAME references as coverage.
    for tp, tt in test_trees.items():
        tf = rel(tp)
        helper_asserts = {
            n.name
            for n in ast.walk(tt)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not n.name.startswith("test")
            and fn_asserts(n)
        }
        for node in ast.walk(tt):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) or not node.name.startswith("test"):
                continue
            dec_ids = set()
            for d in node.decorator_list:
                for s in ast.walk(d):
                    if isinstance(s, ast.Name):
                        dec_ids.add(s.id)
                    elif isinstance(s, ast.Attribute):
                        dec_ids.add(s.attr)
            if dec_ids & {"skip", "skipif", "xfail", "fixture"}:
                continue  # intentionally skipped, or not a test at all
            if not fn_asserts(node, helper_asserts):
                sink.add(
                    "T4",
                    tf,
                    node.lineno,
                    f"{node.name}() has no assert/raises - passes green, "
                    f"proves nothing",
                )

    # T5 - monkeypatch/mock.patch targets missing from the target module
    # (the `_path` -> `_db_path` drift class). Alias-resolved; modules with
    # __getattr__ skipped (dynamic attrs); non-module targets skipped.
    prod_bindings, prod_dynamic = {}, set()
    for p, tree in trees.items():
        mn = modname(p)
        binds = set()
        for st in tree.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                binds.add(st.name)
                if st.name == "__getattr__":
                    prod_dynamic.add(mn)
            elif isinstance(st, ast.Assign):
                binds.update(t.id for t in st.targets if isinstance(t, ast.Name))
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                binds.add(st.target.id)
            elif isinstance(st, ast.ImportFrom):
                binds.update(a.asname or a.name for a in st.names)
            elif isinstance(st, ast.Import):
                binds.update((a.asname or a.name).split(".")[0] for a in st.names)
        prod_bindings[mn] = binds

    for tp, tt in test_trees.items():
        tf = rel(tp)
        alias = {}
        for st in ast.walk(tt):
            if isinstance(st, ast.ImportFrom) and st.module:
                for a in st.names:
                    alias[a.asname or a.name] = f"{st.module}.{a.name}"
            elif isinstance(st, ast.Import):
                for a in st.names:
                    alias[a.asname or a.name.split(".")[0]] = (
                        a.name if a.asname else a.name.split(".")[0]
                    )
            elif (
                isinstance(st, ast.Assign)
                and isinstance(st.value, ast.Call)
                and call_name(st.value) in ("_imp", "import_module", "__import__")
                and st.value.args
                and isinstance(st.value.args[0], ast.Constant)
                and isinstance(st.value.args[0].value, str)
                and len(st.targets) == 1
                and isinstance(st.targets[0], ast.Name)
            ):
                alias[st.targets[0].id] = st.value.args[0].value

        def dotted(expr):
            parts = []
            while isinstance(expr, ast.Attribute):
                parts.append(expr.attr)
                expr = expr.value
            if isinstance(expr, ast.Name):
                parts.append(alias.get(expr.id, expr.id))
                return ".".join(reversed(parts))
            return None

        for node in ast.walk(tt):
            if not isinstance(node, ast.Call):
                continue
            cn, rcv = call_name(node), receiver_name(node)
            if any(
                kw.arg == "create"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            ):
                continue  # mock.patch(..., create=True) is intentional
            tgt_mod = attr = None
            raising_off = False
            if (
                cn in ("setattr", "delattr")
                and rcv == "monkeypatch"
                and len(node.args) >= 1
            ):
                raising_off = any(
                    kw.arg == "raising"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                    for kw in node.keywords
                )
                a0 = node.args[0]
                a1 = node.args[1] if len(node.args) > 1 else None
                if (
                    isinstance(a0, ast.Constant)
                    and isinstance(a0.value, str)
                    and "." in a0.value
                ):
                    tgt_mod, _, attr = a0.value.rpartition(".")
                elif isinstance(a1, ast.Constant) and isinstance(a1.value, str):
                    tgt_mod, attr = dotted(a0), a1.value
            elif (
                cn == "patch"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "." in node.args[0].value
            ):
                tgt_mod, _, attr = node.args[0].value.rpartition(".")
            elif (
                cn == "object"
                and rcv == "patch"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                tgt_mod, attr = dotted(node.args[0]), node.args[1].value
            if not tgt_mod or not attr or attr.startswith("__"):
                continue
            if f"{tgt_mod}.{attr}" in prod_bindings:
                continue  # patching a whole submodule - fine
            if (
                tgt_mod in prod_bindings
                and tgt_mod not in prod_dynamic
                and attr not in prod_bindings[tgt_mod]
            ):
                extra = " (raising=False - fails SILENTLY)" if raising_off else ""
                sink.add(
                    "T5",
                    tf,
                    node.lineno,
                    f"patches {tgt_mod}.{attr} but `{attr}` is not defined "
                    f"in that module{extra}",
                )

    # T7 - mock.patch targets that don't exist in the module
    # (same pattern as T5 but for mock.patch / unittest.mock.patch decorators and
    # context managers). The patch target is the first string arg: 'module.func'.
    # Skip create=True (intentional dynamic patches).
    for tp, tt in test_trees.items():
        tf = rel(tp)
        # Build alias map for this test file (same as T5)
        alias = {}
        for st in ast.walk(tt):
            if isinstance(st, ast.ImportFrom) and st.module:
                for a in st.names:
                    alias[a.asname or a.name] = f"{st.module}.{a.name}"
            elif isinstance(st, ast.Import):
                for a in st.names:
                    alias[a.asname or a.name.split(".")[0]] = (
                        a.name if a.asname else a.name.split(".")[0]
                    )
        for node in ast.walk(tt):
            if not isinstance(node, ast.Call):
                continue
            cn, rcv = call_name(node), receiver_name(node)
            # mock.patch('a.b.c') or patch('a.b.c') or unittest.mock.patch('a.b.c')
            if cn != "patch":
                continue
            if rcv not in ("mock", "unittest.mock", "patch", ""):
                continue
            if any(
                kw.arg == "create"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            ):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            target_str = node.args[0].value
            if not isinstance(target_str, str) or "." not in target_str:
                continue
            # Split 'module.function' into module and function
            parts = target_str.rsplit(".", 1)
            if len(parts) != 2:
                continue
            mod, attr = parts
            # Resolve module alias
            mod = alias.get(mod, mod)
            binds = prod_bindings.get(mod)
            if binds is None:
                sink.add(
                    "T7",
                    tf,
                    node.lineno,
                    f"mock.patch('{target_str}') — module '{mod}' not found "
                    "in production code",
                )
            elif attr not in binds:
                sink.add(
                    "T7",
                    tf,
                    node.lineno,
                    f"mock.patch('{target_str}') — '{attr}' is not defined "
                    f"in module '{mod}'",
                )

    # DOC - docstring coverage
    doc_missing = collections.Counter()
    doc_total = collections.Counter()
    for p, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not node.name.startswith("_"):
                doc_total[rel(p)] += 1
                if not ast.get_docstring(node):
                    doc_missing[rel(p)] += 1

    # ── report ───────────────────────────────────────────────────────────
    SECTIONS = [
        ("C1", "HIGH", "bare `except:` (Dim 1)"),
        ("C2", "HIGH", "exceptions swallowed with `pass` (Dim 1)"),
        ("SEC1", "HIGH", "subprocess shell=True (Phase 1 security - RCE class)"),
        ("SEC2", "HIGH", "eval/exec/exec_module/pickle.load (Phase 2 security)"),
        ("SEC3", "HIGH", "hardcoded credentials in source (R7 covers logs)"),
        ("SEC4", "HIGH", "yaml.load() without SafeLoader (RCE risk)"),
        (
            "SEC6",
            "HIGH",
            "SQL built with f-string/format/concat in execute() (injection)",
        ),
        (
            "SEC8",
            "HIGH",
            "MongoDB $where/$eval/db.eval — NoSQL injection risk",
        ),
        (
            "SEC7",
            "MEDIUM",
            "DEBUG = True in a settings module (data leak in production)",
        ),
        ("AUTH1", "HIGH", "FastAPI route without auth guard (no Depends in decorator)"),
        (
            "LANG1",
            "MEDIUM",
            "LLM constructor without temperature= (nondeterministic output)",
        ),
        (
            "BOTTLE1",
            "MEDIUM",
            "await in loop — sequential async kills throughput, use asyncio.gather()",
        ),
        ("BOTTLE2", "HIGH", "sync blocking I/O inside async def — blocks event loop"),
        ("B1", "HIGH", "mutable default arguments (Dim 1 - shared-state bugs)"),
        ("B4", "MEDIUM", "tempfile.mktemp/os.tempnam — race-prone (TOCTOU)"),
        ("B5", "MEDIUM", "assert used for validation (stripped under python -O)"),
        ("F1", "HIGH", "locks defined but never acquired (Dim 3)"),
        ("F5", "MEDIUM", "lock ordering inconsistency (potential deadlock)"),
        ("R9", "HIGH", "broken structured logging — invalid kwargs to log.info()"),
        ("R10", "MEDIUM", "logging.basicConfig() called >1x (only the first wins)"),
        ("SEC5", "HIGH", "SQLite engine without PRAGMA foreign_keys=ON"),
        ("P1", "HIGH", "imports inside loop bodies (Phase 4)"),
        ("E1", "HIGH", "prompts frozen at import (engine regression)"),
        ("E2", "HIGH", "hook prompts missing {task} (engine regression)"),
        ("D2", "HIGH", "circular module imports (Phase 2)"),
        ("C3", "MEDIUM", "silent fallback returns in except handlers (Dim 1)"),
        ("C4", "MEDIUM", "open() resource discipline (Dim 1)"),
        ("C5", "MEDIUM", "unguarded TOCTOU exists()-then-remove() (Phase 3)"),
        (
            "C6",
            "MEDIUM",
            "LLM-parsed dicts indexed bare - KeyError on missing field (Dim 1)",
        ),
        ("C7", "HIGH", "shutil.rmtree() without try/except OSError (Dim 1)"),
        ("C8", "MEDIUM", "except: continue — silently discards errors (Dim 1)"),
        ("C9", "MEDIUM", "float == comparison — floating-point rounding (Dim 1)"),
        ("F2", "MEDIUM", "`global` shared mutable state (Dim 3)"),
        ("F3", "MEDIUM", "import-time side effects (Phase 2)"),
        ("F4", "MEDIUM", "bare cfg[...]/environ[...] indexing (Phase 2)"),
        ("B2", "MEDIUM", "HTTP calls without timeout= (hangs forever)"),
        ("G1", "MEDIUM", "hardcoded tuning knobs outside config (audit S2)"),
        ("G2", "MEDIUM", "module-level mutable state mutated from code (audit S6-04)"),
        ("G3", "HIGH", "__init__ returning non-None (TypeError at runtime)"),
        ("D1", "MEDIUM", "duplicate function implementations (Phase 3 drift)"),
        ("D3", "MEDIUM", "sys.path-dependent flat imports (Phase 1)"),
        ("D4", "MEDIUM", "hardcoded model strings outside config layer (Phase 1)"),
        ("D5", "MEDIUM", "scattered env reads (Phase 1)"),
        ("P4", "MEDIUM", "settings lookups inside loops (Phase 4)"),
        ("T1", "MEDIUM", "modules referenced by no test (Dim 2 proxy)"),
        ("T2", "MEDIUM", "public defs referenced by no test (Dim 2 per-def)"),
        ("T3", "MEDIUM", "defs tested happy-path only - no edge/failure input (Dim 2)"),
        ("T4", "MEDIUM", "assertion-free tests - green forever, prove nothing (Dim 2)"),
        (
            "T5",
            "MEDIUM",
            "monkeypatch/patch targets missing from target module (Dim 2)",
        ),
        (
            "T7",
            "MEDIUM",
            "mock.patch targets missing from the patched module (Dim 2)",
        ),
        ("DG1", "MEDIUM", "god functions / classes / files (Dim 4)"),
        ("C2i", "INFO", "best-effort logging swallowed (acceptable; review once)"),
        ("E1i", "INFO", "@cache prompt getters (hot-reload frozen after first call)"),
        ("F2i", "INFO", "lazy-init cache globals (idiomatic; thread-check once)"),
        ("B3", "INFO", "daemon threads (need cooperative cancellation)"),
        ("P2", "INFO", "imports inside functions (anti-circular idiom; counts)"),
        ("P3", "INFO", "re.compile() inside functions (Phase 4)"),
    ]

    if as_json:
        out = {
            cid: [{"file": f, "line": ln, "msg": m} for f, ln, m in sink.data[cid]]
            for cid, _, _ in SECTIONS
            if sink.data.get(cid)
        }
        out["_doc_coverage"] = {
            f: f"{doc_missing[f]}/{doc_total[f]}" for f in doc_missing
        }
        print(json.dumps(out, indent=2))
        high = sum(len(sink.data[c]) for c, s, _ in SECTIONS if s == "HIGH")
        sys.exit(1 if strict and high else 0)

    print(
        f"scanned: {len(trees)} production files, {len(tests)} test files "
        f"({sink.suppressed} findings suppressed via `# audit: ok`)\n"
    )
    high = 0
    CAP = 25
    for cid, sev, title in SECTIONS:
        items = sink.data.get(cid, [])
        if sev == "HIGH":
            high += len(items)
        if not items and sev != "HIGH":
            continue  # quiet empty non-HIGH sections
        print("=" * 74)
        print(f"{cid} [{sev}] {title} - {len(items)} finding(s)")
        print("=" * 74)
        if cid == "P2":
            cnt = collections.Counter(fl for fl, _, _ in items)
            for fl, c in cnt.most_common(15):
                print(f"  {fl:46} {c} inline imports")
        else:
            for fl, line, msg in sorted(items)[:CAP]:
                loc = f"{fl}:{line}" if line else fl
                print(f"  {loc:52} {msg}")
            if len(items) > CAP:
                print(f"  ... +{len(items) - CAP} more")
        print()

    print("=" * 74)
    print(
        f"DOC [INFO] public defs without docstrings (Dim 5) - "
        f"{sum(doc_missing.values())}/{sum(doc_total.values())} undocumented"
    )
    print("=" * 74)
    for fl, miss in doc_missing.most_common(10):
        print(f"  {fl:46} {miss}/{doc_total[fl]}")

    med = sum(len(sink.data[c]) for c, s, _ in SECTIONS if s == "MEDIUM")
    info = sum(len(sink.data[c]) for c, s, _ in SECTIONS if s == "INFO")
    if min_sev == "HIGH":
        med = info = 0
    print(f"\n{'=' * 74}")
    print(
        f"SUMMARY  HIGH: {high}   MEDIUM: {med}   INFO: {info}   "
        f"suppressed: {sink.suppressed}"
    )
    if strict and high:
        sys.exit(1)


if __name__ == "__main__":
    main()
