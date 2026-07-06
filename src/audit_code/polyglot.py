"""polyglot.py — language-agnostic wiring / phd / runtime audits.

Python gets the full ast-based audits (`wiring.py`, `phd.py`, `runtime.py`).
Every *other* language gets this portable, dependency-free subset:

  * wiring   — dead symbols: a definition whose name is never referenced
               anywhere else (the same token-appearance heuristic the Python
               wiring audit uses; conservative, so it under-reports rather than
               cries wolf).
  * phd      — correctness/security discipline: swallowed errors, catch-all
               handlers, dynamic code execution, hardcoded secrets, panics.
  * runtime  — hygiene: debug leftovers, debt markers, unbounded loops.

Each language declares a :class:`LangSpec` (file extensions, how a definition
looks, and which rules apply). Detection is by extension in a single tree walk,
so a language needs no full adapter to be scanned. Nothing is hardcoded to a
product domain, and an unsupported language returns an honest SKIP.
"""

import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from audit_code.audit_shared import SKIP_PARTS, configured_encoding
from audit_code.config import ADAPTER_EXCLUDE_DIRS
from audit_code.models import AuditResult, AuditStatus, Finding, Severity

# Names that are entry points / framework callbacks — defining them without a
# local caller is normal, so they must never be flagged as dead.
_COMMON_ENTRY = frozenset(
    {
        "main",
        "Main",
        "init",
        "Init",
        "setup",
        "setUp",
        "teardown",
        "tearDown",
        "toString",
        "hashCode",
        "equals",
        "constructor",
        "new",
        "default",
        "run",
        "start",
        "stop",
        "handler",
        "Handler",
        "index",
    }
)


@dataclass(frozen=True)
class Rule:
    """One regex-based finding rule."""

    rule_id: str
    severity: Severity
    message: str
    pattern: re.Pattern


@dataclass(frozen=True)
class DefPattern:
    """How a definition of a symbol looks, and whether a match is 'public'.

    ``regex`` must expose a named group ``name``. If ``public`` is set and it
    matches the source line, the symbol is treated as exported API and never
    flagged dead (we can only be confident about local symbols).
    """

    regex: re.Pattern
    public: re.Pattern | None = None


@dataclass(frozen=True)
class LangSpec:
    """Everything the portable scanners need to reason about one language."""

    language: str
    extensions: tuple[str, ...] = ()
    defs: tuple[DefPattern, ...] = ()
    phd_rules: tuple[Rule, ...] = ()
    runtime_rules: tuple[Rule, ...] = ()
    supports_wiring: bool = True


def _rule(rule_id, severity, message, pattern, flags=0) -> Rule:
    return Rule(rule_id, severity, message, re.compile(pattern, flags))


def _defp(regex, public=None) -> DefPattern:
    return DefPattern(re.compile(regex), re.compile(public) if public else None)


# ── Shared rules (apply to every language) ───────────────────────────────────

_IDENT = re.compile(r"[A-Za-z_]\w*")
_TODO = _rule(
    "poly-todo",
    Severity.INFO,
    "debt marker (TODO/FIXME/XXX/HACK)",
    r"(?://|#|/\*|--|;;?)\s*(TODO|FIXME|XXX|HACK)\b",
)
# Hardcoded secret: a non-trivial literal assigned to a secret-ish name,
# excluding obvious env lookups / empty placeholders. MEDIUM (false-positive
# prone across languages), so it informs rather than blocks.
_SECRET = _rule(
    "poly-secret",
    Severity.MEDIUM,
    "possible hardcoded secret (assign from a config/env source instead)",
    r"""(?ix)
        \b(api[_-]?key|secret|passwd|password|token|access[_-]?key|
           private[_-]?key|client[_-]?secret)
        \s*[:=]\s*
        (["'])(?!\s*$)
        (?!.*(env|process\.|getenv|os\.environ|\$\{|<|xxx|changeme|placeholder))
        [^"']{6,}\2
    """,
)
_SHARED_RUNTIME = (_TODO,)
_SHARED_PHD = (_SECRET,)


# ── Reusable rule building blocks ────────────────────────────────────────────

_EMPTY_CATCH = _rule(
    "poly-empty-catch",
    Severity.HIGH,
    "empty catch block silently swallows the error",
    r"catch\s*(\([^)]*\))?\s*\{\s*\}",
)
_DYN_EVAL = _rule(
    "poly-dynamic-exec",
    Severity.HIGH,
    "dynamic code execution (eval / new Function) — injection risk",
    r"\beval\s*\(|\bnew\s+Function\s*\(",
)
_CONSOLE_LOG = _rule(
    "poly-debug-leftover",
    Severity.INFO,
    "console.log left in source",
    r"\bconsole\.(log|debug)\s*\(",
)
# Handles `while (true)`, `while(1)`, `while true`, and `for (;;)`.
_UNBOUNDED = _rule(
    "poly-unbounded-loop",
    Severity.INFO,
    "unbounded loop — ensure it has an exit condition",
    r"\bwhile\s*\(?\s*(?:true|1)\b|for\s*\(\s*;\s*;\s*\)",
)
_BROAD_CATCH_JVM = _rule(
    "poly-broad-catch",
    Severity.MEDIUM,
    "catches a very broad exception type",
    r"catch\s*\(\s*(?:\w+\s*:\s*)?(Exception|Throwable|RuntimeException|Error)\b",
)


# ── Per-language specs ───────────────────────────────────────────────────────

_JS = LangSpec(
    language="javascript",
    extensions=(".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"),
    defs=(
        _defp(r"function\s+(?P<name>[A-Za-z_]\w*)\s*\(", public=r"\bexport\b"),
        _defp(r"class\s+(?P<name>[A-Za-z_]\w*)\b", public=r"\bexport\b"),
        _defp(
            r"(?:const|let)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?:async\s*)?\(",
            public=r"\bexport\b",
        ),
    ),
    phd_rules=(_EMPTY_CATCH, _DYN_EVAL),
    runtime_rules=(_CONSOLE_LOG, _UNBOUNDED),
)

_GO = LangSpec(
    language="go",
    extensions=(".go",),
    defs=(
        # Unexported (lowercase-initial) funcs only; capitalised = exported API.
        _defp(r"func\s+(?P<name>[a-z]\w*)\s*\("),
        _defp(r"func\s+\([^)]*\)\s+(?P<name>[a-z]\w*)\s*\("),
    ),
    phd_rules=(
        _rule(
            "poly-empty-errcheck",
            Severity.MEDIUM,
            "error checked but body is empty — the error is ignored",
            r"if\s+err\s*!=\s*nil\s*\{\s*\}",
        ),
    ),
    runtime_rules=(_UNBOUNDED,),
)

_RUST = LangSpec(
    language="rust",
    extensions=(".rs",),
    defs=(_defp(r"fn\s+(?P<name>[A-Za-z_]\w*)\s*\(", public=r"\bpub\b"),),
    phd_rules=(
        _rule(
            "poly-rust-unwrap",
            Severity.MEDIUM,
            ".unwrap()/.expect() can panic — handle the Result/Option",
            r"\.unwrap\s*\(\s*\)|\.expect\s*\(",
        ),
        _rule(
            "poly-panic",
            Severity.MEDIUM,
            "panic!/unreachable! aborts the process",
            r"\b(panic|unreachable|todo|unimplemented)\s*!\s*\(",
        ),
    ),
    runtime_rules=(_UNBOUNDED,),
)

_JAVA = LangSpec(
    language="java",
    extensions=(".java",),
    defs=(
        # Only private methods — public/protected are API we can't judge dead.
        _defp(
            r"private\s+(?:static\s+)?(?:final\s+)?[A-Za-z_][\w<>\[\],.\s]*?\s+"
            r"(?P<name>[A-Za-z_]\w*)\s*\("
        ),
    ),
    phd_rules=(_EMPTY_CATCH, _BROAD_CATCH_JVM),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "System.out.println left in source",
            r"System\.(out|err)\.print(ln)?\s*\(",
        ),
        _UNBOUNDED,
    ),
)

_CSHARP = LangSpec(
    language="csharp",
    extensions=(".cs",),
    defs=(
        _defp(
            r"private\s+(?:static\s+)?(?:async\s+)?[A-Za-z_][\w<>\[\],.\s]*?\s+"
            r"(?P<name>[A-Za-z_]\w*)\s*\("
        ),
    ),
    phd_rules=(_EMPTY_CATCH, _BROAD_CATCH_JVM),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "Console.WriteLine left in source",
            r"Console\.Write(Line)?\s*\(",
        ),
        _UNBOUNDED,
    ),
)

_CPP = LangSpec(
    language="cpp",
    extensions=(".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"),
    # C/C++ linkage + headers make dead-symbol detection unreliable; skip wiring
    # but still apply the portable pattern rules.
    supports_wiring=False,
    phd_rules=(
        _rule(
            "poly-empty-catch",
            Severity.HIGH,
            "empty catch block silently swallows the error",
            r"catch\s*\([^)]*\)\s*\{\s*\}",
        ),
        _rule(
            "poly-catch-all",
            Severity.MEDIUM,
            "catch(...) swallows every exception type",
            r"catch\s*\(\s*\.\.\.\s*\)",
        ),
        _rule(
            "poly-unsafe-c",
            Severity.HIGH,
            "unsafe C function (gets/strcpy/sprintf) — buffer overflow risk",
            r"\b(gets|strcpy|strcat|sprintf)\s*\(",
        ),
    ),
    runtime_rules=(_UNBOUNDED,),
)

_KOTLIN = LangSpec(
    language="kotlin",
    extensions=(".kt", ".kts"),
    defs=(_defp(r"private\s+fun\s+(?P<name>[A-Za-z_]\w*)\s*\("),),
    phd_rules=(
        _EMPTY_CATCH,
        _BROAD_CATCH_JVM,
        _rule(
            "poly-kotlin-notnull",
            Severity.MEDIUM,
            "!! not-null assertion can throw NPE",
            r"[\w\)\]]\s*!!",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "println left in source",
            r"\bprintln\s*\(",
        ),
        _UNBOUNDED,
    ),
)

_SWIFT = LangSpec(
    language="swift",
    extensions=(".swift",),
    defs=(_defp(r"private\s+func\s+(?P<name>[A-Za-z_]\w*)\s*\("),),
    phd_rules=(
        _rule(
            "poly-swift-force",
            Severity.MEDIUM,
            "try!/as! force operation can crash at runtime",
            r"\btry!\s|\bas!\s",
        ),
        _rule(
            "poly-fatal",
            Severity.MEDIUM,
            "fatalError() aborts the process",
            r"\bfatalError\s*\(",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "print left in source",
            r"\bprint\s*\(",
        ),
        _UNBOUNDED,
    ),
)

_DART = LangSpec(
    language="dart",
    extensions=(".dart",),
    # Dart marks privacy with a leading underscore.
    defs=(
        _defp(
            r"(?P<name>_[A-Za-z]\w*)\s*\([^;=]*\)\s*(?:async\s*)?(?:\*\s*)?\{",
        ),
    ),
    phd_rules=(_EMPTY_CATCH,),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "print left in source",
            r"\bprint\s*\(",
        ),
        _UNBOUNDED,
    ),
)

_RUBY = LangSpec(
    language="ruby",
    extensions=(".rb",),
    defs=(_defp(r"\bdef\s+(?P<name>[a-z_][\w]*[!?]?)\b"),),
    phd_rules=(
        _rule(
            "poly-dynamic-exec",
            Severity.HIGH,
            "eval executes dynamic code — injection risk",
            r"\beval\s*\(",
        ),
        _rule(
            "poly-broad-catch",
            Severity.MEDIUM,
            "rescue Exception / bare rescue catches everything",
            r"rescue\s+Exception\b|rescue\s*(?:=>|$)",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.MEDIUM,
            "debugger breakpoint left in source (binding.pry / byebug)",
            r"\bbinding\.pry\b|\bbyebug\b",
        ),
    ),
)

_PHP = LangSpec(
    language="php",
    extensions=(".php",),
    defs=(_defp(r"private\s+function\s+(?P<name>[A-Za-z_]\w*)\s*\("),),
    phd_rules=(
        _EMPTY_CATCH,
        _rule(
            "poly-dynamic-exec",
            Severity.HIGH,
            "eval executes dynamic code — injection risk",
            r"\beval\s*\(",
        ),
        _rule(
            "poly-broad-catch",
            Severity.MEDIUM,
            "catches a very broad exception type",
            r"catch\s*\(\s*\\?(Exception|Throwable)\b",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "var_dump/print_r debug output left in source",
            r"\b(var_dump|print_r|var_export)\s*\(",
        ),
        _UNBOUNDED,
    ),
)

_ZIG = LangSpec(
    language="zig",
    extensions=(".zig",),
    defs=(_defp(r"fn\s+(?P<name>[A-Za-z_]\w*)\s*\(", public=r"\bpub\b"),),
    phd_rules=(
        _rule(
            "poly-panic",
            Severity.MEDIUM,
            "@panic / catch unreachable aborts the process",
            r"@panic\s*\(|catch\s+unreachable\b",
        ),
    ),
    runtime_rules=(_UNBOUNDED,),
)

_SCALA = LangSpec(
    language="scala",
    extensions=(".scala", ".sc"),
    defs=(_defp(r"private\s+def\s+(?P<name>[A-Za-z_]\w*)\s*[\(\[:]"),),
    phd_rules=(
        _rule(
            "poly-scala-null",
            Severity.MEDIUM,
            "null used — prefer Option",
            r"(?<![\w.])null(?![\w])",
        ),
        _rule(
            "poly-scala-cast",
            Severity.MEDIUM,
            "asInstanceOf can throw ClassCastException",
            r"\.asInstanceOf\s*\[",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "println left in source",
            r"\bprintln\s*\(",
        ),
        _UNBOUNDED,
    ),
)

_LUA = LangSpec(
    language="lua",
    extensions=(".lua",),
    defs=(_defp(r"local\s+function\s+(?P<name>[A-Za-z_]\w*)\s*\("),),
    phd_rules=(
        _rule(
            "poly-dynamic-exec",
            Severity.HIGH,
            "load/loadstring executes dynamic code — injection risk",
            r"\bload(string)?\s*\(",
        ),
        _rule(
            "poly-shell",
            Severity.MEDIUM,
            "os.execute runs a shell command",
            r"\bos\.execute\s*\(",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "print left in source",
            r"\bprint\s*\(",
        ),
        _rule(
            "poly-unbounded-loop",
            Severity.INFO,
            "unbounded loop — ensure it has an exit condition",
            r"\bwhile\s+true\b",
        ),
    ),
)

_HASKELL = LangSpec(
    language="haskell",
    extensions=(".hs",),
    # Module-export detection is unreliable via regex; skip wiring.
    supports_wiring=False,
    phd_rules=(
        _rule(
            "poly-haskell-unsafe",
            Severity.HIGH,
            "unsafePerformIO breaks referential transparency",
            r"\bunsafePerformIO\b",
        ),
        _rule(
            "poly-partial",
            Severity.MEDIUM,
            "partial function (undefined / fromJust) can crash",
            r"\bundefined\b|\bfromJust\b",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "Debug.Trace left in source",
            r"\bDebug\.Trace\b|\btrace\s+",
        ),
    ),
)

_ELIXIR = LangSpec(
    language="elixir",
    extensions=(".ex", ".exs"),
    defs=(_defp(r"\bdefp\s+(?P<name>[a-z_]\w*)\b"),),
    phd_rules=(
        _rule(
            "poly-dynamic-exec",
            Severity.HIGH,
            "Code.eval executes dynamic code — injection risk",
            r"\bCode\.eval",
        ),
        _rule(
            "poly-atom-exhaustion",
            Severity.MEDIUM,
            "String.to_atom on external input can exhaust the atom table",
            r"\bString\.to_atom\s*\(",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.INFO,
            "IO.inspect left in source",
            r"\bIO\.inspect\s*\(",
        ),
    ),
)

_SQL = LangSpec(
    language="sql",
    extensions=(".sql",),
    supports_wiring=False,
    phd_rules=(
        _rule(
            "poly-sql-delete-all",
            Severity.HIGH,
            "DELETE without a WHERE clause removes every row",
            r"(?is)\bdelete\s+from\s+[\w.\"'`]+\s*;",
        ),
        _rule(
            "poly-sql-update-all",
            Severity.HIGH,
            "UPDATE without a WHERE clause rewrites every row",
            r"(?is)\bupdate\s+[\w.\"'`]+\s+set\b(?:(?!\bwhere\b)[^;])*;",
        ),
    ),
    runtime_rules=(
        _rule(
            "poly-sql-select-star",
            Severity.INFO,
            "SELECT * — list columns explicitly",
            r"(?i)\bselect\s+\*",
        ),
    ),
)

_ALL_SPECS = (
    _JS,
    _GO,
    _RUST,
    _JAVA,
    _CSHARP,
    _CPP,
    _KOTLIN,
    _SWIFT,
    _DART,
    _RUBY,
    _PHP,
    _ZIG,
    _SCALA,
    _LUA,
    _HASKELL,
    _ELIXIR,
    _SQL,
)

# Lookup by language name (plus the common `typescript`/`c` aliases).
SPECS: dict[str, LangSpec] = {s.language: s for s in _ALL_SPECS}
SPECS["typescript"] = _JS
SPECS["c"] = _CPP

# Extension → canonical language, for single-walk detection.
_EXT_LANG: dict[str, str] = {}
for _spec in _ALL_SPECS:
    for _ext in _spec.extensions:
        _EXT_LANG[_ext] = _spec.language


# ── File discovery ───────────────────────────────────────────────────────────


def _project_excludes(root: Path) -> set[str]:
    """Extra skip names declared in the target's .audit-test-ignore."""
    extras: set[str] = set()
    ignore = root / ".audit-test-ignore"
    try:
        for line in ignore.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                extras.add(line)
    except OSError:
        pass
    return extras


def detect(root: Path) -> dict[str, list[Path]]:
    """One tree walk → ``{language: [files]}`` for every spec with files present."""
    root = Path(root)
    skip = ADAPTER_EXCLUDE_DIRS | SKIP_PARTS | _project_excludes(root)
    found: dict[str, list[Path]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            lang = _EXT_LANG.get(os.path.splitext(fn)[1])
            if lang:
                found.setdefault(lang, []).append(Path(dirpath) / fn)
    return found


# ── Scanners ─────────────────────────────────────────────────────────────────


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _line_at(text: str, pos: int) -> str:
    """The full source line containing offset *pos*."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end != -1 else len(text)]


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read(path: Path, encoding: str) -> str:
    try:
        return path.read_text(encoding=encoding, errors="replace")
    except OSError:
        return ""


def wiring_scan(root: Path, sources: dict[Path, str], spec: LangSpec) -> list[Finding]:
    """Flag definitions whose name is never referenced anywhere in the corpus."""
    if not spec.supports_wiring or not spec.defs:
        return []
    # One tokenisation of the whole corpus → reference counts per identifier.
    refs: Counter = Counter()
    for text in sources.values():
        refs.update(_IDENT.findall(text))

    definitions: dict[str, list[tuple[Path, int]]] = {}
    for path, text in sources.items():
        for dp in spec.defs:
            for m in dp.regex.finditer(text):
                name = m.group("name")
                if len(name) < 3 or name in _COMMON_ENTRY:
                    continue
                # Public/exported modifiers (export, pub) sit before the matched
                # definition, so test the whole source line, not just the match.
                if dp.public and dp.public.search(_line_at(text, m.start())):
                    continue
                definitions.setdefault(name, []).append(
                    (path, _line_of(text, m.start()))
                )

    findings: list[Finding] = []
    for name, sites in definitions.items():
        # A reference count no greater than the number of definition sites means
        # the name appears only where it is defined — nothing uses it.
        if refs.get(name, 0) <= len(sites):
            for path, line in sites:
                findings.append(
                    Finding(
                        rule_id="poly-wiring-dead",
                        severity=Severity.MEDIUM,
                        message=f"{name!r} defined but never referenced",
                        file=_rel(path, root),
                        line=line,
                        language=spec.language,
                        source="polyglot",
                    )
                )
    return findings


def _apply_rules(
    root: Path, sources: dict[Path, str], rules: tuple[Rule, ...], lang: str
) -> list[Finding]:
    findings: list[Finding] = []
    for path, text in sources.items():
        for rule in rules:
            for m in rule.pattern.finditer(text):
                findings.append(
                    Finding(
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        message=rule.message,
                        file=_rel(path, root),
                        line=_line_of(text, m.start()),
                        language=lang,
                        source="polyglot",
                    )
                )
    return findings


def run(kind: str, root: Path, language: str, files: list[Path]) -> AuditResult:
    """Run one portable audit (``kind``) for ``language`` over ``files``.

    Returns an honest SKIP when the language has no spec, never a fake pass.
    """
    audit_id = f"{language}-{kind}"
    spec = SPECS.get(language)
    if spec is None:
        return AuditResult(
            audit_id=audit_id,
            status=AuditStatus.SKIP,
            stdout=f"SKIP: {kind} not supported for {language}",
        )
    root = Path(root)
    encoding = configured_encoding(root)
    sources = {f: _read(f, encoding) for f in files}

    if kind == "wiring":
        findings = wiring_scan(root, sources, spec)
    elif kind == "phd":
        findings = _apply_rules(root, sources, spec.phd_rules + _SHARED_PHD, language)
    elif kind == "runtime":
        findings = _apply_rules(
            root, sources, spec.runtime_rules + _SHARED_RUNTIME, language
        )
    else:
        return AuditResult(
            audit_id=audit_id,
            status=AuditStatus.SKIP,
            stdout=f"SKIP: unknown audit kind {kind!r}",
        )

    return _to_result(audit_id, kind, language, findings, len(files))


def _to_result(
    audit_id: str, kind: str, language: str, findings: list[Finding], n_files: int
) -> AuditResult:
    high = sum(1 for f in findings if f.severity == Severity.HIGH)
    med = sum(1 for f in findings if f.severity == Severity.MEDIUM)
    status = (
        AuditStatus.FAIL
        if high
        else (AuditStatus.WARN if med or findings else AuditStatus.PASS)
    )
    lines = [
        f"{language} {kind}: scanned {n_files} file(s), {len(findings)} finding(s)"
    ]
    for f in findings[:50]:
        loc = f"{f.file}:{f.line}" if f.file else ""
        lines.append(f"  [{f.severity.value}] {loc}  {f.message}")
    if len(findings) > 50:
        lines.append(f"  ... {len(findings) - 50} more")
    return AuditResult(
        audit_id=audit_id,
        status=status,
        findings=findings,
        completed=True,
        stdout="\n".join(lines),
    )
