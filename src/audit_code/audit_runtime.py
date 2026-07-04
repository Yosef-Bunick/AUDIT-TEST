#!/usr/bin/env python3
"""
audit_runtime.py - what fails at 2am during a run, not in code review.

Third audit of the set. audit_wiring.py answers "is it connected?",
audit_phd.py answers "does it meet the quality bar?". This one answers
"will the loop hang, crash on another machine, or run with the wrong
brain?" - the operational failure modes of an agent engine.

ONE FILE BY DESIGN. The 20-audit wishlist collapses into 3 scripts; this
docstring is the map of where every idea lives:

  audit_dead_code      -> wiring CHECK 1/2 (dead + test-only symbols)
  audit_config         -> wiring CHECK 3/4/7/8 (dead keys, cfg flow,
                          shadowed config, transitively dead config)
  audit_imports        -> phd D2 (cycles), D3 (flat imports), P1/P2 (inline)
  audit_exceptions     -> phd C1/C2/C3 (bare/swallowed/silent-fallback)
  audit_security       -> phd SEC1/SEC2 (shell=True, eval/exec/pickle)
  audit_state          -> phd F2/G2 (globals, module mutables)
  audit_tests          -> phd T1 (untested modules)
  audit_complexity     -> phd DG1 (god functions/files/classes)
  audit_performance    -> phd P1-P4 (+ R-checks here for blocking calls)
  stdout protocol      -> wiring CHECK 9
  audit_agent_loop     -> R1 here (unbounded loops) + the brakes already
                          audited by wiring CHECK 3/8 (limits that can't fire)
  audit_retries_timeouts -> R2/R3 here (subprocess/blocking w/o timeout)
  audit_paths          -> R4/R5 here (hardcoded absolute, CWD-relative)
  audit_file_io        -> R6 here (text I/O without encoding=)
  audit_logging        -> R7/R8 here (secrets in logs, stackless logging)
  audit_tool_calls     -> R9 here (TOOL_DEFINITIONS <-> run_tool parity)
  audit_prompts        -> R10 here (prompt files + HOOK_ROLE <-> raw prompts)
  audit_dependencies   -> R11 here (third-party imports vs requirements.txt)
  audit_llm_contracts  -> R12 here (prompt JSON schema <-> parsed-response reads)
  audit_gates          -> R13 here (advisory check_*/validate_* results ignored)
  audit_contracts/schema/invariants -> semantic; stays with the judger,
                          tests/tests_test_invariants_deep.py, and human review

CHECKS
======
  R1  unbounded loop (HIGH)
      `while True:` with no break at the loop's own level, no return/raise,
      no sys.exit anywhere in the body (nested defs excluded - their exits
      don't exit this loop). For an autonomous engine this is the runaway-
      run class: the loop the brakes can't reach.

  R2  subprocess without timeout (HIGH)
      subprocess.run/call/check_call/check_output without timeout=. The
      sandbox runs agent-written code; a hung child hangs the round.

  R3  blocking wait without timeout (MEDIUM)
      .communicate()/.wait()/.join() called with no arguments at all.
      Event.wait() and thread.join() without timeout block forever; the
      engine's own critical rule is "wait_all uses a total deadline".
      Calls passing any argument are assumed bounded (precision first).

  R4  hardcoded absolute path (MEDIUM)
      String literals like C:\\AI\\... or /home/... in production code.
      Breaks the moment the project moves machines; should come from
      settings.paths / env.

  R5  CWD-relative file I/O (MEDIUM)
      open("x.json") / Path("data/y.txt") with a relative literal that has
      a file extension. The Tauri app spawns Python with ITS working dir;
      relative paths resolve somewhere else. Anchor to Path(__file__).

  R6  text I/O without encoding= (MEDIUM - the Windows killer)
      open()/read_text()/write_text() in text mode without encoding=.
      Windows defaults to cp1252; the first agent-generated UTF-8 file
      crashes the read with UnicodeDecodeError.

  R7  secret-shaped identifier in a log call (HIGH)
      log/print/emit/info/... whose arguments reference identifiers
      matching api_key/secret/password/credential/token. Logs are written
      to disk and shipped to the UI.

  R8  exception logged without stack (INFO, per-file counts)
      Handlers that log only the exception message - fine for one-liners,
      but a file full of them means no finding root causes later. Counted,
      not flooded.

  R9  tool registry parity (HIGH - engine)
      TOOL_DEFINITIONS names (incl. imported dicts like VERIFY_TOOL,
      resolved across files) vs run_tool's `name == "..."` dispatch:
        dispatched-but-not-defined -> _ALLOWED_TOOLS gate blocks it before
                                      its branch can run (dead feature)
        defined-but-not-dispatched -> advertised to the model, lands in
                                      "[unknown tool]" at execution
      One malformed boundary here and the agent "has" a tool it can't use.

  R10 prompt & hook parity (HIGH - engine)
      (a) every prompt name referenced in code maps to prompts/<name>.md
          (missing file = PromptMissingError mid-run);
      (b) orphan .md files nothing references (stale brain). *_phd files
          are only blessed when some call site actually loads that name
          with phd=True - v2 blessed EVERY <name>_phd blanket-style,
          which hid worker_phd.md being loaded by nothing;
      (c) every HOOK_ROLE key has register_raw_prompt("hook_<key>", ...) -
          otherwise prompt_for_role falls back to the GENERIC system
          prompt and the hook silently runs with the wrong brain;
      (d) every phd=True call site has prompts/<name>_phd.md on disk
          (the loader RAISES PromptMissingError for a missing variant).

  R10p prompt placeholder parity (MEDIUM - v3)
      {word} placeholders in prompts/*.md that NO loader call site ever
      supplies as a kwarg (engine_dir is auto-filled). The loader logs an
      unfilled-placeholder warning on every load and the literal {x}
      reaches the model - the spy.md {score}/{verdict} example-brace
      class. Fix: pass the kwarg, or rewrite the example without braces.

  R11 dependency inventory (INFO)
      Third-party imports vs requirements.txt (or its absence). A fresh
      clone should know what to pip install.

  R12 prompt JSON-contract parity (MEDIUM / INFO - v3)
      For prompts that promise a JSON schema ("key": ... lines in the .md)
      vs the keys code reads off the parsed response, in modules that LOAD
      those prompts:
        R12  read-but-never-promised (MEDIUM): result.get("k") on a
             parse_json/json.loads result where no prompt the module loads
             mentions "k" - the model was never ASKED for the field
             (the judger subsystem_scores class);
        R12i promised-but-never-read (INFO): a schema field whose quoted
             form appears nowhere in any module loading that prompt.
      Taint is one hop (v = parse_json(...)); keys on nested loop vars are
      checked text-wide, not flow-wise.

  R13 advisory gates (MEDIUM - v3)
      In-repo check_*/verify_*/enforce_*/validate_* functions that RETURN
      a verdict and never raise, whose call sites discard the result or
      never branch on it. A brake that cannot say no - the check_llm_call
      class: budget verdict computed, logged, ignored. Gates that raise
      internally are exempt (calling them bare IS the enforcement).

Workflow (same as audit_phd):
  # audit: ok   on the flagged line suppresses it
  --json        machine-readable
  --strict      exit 1 if any HIGH survives suppression
"""

import ast
import collections
import json
import re
import sys
from pathlib import Path

from audit_code.audit_shared import SKIP_PARTS

ROOT = Path(__file__).resolve().parent.parent.parent
# Allow --path override for audit-code wrapper
for _i, _a in enumerate(sys.argv):
    if _a == "--path" and _i + 1 < len(sys.argv):
        ROOT = Path(sys.argv[_i + 1]).resolve()
        break

SUBPROCESS_FNS = {"run", "call", "check_call", "check_output"}
BLOCKING_WAITS = {"communicate", "wait", "join"}
LOG_CALLS = {
    "log",
    "info",
    "debug",
    "warning",
    "warn",
    "error",
    "exception",
    "critical",
    "print",
    "emit",
}
SECRET_RE = re.compile(
    r"(api_?keys?|secret|passwd|password|credential|auth_token|access_token|bearer)",
    re.I,
)
ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?:home|Users|tmp|var|opt|etc|usr)/)")
REL_FILE_RE = re.compile(
    r"^[\w][\w\-./\\]*\.(json|txt|md|py|db|log|jsonl|yaml|yml|csv)$"
)
TRACEBACK_MARKS = {
    "format_exc",
    "print_exc",
    "exc_info",
    "traceback",
    "format_exception",
}
SUPPRESS_RE = re.compile(r"#\s*audit:\s*ok")


def is_test(p: Path) -> bool:
    s = str(p).replace("\\", "/")
    return (
        "/tests/" in s
        or p.name.startswith(("test_", "tests_"))
        or p.name == "conftest.py"
    )


def is_audit_file(p: Path) -> bool:
    return p.parent == ROOT and (
        p.name.startswith("audit_") or p.name == "run_all_audits.py"
    )


def collect():
    prod, test = {}, {}
    for p in ROOT.rglob("*.py"):
        if is_audit_file(p) or any(part in SKIP_PARTS for part in p.parts):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        (test if is_test(p) else prod)[p] = txt
    return prod, test


def rel(p):
    return str(p.relative_to(ROOT))


def call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def receiver_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        v = f.value
        if isinstance(v, ast.Name):
            return v.id
        if isinstance(v, ast.Attribute):
            return v.attr
    return ""


class Sink:
    def __init__(self):
        self.data = collections.defaultdict(list)
        self.lines = {}
        self.suppressed = 0

    def register(self, relpath, text):
        self.lines[relpath] = text.splitlines()

    def add(self, cid, f, line, msg):
        src = self.lines.get(f, [])
        if 0 < line <= len(src) and SUPPRESS_RE.search(src[line - 1]):
            self.suppressed += 1
            return
        self.data[cid].append((f, line, msg))


# ──────────────────────────────────────────────────────────────────────────
# R1 - unbounded loops
# ──────────────────────────────────────────────────────────────────────────


def loop_has_exit(loop):
    """break at this loop's level / return / raise / sys.exit in the body."""

    def scan(stmts, depth):
        for st in stmts:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # nested def's exits don't exit us
            if depth == 0 and isinstance(st, ast.Break):
                return True
            if isinstance(st, (ast.Return, ast.Raise)):
                return True
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
                cn = call_name(st.value)
                if cn in ("exit", "_exit", "quit") or (
                    cn == "exit" and receiver_name(st.value) in ("sys", "os")
                ):
                    return True
            deeper = depth + (
                1 if isinstance(st, (ast.For, ast.AsyncFor, ast.While)) else 0
            )
            for field in ("body", "orelse", "finalbody"):
                if scan(getattr(st, field, []) or [], deeper):
                    return True
            for h in getattr(st, "handlers", []):
                if scan(h.body, deeper):
                    return True
            for case in getattr(st, "cases", []):
                if scan(case.body, deeper):
                    return True
        return False

    return scan(loop.body, 0)


# ──────────────────────────────────────────────────────────────────────────
# R9 - tool registry parity
# ──────────────────────────────────────────────────────────────────────────


def dict_tool_name(d):
    if isinstance(d, ast.Dict):
        for k, v in zip(d.keys, d.values):
            if (
                isinstance(k, ast.Constant)
                and k.value == "name"
                and isinstance(v, ast.Constant)
                and isinstance(v.value, str)
            ):
                return v.value
    return None


def audit_tool_parity(trees):
    reg = next(
        (p for p in trees if rel(p).replace("\\", "/") == "tools/registry.py"), None
    )
    if reg is None:
        return [], [], False
    tree = trees[reg]

    # tool dicts assigned to names anywhere in prod (VERIFY_TOOL etc.)
    named_dicts = {}
    for p, t in trees.items():
        for node in ast.walk(t):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                nm = dict_tool_name(node.value)
                if nm:
                    named_dicts[node.targets[0].id] = nm

    defined = set()
    for node in ast.walk(tree):
        target_list = None
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "TOOL_DEFINITIONS"
        ):
            target_list = node.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "TOOL_DEFINITIONS"
            and node.func.attr in ("append", "extend")
        ):
            target_list = ast.List(elts=list(node.args))
        if target_list is None:
            continue
        elts = target_list.elts if isinstance(target_list, ast.List) else []
        for e in elts:
            nm = dict_tool_name(e)
            if nm:
                defined.add(nm)
            elif isinstance(e, ast.Name) and e.id in named_dicts:
                defined.add(named_dicts[e.id])
            elif isinstance(e, ast.List):
                for sub in e.elts:
                    nm = dict_tool_name(sub)
                    if nm:
                        defined.add(nm)

    dispatched = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "run_tool"
        ):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Compare)
                    and isinstance(sub.left, ast.Name)
                    and sub.left.id == "name"
                ):
                    for op, comp in zip(sub.ops, sub.comparators):
                        if (
                            isinstance(op, ast.Eq)
                            and isinstance(comp, ast.Constant)
                            and isinstance(comp.value, str)
                        ):
                            dispatched.add(comp.value)
                        elif isinstance(op, ast.In) and isinstance(
                            comp, (ast.Tuple, ast.List, ast.Set)
                        ):
                            for e in comp.elts:
                                if isinstance(e, ast.Constant) and isinstance(
                                    e.value, str
                                ):
                                    dispatched.add(e.value)
    ghost_branches = sorted(dispatched - defined)  # blocked by _ALLOWED_TOOLS gate
    unknown_at_runtime = sorted(defined - dispatched)
    return ghost_branches, unknown_at_runtime, True


# ──────────────────────────────────────────────────────────────────────────
# R10 - prompt & hook parity
# ──────────────────────────────────────────────────────────────────────────


def audit_prompts(trees):
    prompt_dir = ROOT / "prompts"
    md_files = sorted(prompt_dir.glob("*.md")) if prompt_dir.exists() else []
    stems = {p.stem for p in md_files}

    referenced = set()
    raw_registered = set()
    hook_keys = set()
    role_to_prompt = {}  # role -> prompt name (from dicts in prompts pkg)
    phd_used = set()  # prompt NAMES loaded with phd=True somewhere
    pfr_phd_roles = (
        set()
    )  # prompt_for_role(<role>, phd=True) roles, resolved after walk
    supplied_kwargs = set()  # every kwarg name any loader call site passes
    for p, tree in trees.items():
        in_prompts_pkg = rel(p).replace("\\", "/").startswith("prompts/")
        for node in ast.walk(tree):
            # role->prompt maps inside the prompts package: str values + (str, bool) tuples
            if in_prompts_pkg and isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (
                        isinstance(v, ast.Constant)
                        and isinstance(v.value, str)
                        and re.fullmatch(r"[a-z][a-z0-9_]*", v.value)
                    ):
                        referenced.add(v.value)
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            role_to_prompt[k.value] = v.value
                    elif (
                        isinstance(v, ast.Tuple)
                        and v.elts
                        and isinstance(v.elts[0], ast.Constant)
                        and isinstance(v.elts[0].value, str)
                    ):
                        referenced.add(v.elts[0].value)
                        # prompt_loader's __getattr__ _map: ("system", True)
                        if (
                            len(v.elts) >= 2
                            and isinstance(v.elts[1], ast.Constant)
                            and v.elts[1].value is True
                        ):
                            phd_used.add(v.elts[0].value)
            if isinstance(node, ast.Call):
                cn = call_name(node)
                if cn in ("prompt", "_load_prompt", "prompt_for_role"):
                    supplied_kwargs |= {kw.arg for kw in node.keywords if kw.arg}
                    first = node.args[0] if node.args else None
                    fname = (
                        first.value
                        if isinstance(first, ast.Constant)
                        and isinstance(first.value, str)
                        else None
                    )
                    phd_true = any(
                        kw.arg == "phd"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                        for kw in node.keywords
                    )
                    if fname:
                        if cn == "prompt_for_role":
                            # roles resolve through _ROLE_TO_PROMPT (collected above);
                            # hook_/unknown roles hit raw prompts, not .md files
                            if phd_true:
                                pfr_phd_roles.add(fname)
                        else:
                            referenced.add(fname)
                            if phd_true:
                                phd_used.add(fname)
                elif (
                    cn == "register_raw_prompt"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    raw_registered.add(node.args[0].value)
            # HOOK_ROLE = {...} and HOOK_ROLE["x"] = ...
            if isinstance(node, ast.Assign):
                tgt = node.targets[0]
                if (
                    isinstance(tgt, ast.Name)
                    and tgt.id == "HOOK_ROLE"
                    and isinstance(node.value, ast.Dict)
                ):
                    for k in node.value.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            hook_keys.add(k.value)
                elif (
                    isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "HOOK_ROLE"
                    and isinstance(tgt.slice, ast.Constant)
                    and isinstance(tgt.slice.value, str)
                ):
                    hook_keys.add(tgt.slice.value)

    for r in pfr_phd_roles:
        n = role_to_prompt.get(r)
        if n:
            phd_used.add(n)

    missing_files = sorted(n for n in referenced if n not in stems)
    # (d) phd=True call site whose _phd variant is missing -> loader RAISES
    missing_files += sorted(f"{n}_phd" for n in phd_used if f"{n}_phd" not in stems)
    # (b) bless a _phd file only when that name is actually loaded with phd=True
    covered = referenced | {f"{n}_phd" for n in phd_used}
    orphans = sorted(s for s in stems if s not in covered)
    hooks_wrong_brain = sorted(
        k for k in hook_keys if f"hook_{k}" not in raw_registered
    )
    orphan_raw = sorted(
        r for r in raw_registered if r.startswith("hook_") and r[5:] not in hook_keys
    )

    # R10p - {placeholders} in .md files nothing ever fills. engine_dir is
    # auto-defaulted by the loader; phd/mode are control args, not templates.
    fillable = {"engine_dir"} | (supplied_kwargs - {"phd", "mode"})
    unfillable = []
    for md in md_files:
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        bad = sorted(set(re.findall(r"\{(\w+)\}", text)) - fillable)
        if bad:
            unfillable.append((md.name, bad))
    return missing_files, orphans, hooks_wrong_brain, orphan_raw, unfillable


# ──────────────────────────────────────────────────────────────────────────
# R12 - prompt JSON-contract parity
# ──────────────────────────────────────────────────────────────────────────


def _reads_file(call):
    """json.loads(<file read>) is config parsing, not an LLM response."""
    for n in ast.walk(call):
        if isinstance(n, ast.Attribute) and n.attr in ("read_text", "read"):
            return True
        if isinstance(n, ast.Call) and call_name(n) == "open":
            return True
    return False


def audit_llm_contracts(trees, prod):
    """(read_not_promised, promised_not_read) - see R12 in the docstring."""
    prompt_dir = ROOT / "prompts"
    keys_of = {}  # prompt stem -> promised JSON keys
    for md in (prompt_dir.glob("*.md") if prompt_dir.exists() else ()):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ks = set(re.findall(r'"(\w+)"\s*:', text))
        if ks:
            keys_of[md.stem] = ks

    role_map = {}  # role -> prompt name (prompts pkg dicts)
    for p, tree in trees.items():
        if not rel(p).replace("\\", "/").startswith("prompts/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (
                        isinstance(k, ast.Constant)
                        and isinstance(k.value, str)
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)
                    ):
                        role_map[k.value] = v.value

    def _resolved(node):
        """Prompt name a direct loader call resolves to, else None."""
        if (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            cn = call_name(node)
            if cn in ("prompt", "_load_prompt"):
                return node.args[0].value
            if cn == "prompt_for_role":
                return role_map.get(node.args[0].value)
        return None

    # getter map: def _score_sys(): return prompt_for_role("score") - the
    # engine's idiom. Lets contracts bind at FUNCTION level: a function is
    # judged only against prompts IT references, so parsing engine-internal
    # JSON (worker result envelopes) is never held to a prompt's schema.
    getter_map = {}
    for p, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    nm = _resolved(sub)
                    if nm and nm in keys_of:
                        getter_map.setdefault(node.name, set()).add(nm)

    loads_by_file = {}  # path -> contract prompts it loads
    read_not_promised = set()
    for p, tree in trees.items():
        file_contract = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            contract = set()
            for sub in ast.walk(node):
                nm = _resolved(sub)
                if nm and nm in keys_of:
                    contract.add(nm)
                elif isinstance(sub, ast.Call) and call_name(sub) in getter_map:
                    contract |= getter_map[call_name(sub)]
            file_contract |= contract
            if not contract:
                continue  # no contract prompt in scope - skip
            # phd=True prepends <name>_phd.md to the base - the model sees
            # BOTH files, so the promised set is their union.
            promised = set()
            for n in contract:
                promised |= keys_of[n]
                promised |= keys_of.get(f"{n}_phd", set())
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
                        and not _reads_file(sub.value)
                    ):
                        tainted.add(sub.targets[0].id)
            if not tainted:
                continue
            for sub in ast.walk(node):
                key = None
                if (
                    isinstance(sub, ast.Call)
                    and call_name(sub) == "get"
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id in tainted
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)
                ):
                    key = sub.args[0].value
                elif (
                    isinstance(sub, ast.Subscript)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id in tainted
                    and isinstance(sub.ctx, ast.Load)
                    and isinstance(sub.slice, ast.Constant)
                    and isinstance(sub.slice.value, str)
                ):
                    key = sub.slice.value
                if key and not key.startswith("_") and key not in promised:
                    read_not_promised.add(
                        (rel(p), sub.lineno, key, tuple(sorted(contract)))
                    )
        if file_contract:
            loads_by_file[p] = file_contract
    read_not_promised = sorted(read_not_promised)

    promised_not_read = []
    for name, ks in sorted(keys_of.items()):
        readers = [p for p, c in loads_by_file.items() if name in c]
        if not readers:
            continue
        blob = "\n".join(prod[p] for p in readers if p in prod)
        for k in sorted(ks):
            if not re.search(r"""["']{}["']""".format(re.escape(k)), blob):
                promised_not_read.append((name, k))
    return read_not_promised, promised_not_read


# ──────────────────────────────────────────────────────────────────────────
# R13 - advisory gates (a brake that cannot say no)
# ──────────────────────────────────────────────────────────────────────────

GATE_RE = re.compile(r"^(check|verify|enforce|validate)_")


def audit_gates(trees):
    # gates worth auditing: defined in-repo, RETURN a value, never raise.
    # A gate that raises enforces itself - calling it bare is correct.
    returns_only, raises_somewhere = set(), set()
    for p, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and GATE_RE.match(node.name):
                body = [s for s in ast.walk(node)]
                if any(isinstance(s, ast.Raise) for s in body):
                    raises_somewhere.add(node.name)
                elif any(
                    isinstance(s, ast.Return) and s.value is not None for s in body
                ):
                    returns_only.add(node.name)
    gate_names = returns_only - raises_somewhere

    def _inert(stmts):
        """All statements are pure logging/pass - the branch decides nothing.
        `if not ok: log(...)` is the F1 pattern: a gate consulted, then
        overruled by falling through to the very call it vetoed."""
        for st in stmts:
            if isinstance(st, ast.Pass):
                continue
            if (
                isinstance(st, ast.Expr)
                and isinstance(st.value, ast.Call)
                and call_name(st.value) in LOG_CALLS
            ):
                continue
            return False
        return True

    def classify_uses(fn):
        """(branchy, consumed): names loaded in an EFFECTIVE decision position
        vs names loaded anywhere else except inside a log call. An If whose
        body only logs counts as no use at all; a gate result whose only
        afterlife is logging gets flagged, one that feeds a real branch,
        a return, or data flow is doing its job."""
        branchy, consumed = set(), set()

        def visit(node, in_log, in_dec, in_void):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and not in_void
            ):
                if in_dec:
                    branchy.add(node.id)
                elif not in_log:
                    consumed.add(node.id)
            for ch in ast.iter_child_nodes(node):
                il, idc, iv = in_log, in_dec, in_void
                if isinstance(node, ast.If) and ch is node.test:
                    if _inert(node.body) and _inert(node.orelse):
                        iv = True
                    else:
                        idc = True
                elif isinstance(node, (ast.While, ast.IfExp)) and ch is node.test:
                    idc = True
                elif isinstance(node, ast.Assert) and ch is node.test:
                    idc = True
                elif isinstance(node, ast.Return) and ch is node.value:
                    idc = True
                elif isinstance(node, ast.Raise) and ch is node.exc:
                    idc = True
                if isinstance(node, ast.Call) and call_name(node) in LOG_CALLS:
                    il = True
                visit(ch, il, idc, iv)

        visit(fn, False, False, False)
        return branchy, consumed

    def assign_targets(node):
        tgts = set()
        for t in node.targets:
            if isinstance(t, ast.Name):
                tgts.add(t.id)
            elif isinstance(t, ast.Tuple):
                tgts.update(e.id for e in t.elts if isinstance(e, ast.Name))
        return tgts

    findings = []
    for p, tree in trees.items():
        flagged_lines = set()

        # Pattern 1 (exact idiom, immune to name reuse): gate assign followed
        # immediately by an If on the verdict whose branch only logs.
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                stmts = getattr(node, field, None)
                if not isinstance(stmts, list):  # Lambda.body is a single expr
                    continue
                for a, b in zip(stmts, stmts[1:]):
                    if (
                        isinstance(a, ast.Assign)
                        and isinstance(a.value, ast.Call)
                        and call_name(a.value) in gate_names
                        and isinstance(b, ast.If)
                        and _inert(b.body)
                        and _inert(b.orelse)
                    ):
                        tgts = assign_targets(a)
                        tested = {
                            n.id for n in ast.walk(b.test) if isinstance(n, ast.Name)
                        }
                        if tgts & tested:
                            flagged_lines.add(a.lineno)
                            findings.append(
                                (
                                    rel(p),
                                    a.lineno,
                                    f"{call_name(a.value)}() verdict tested but the "
                                    f"branch only logs - execution falls through",
                                )
                            )

        # Pattern 2 (name-level): result discarded or never effectively used.
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            branchy = consumed = None  # computed lazily, once per fn
            for sub in ast.walk(fn):
                if (
                    isinstance(sub, ast.Expr)
                    and isinstance(sub.value, ast.Call)
                    and call_name(sub.value) in gate_names
                ):
                    findings.append(
                        (
                            rel(p),
                            sub.lineno,
                            f"{call_name(sub.value)}() result discarded",
                        )
                    )
                elif (
                    isinstance(sub, ast.Assign)
                    and isinstance(sub.value, ast.Call)
                    and call_name(sub.value) in gate_names
                    and sub.lineno not in flagged_lines
                ):
                    tgts = assign_targets(sub)
                    if branchy is None:
                        branchy, consumed = classify_uses(fn)
                    if tgts and not (tgts & (branchy | consumed)):
                        findings.append(
                            (
                                rel(p),
                                sub.lineno,
                                f"{call_name(sub.value)}() -> "
                                f"{', '.join(sorted(tgts))} feeds no effective "
                                f"branch - logged, then overruled",
                            )
                        )
    return sorted(set(findings))  # nested defs are walked twice - dedupe


# ──────────────────────────────────────────────────────────────────────────
# R11 - dependency inventory
# ──────────────────────────────────────────────────────────────────────────


def audit_dependencies(trees):
    # Every .py stem in the repo counts as local: the engine uses flat
    # sys.path imports (`import checkpoint`, `import router`) for modules
    # living in subpackages - phd's D3 flags the style; they are not deps.
    local = {
        p.stem
        for p in ROOT.rglob("*.py")
        if not any(part in SKIP_PARTS for part in p.parts)
    } | {d.name for d in ROOT.iterdir() if d.is_dir()}
    stdlib = getattr(sys, "stdlib_module_names", set())
    imported = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
    third = sorted(m for m in imported if m not in stdlib and m not in local)
    # One canonical dependency source, checked in priority order — deps.py
    # writes .requirements, many repos use requirements.txt. Reading only one
    # name meant the two audits could contradict each other on the same repo.
    declared = set()
    found = False
    for name in ("requirements.txt", ".requirements"):
        req = ROOT / name
        if not req.exists():
            continue
        found = True
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("---"):
                declared.add(re.split(r"[<>=!\[ ]", line)[0].lower().replace("-", "_"))
    return third, declared, found


# ──────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────


def main():
    strict = "--strict" in sys.argv
    as_json = "--json" in sys.argv
    prod, tests = collect()
    sink = Sink()
    trees = {}
    for p, txt in prod.items():
        sink.register(rel(p), txt)
        try:
            trees[p] = ast.parse(txt)
        except SyntaxError as e:
            print(f"[warn] cannot parse {rel(p)}: {e}")

    stackless = collections.Counter()  # R8 per-file counts

    for p, tree in trees.items():
        f = rel(p)
        for node in ast.walk(tree):

            # R1 - unbounded while True. An await inside the body makes the
            # loop cancellable from outside (CancelledError lands at the
            # await) - that's the idiomatic asyncio service loop, INFO only.
            if (
                isinstance(node, ast.While)
                and isinstance(node.test, ast.Constant)
                and node.test.value
            ):
                if not loop_has_exit(node):
                    has_await = any(isinstance(s, ast.Await) for s in ast.walk(node))
                    if has_await:
                        sink.add(
                            "R1i",
                            f,
                            node.lineno,
                            "async forever-loop (cancellable at await; "
                            "verify a task.cancel path exists)",
                        )
                    else:
                        sink.add(
                            "R1",
                            f,
                            node.lineno,
                            "while True with no break/return/raise - runaway loop",
                        )

            # R2 - subprocess without timeout
            if (
                isinstance(node, ast.Call)
                and call_name(node) in SUBPROCESS_FNS
                and receiver_name(node) == "subprocess"
            ):
                has_kw = any(
                    kw.arg == "timeout" or kw.arg is None for kw in node.keywords
                )
                if not has_kw:
                    sink.add(
                        "R2",
                        f,
                        node.lineno,
                        f"subprocess.{call_name(node)}() without timeout=",
                    )

            # R3 - blocking waits called with no bound at all
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in BLOCKING_WAITS
                and not node.args
                and not node.keywords
            ):
                sink.add(
                    "R3",
                    f,
                    node.lineno,
                    f".{node.func.attr}() with no timeout - can block forever",
                )

            # R4 / R5 - path literals
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if ABS_PATH_RE.match(node.value):
                    sink.add(
                        "R4",
                        f,
                        node.lineno,
                        f"hardcoded absolute path {node.value[:50]!r}",
                    )
            if isinstance(node, ast.Call) and call_name(node) in ("open", "Path"):
                a = node.args[0] if node.args else None
                if (
                    isinstance(a, ast.Constant)
                    and isinstance(a.value, str)
                    and REL_FILE_RE.match(a.value)
                    and not ABS_PATH_RE.match(a.value)
                ):
                    sink.add(
                        "R5",
                        f,
                        node.lineno,
                        f"{call_name(node)}({a.value!r}) is CWD-relative - anchor to __file__",
                    )

            # R6 - text I/O without encoding
            if isinstance(node, ast.Call):
                cn = call_name(node)
                kws = {kw.arg for kw in node.keywords}
                if isinstance(node.func, ast.Name) and cn == "open" and None not in kws:
                    mode = ""
                    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = str(kw.value.value)
                    if "b" not in mode and "encoding" not in kws:
                        sink.add(
                            "R6",
                            f,
                            node.lineno,
                            "open() text mode without encoding= (cp1252 trap)",
                        )
                elif (
                    cn in ("read_text", "write_text")
                    and "encoding" not in kws
                    and None not in kws
                ):
                    sink.add(
                        "R6", f, node.lineno, f".{cn}() without encoding= (cp1252 trap)"
                    )

            # R7 - secrets in log calls
            if isinstance(node, ast.Call) and call_name(node) in LOG_CALLS:
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    hit = None
                    for sub in ast.walk(arg):
                        nm = (
                            sub.id
                            if isinstance(sub, ast.Name)
                            else (sub.attr if isinstance(sub, ast.Attribute) else "")
                        )
                        if (
                            nm
                            and SECRET_RE.search(nm)
                            and "mask" not in nm.lower()
                            and "redact" not in nm.lower()
                        ):
                            hit = nm
                            break
                    if hit:
                        sink.add(
                            "R7",
                            f,
                            node.lineno,
                            f"{call_name(node)}(...) references {hit!r} - secret in logs?",
                        )
                        break

            # R8 - exception logged without stack
            if isinstance(node, ast.ExceptHandler) and node.name:
                uses_e_in_log = False
                has_stack = False
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and call_name(sub) in LOG_CALLS:
                        for a in ast.walk(sub):
                            if isinstance(a, ast.Name) and a.id == node.name:
                                uses_e_in_log = True
                    nm = (
                        sub.attr
                        if isinstance(sub, ast.Attribute)
                        else (sub.id if isinstance(sub, ast.Name) else "")
                    )
                    if nm in TRACEBACK_MARKS:
                        has_stack = True
                    if isinstance(sub, ast.Raise):
                        has_stack = True
                if uses_e_in_log and not has_stack:
                    stackless[f] += 1

    # R9 - tool parity
    ghost, unknown, reg_found = audit_tool_parity(trees)
    for t in ghost:
        sink.add(
            "R9",
            "tools/registry.py",
            0,
            f"'{t}' dispatched in run_tool but NOT in TOOL_DEFINITIONS - "
            f"_ALLOWED_TOOLS blocks it before its branch runs",
        )
    for t in unknown:
        sink.add(
            "R9",
            "tools/registry.py",
            0,
            f"'{t}' in TOOL_DEFINITIONS but run_tool has no branch - "
            f"'[unknown tool]' at call time",
        )

    # R10 - prompts & hooks
    missing_files, orphans, wrong_brain, orphan_raw, unfillable = audit_prompts(trees)
    for n in missing_files:
        sink.add(
            "R10",
            "prompts/",
            0,
            f"prompt '{n}' referenced in code but prompts/{n}.md does not exist "
            f"(PromptMissingError mid-run)",
        )
    for fname, ph in unfillable:
        sink.add(
            "R10p",
            f"prompts/{fname}",
            0,
            "{" + "}, {".join(ph) + "} supplied by no call site - loader "
            "warns every load; literal braces reach the model",
        )
    for k in wrong_brain:
        sink.add(
            "R10",
            "agents/agent_manager.py",
            0,
            f"HOOK_ROLE['{k}'] has no register_raw_prompt('hook_{k}') - "
            f"hook silently runs with the generic system prompt",
        )
    for s in orphans:
        sink.add(
            "R10o",
            "prompts/",
            0,
            f"prompts/{s}.md referenced by nothing (stale brain?)",
        )
    for r in orphan_raw:
        sink.add(
            "R10o",
            "prompts/prompt_registry.py",
            0,
            f"raw prompt '{r}' registered but '{r[5:]}' is not a HOOK_ROLE key",
        )

    # R12 - prompt JSON-contract parity
    read_np, promised_nr = audit_llm_contracts(trees, prod)
    for fl, line, key, contract in read_np:
        sink.add(
            "R12",
            fl,
            line,
            f'reads "{key}" off an LLM-parsed dict, but none of the '
            f"prompts this module loads ({', '.join(contract)}) promise it",
        )
    for pname, key in promised_nr:
        sink.add(
            "R12i",
            f"prompts/{pname}.md",
            0,
            f'promises "{key}" but no module loading this prompt ever '
            f"references it",
        )

    # R13 - advisory gates
    for fl, line, msg in audit_gates(trees):
        sink.add("R13", fl, line, msg + " - gate cannot say no")

    # R11 - dependencies
    third, declared, has_req = audit_dependencies(trees)
    dep_lines = []
    if not has_req:
        dep_lines.append(
            f"no requirements.txt; third-party imports: {', '.join(third) or 'none'}"
        )
    else:
        missing = [t for t in third if t.lower().replace("-", "_") not in declared]
        unused = [
            d for d in declared if d not in {t.lower().replace("-", "_") for t in third}
        ]
        if missing:
            dep_lines.append(
                f"imported but not in requirements.txt: {', '.join(missing)}"
            )
        if unused:
            dep_lines.append(
                f"in requirements.txt but never imported: {', '.join(unused)}"
            )

    # ── report ───────────────────────────────────────────────────────────
    SECTIONS = [
        ("R1", "HIGH", "unbounded `while True` loops (runaway-run class)"),
        ("R9", "HIGH", "tool registry parity (TOOL_DEFINITIONS <-> run_tool)"),
        ("R10", "HIGH", "prompt/hook parity (missing prompt = wrong brain)"),
        ("R7", "HIGH", "secret-shaped identifiers in log calls"),
        ("R2", "HIGH", "subprocess calls without timeout="),
        ("R3", "MEDIUM", "blocking communicate()/wait()/join() with no bound"),
        ("R4", "MEDIUM", "hardcoded absolute paths"),
        ("R5", "MEDIUM", "CWD-relative file I/O"),
        ("R6", "MEDIUM", "text I/O without encoding= (Windows cp1252 trap)"),
        (
            "R10p",
            "MEDIUM",
            "prompt placeholders nothing fills (loader warns; raw {x} to model)",
        ),
        ("R12", "MEDIUM", "code reads LLM-response keys no loaded prompt promises"),
        (
            "R13",
            "MEDIUM",
            "advisory gates - check/verify results discarded or unbranched",
        ),
        ("R12i", "INFO", "prompt-promised JSON keys nothing reads (rotting contract)"),
        ("R10o", "INFO", "orphan prompts (on disk / registered, never used)"),
        ("R1i", "INFO", "async forever-loops (cancellable; verify cancel path)"),
    ]

    if as_json:
        out = {
            cid: [{"file": fl, "line": ln, "msg": m} for fl, ln, m in sink.data[cid]]
            for cid, _, _ in SECTIONS
            if sink.data.get(cid)
        }
        out["R8_stackless_logging"] = dict(stackless)
        out["R11_dependencies"] = dep_lines
        print(json.dumps(out, indent=2))
        high = sum(len(sink.data[c]) for c, s, _ in SECTIONS if s == "HIGH")
        sys.exit(1 if strict and high else 0)

    print(
        f"scanned: {len(trees)} production files, {len(tests)} test files "
        f"({sink.suppressed} findings suppressed via `# audit: ok`)"
    )
    if not reg_found:
        print("[warn] tools/registry.py not found - R9 skipped")
    print()

    high = 0
    for cid, sev, title in SECTIONS:
        items = sink.data.get(cid, [])
        if sev == "HIGH":
            high += len(items)
        if not items and sev != "HIGH":
            continue
        print("=" * 74)
        print(f"{cid} [{sev}] {title} - {len(items)} finding(s)")
        print("=" * 74)
        for fl, line, msg in sorted(items):
            loc = f"{fl}:{line}" if line else fl
            print(f"  {loc:48} {msg}")
        print()

    print("=" * 74)
    print(
        f"R8 [INFO] exceptions logged without stack (counts; fine for "
        f"one-liners) - {sum(stackless.values())} across {len(stackless)} files"
    )
    print("=" * 74)
    for fl, c in stackless.most_common(10):
        print(f"  {fl:48} {c}")

    print()
    print("=" * 74)
    print("R11 [INFO] dependency inventory")
    print("=" * 74)
    for line in dep_lines or ["  requirements.txt matches imports"]:
        print(f"  {line}")

    med = sum(len(sink.data[c]) for c, s, _ in SECTIONS if s == "MEDIUM")
    info = sum(len(sink.data[c]) for c, s, _ in SECTIONS if s == "INFO")
    print(f"\n{'=' * 74}")
    print(
        f"SUMMARY  HIGH: {high}   MEDIUM: {med}   INFO: {info}   "
        f"suppressed: {sink.suppressed}"
    )
    if strict and high:
        sys.exit(1)


if __name__ == "__main__":
    main()
