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

# ── Cross-language shared rules ───────────────────────────────────────────────

_SHELL_EXEC = _rule(
    "poly-shell-exec",
    Severity.HIGH,
    "shell command execution — injection risk, prefer subprocess/exec with args",
    r"\b(?:system|exec|shell_exec|popen)\s*\(",
)
_DEFER_IN_LOOP = _rule(
    "poly-defer-in-loop",
    Severity.MEDIUM,
    "defer inside a loop — resources accumulate, defers run after the function returns",
    r"(?s)\b(for|range)\b.*?\n.*?\bdefer\b",
)
_UNSAFE_RUST = _rule(
    "poly-rust-unsafe-block",
    Severity.HIGH,
    "unsafe block — bypasses Rust's memory safety guarantees",
    r"\bunsafe\s*\{",
)
_THREAD_SLEEP = _rule(
    "poly-thread-sleep",
    Severity.MEDIUM,
    "Thread.sleep() in request handler — blocks the thread pool, use async instead",
    r"\bThread\.sleep\s*\(|\bThread\.Sleep\s*\(",
)
_HARD_EXIT = _rule(
    "poly-hard-exit",
    Severity.MEDIUM,
    "System.exit() / Environment.Exit() — hard shutdown, prefer graceful termination",
    r"\b(?:System\.exit|Environment\.Exit)\s*\(",
)
_GO_GOTO = _rule(
    "poly-go-goto",
    Severity.MEDIUM,
    "goto statement — unstructured control flow, use loops or early returns",
    r"\bgoto\b",
)

# ── JavaScript-specific rules ─────────────────────────────────────────────────

_JS_XSS_DANGEROUS_HTML = _rule(
    "poly-js-xss-dangerous-html",
    Severity.HIGH,
    "dangerouslySetInnerHTML — React XSS vector, use a sanitizer instead",
    r"dangerouslySetInnerHTML",
)
_JS_XSS_INNERHTML = _rule(
    "poly-js-xss-innerhtml",
    Severity.HIGH,
    ".innerHTML assignment — DOM XSS vector, use textContent or sanitize",
    r"\.innerHTML\s*=",
)
_JS_STORAGE_TOKEN = _rule(
    "poly-js-storage-token",
    Severity.HIGH,
    "localStorage/sessionStorage storing sensitive data — prefer httpOnly cookies",
    r"""(?ix)
        (?:localStorage|sessionStorage)\.setItem\s*\(\s*
        [\"'](?:token|jwt|auth|access[_-]?token|refresh[_-]?token|
               secret|api[_-]?key|password|credential)
    """,
)
_JS_XSS_DOCUMENT_WRITE = _rule(
    "poly-js-xss-document-write",
    Severity.HIGH,
    "document.write() — DOM XSS vector, use DOM manipulation instead",
    r"document\.write\s*\(",
)
_JS_FETCH_NO_SIGNAL = _rule(
    "poly-js-fetch-no-signal",
    Severity.MEDIUM,
    "fetch() without AbortSignal — request can hang forever, add a timeout",
    r"""(?x)
        \bfetch\s*\(
        # `.` doesn't match newlines, so a plain `.*` lookahead is blind to
        # `signal:` sitting on a later line of a multi-line options object —
        # the common case once a formatter wraps the call. [\s\S] does match
        # newlines; bounded to 400 chars so an unrelated `signal` elsewhere
        # in a large file can't mask a real finding.
        (?![\s\S]{0,400}?\bsignal\b)
    """,
)
_JS_TIMER_STRING = _rule(
    "poly-js-timer-string",
    Severity.HIGH,
    "setTimeout/setInterval with string argument — eval-like code execution",
    r"(?:setTimeout|setInterval)\s*\(\s*['\"]",
)
_HARDCODED_URL = _rule(
    "poly-js-hardcoded-url",
    Severity.HIGH,
    "hardcoded https:// URL in JS bundle — use env var instead",
    r"https?://[a-zA-Z0-9.-]+(?:onrender|vercel|heroku|netlify|fly\.dev)\.\w{2,}/[^\s\"']*",
)
_JS_HOOK_CONDITIONAL = _rule(
    "poly-js-hook-conditional",
    Severity.MEDIUM,
    "React hook called inside a conditional — hooks must run in the same order every render",
    r"\bif\s*\([^)\n]*\)\s*\{?[^\n{}]*\buse[A-Z]\w*\s*\(",
)
_JS_PARSEINT_NO_RADIX = _rule(
    "poly-js-parseint-no-radix",
    Severity.MEDIUM,
    "parseInt() without a radix — '09' parses as 0 in some engines; pass 10",
    r"\bparseInt\s*\(\s*[^,()]+\)",
)
_JS_LOOSE_EQ = _rule(
    "poly-js-loose-eq",
    Severity.MEDIUM,
    "== type-coercing comparison ('' == 0 is true) — use ===",
    r"(?<![=!<>+\-*/%&|^])==(?!=)(?!\s*(?:null|undefined)\b)",
)
_TS_ANY = _rule(
    "poly-ts-any",
    Severity.INFO,
    "explicit `any` bypasses the type checker",
    r":\s*any\b|\bas\s+any\b",
)
_MONGO_INJECTION = _rule(
    "poly-mongo-injection",
    Severity.HIGH,
    "MongoDB $where/$eval with dynamic input — NoSQL injection risk, "
    "use $expr with parameterized aggregation instead",
    r"\$(?:where|eval)\s*:\s*(?!\s*(?:\"\"|''|true|false|null|[\d.-]+\s*[},]))",  # skips empty/literal
)
_MONGO_INJECTION_PY = _rule(
    "poly-mongo-injection",
    Severity.HIGH,
    "MongoDB $where/$eval/db.eval in Python — NoSQL injection risk",
    r"""(?ix)
        (?:\$where|\$eval|db\.eval|database\.eval|\.eval\s*\()
        \s*[:=]\s*
    """,
)
_TS_ENUM_NUMERIC = _rule(
    "poly-ts-enum-numeric",
    Severity.INFO,
    "numeric enum — values serialize as numbers; prefer a string enum or union type",
    r"(?s)\benum\s+\w+\s*\{[^}\"']*\}",
)
_JS_REACTIVE_DESTRUCTURE = _rule(
    "poly-js-reactive-destructure",
    Severity.MEDIUM,
    "destructuring a reactive() object loses reactivity — use toRefs()",
    r"(?:const|let|var)\s*\{[^}\n]*\}\s*=\s*reactive\s*\(",
)


# ── Per-language specs ──

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
    phd_rules=(
        _EMPTY_CATCH,
        _DYN_EVAL,
        _JS_XSS_DANGEROUS_HTML,
        _JS_XSS_INNERHTML,
        _JS_STORAGE_TOKEN,
        _JS_XSS_DOCUMENT_WRITE,
        _JS_FETCH_NO_SIGNAL,
        _JS_TIMER_STRING,
        _HARDCODED_URL,
        _JS_HOOK_CONDITIONAL,
        _JS_PARSEINT_NO_RADIX,
        _JS_LOOSE_EQ,
        _TS_ANY,
        _TS_ENUM_NUMERIC,
        _JS_REACTIVE_DESTRUCTURE,
        _MONGO_INJECTION,
    ),
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
        _DEFER_IN_LOOP,
        _GO_GOTO,
        _rule(
            "poly-go-mutex-value",
            Severity.MEDIUM,
            "sync.Mutex/WaitGroup passed by value — the copy is a separate lock",
            r"func\s[^\n{]*\([^)]*[^*\s(][ \t]+sync\.(?:Mutex|RWMutex|WaitGroup)\s*[,)]",
        ),
        _rule(
            "poly-go-timeafter-select-loop",
            Severity.MEDIUM,
            "time.After in a select loop — a new timer per iteration, "
            "not GC'd until it fires",
            r"(?s)\bfor\b[^\n]*\{.{0,400}?\bselect\s*\{.{0,300}?<-\s*time\.After\s*\(",
        ),
    ),
    runtime_rules=(
        _UNBOUNDED,
        _rule(
            "poly-go-empty-interface",
            Severity.INFO,
            "interface{} — use `any` (Go 1.18+)",
            r"\binterface\s*\{\s*\}",
        ),
    ),
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
        _UNSAFE_RUST,
        _rule(
            "poly-rust-expect-empty",
            Severity.MEDIUM,
            '.expect("") with an empty message — panics with no context',
            r'\.expect\s*\(\s*""\s*\)',
        ),
        _rule(
            "poly-rust-async-blocking",
            Severity.MEDIUM,
            "blocking call inside async fn — blocks the executor; "
            "use the async equivalent or spawn_blocking",
            r"(?s)\basync\s+fn\b[^{;]*\{.{0,600}?"
            r"\b(?:std::fs::\w+|thread::sleep|std::net::TcpStream::connect"
            r"|reqwest::blocking)",
        ),
    ),
    runtime_rules=(
        _UNBOUNDED,
        _rule(
            "poly-rust-clone-in-loop",
            Severity.INFO,
            ".clone() inside a loop — allocates every iteration; hoist or borrow",
            r"(?s)\bfor\s[^{;\n]*\{[^{}]{0,300}?\.clone\s*\(",
        ),
    ),
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
    phd_rules=(
        _EMPTY_CATCH,
        _BROAD_CATCH_JVM,
        _THREAD_SLEEP,
        _HARD_EXIT,
        _rule(
            "poly-java-string-eq",
            Severity.MEDIUM,
            "== compares String references, not values — use .equals()",
            r'"\s*[=!]=(?!=)|(?<![=!<>])[=!]=\s*"',
        ),
        _rule(
            "poly-java-legacy-date",
            Severity.MEDIUM,
            "legacy java.util date API — thread-unsafe; use java.time",
            r"\bnew\s+(?:Date|SimpleDateFormat|GregorianCalendar)\s*\("
            r"|\bCalendar\.getInstance\s*\(",
        ),
        _rule(
            "poly-java-optional-field",
            Severity.MEDIUM,
            "Optional as a field — Optional is for return values only",
            r"(?:private|protected|public)\s+(?:static\s+)?(?:final\s+)?"
            r"Optional<[^>\n]*>\s+\w+\s*[=;]",
        ),
        _rule(
            "poly-java-field-injection",
            Severity.MEDIUM,
            "@Autowired field injection — prefer constructor injection",
            r"@(?:Autowired|Inject)\b\s*(?:\r?\n\s*)?(?:private|protected|public)\b",
        ),
    ),
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
    phd_rules=(
        _EMPTY_CATCH,
        _BROAD_CATCH_JVM,
        _THREAD_SLEEP,
        _HARD_EXIT,
        _rule(
            "poly-cs-blocking-async",
            Severity.MEDIUM,
            ".Wait()/.Result on a Task — deadlock risk; await it instead",
            r"\.Wait\s*\(\s*\)|\.Result\b(?!\s*=[^=])"
            r"|\.GetAwaiter\s*\(\s*\)\s*\.GetResult\s*\(",
        ),
        _rule(
            "poly-cs-async-void",
            Severity.MEDIUM,
            "async void — exceptions are unobservable and crash the process; "
            "use async Task (UI event handlers excepted)",
            r"\basync\s+void\s+\w+\s*\(",
        ),
        _rule(
            "poly-cs-throw-ex",
            Severity.MEDIUM,
            "throw ex; resets the stack trace — use bare `throw;`",
            r"(?s)catch\s*\(\s*\w[\w.]*\s+(?P<ex>\w+)\s*\)\s*\{.{0,400}?"
            r"\bthrow\s+(?P=ex)\s*;",
        ),
    ),
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
            r"\b(gets|strcpy|strcat|sprintf|system)\s*\(",
        ),
        _rule(
            "poly-cpp-raw-new",
            Severity.MEDIUM,
            "raw `new` — prefer std::make_unique/make_shared (RAII)",
            r"^(?!.*(?:unique_ptr|shared_ptr|make_unique|make_shared|//))"
            r"[^\n]*=\s*new\s+[A-Za-z_]",
            re.M,
        ),
        _rule(
            "poly-c-malloc-unchecked",
            Severity.MEDIUM,
            "malloc/calloc result used without a NULL check",
            r"=\s*(?:malloc|calloc|realloc)\s*\([^;\n]*\)\s*;"
            r"(?!\s*(?:if\b|assert\b|//|/\*))",
        ),
        _rule(
            "poly-c-alloc-overflow",
            Severity.INFO,
            "multiplication inside malloc() can overflow — "
            "use calloc() or check bounds",
            r"\bmalloc\s*\([^;{}\n]*\*[^;{}\n]*\)",
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
        _rule(
            "poly-kotlin-globalscope",
            Severity.MEDIUM,
            "GlobalScope coroutines outlive their caller — "
            "use a lifecycle-bound CoroutineScope",
            r"\bGlobalScope\s*\.",
        ),
        _rule(
            "poly-kotlin-job-in-builder",
            Severity.MEDIUM,
            "Job() passed to a coroutine builder breaks the parent-child "
            "cancellation chain",
            r"\b(?:launch|async|withContext)\s*\(\s*(?:Supervisor)?Job\s*\(\s*\)",
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
        _rule(
            "poly-swift-unowned",
            Severity.INFO,
            "unowned crashes if the referent is deallocated — "
            "prefer weak unless lifetime is guaranteed",
            r"\bunowned\b",
        ),
        _rule(
            "poly-swift-iuo",
            Severity.MEDIUM,
            "implicitly unwrapped optional (T!) — hidden force-unwrap "
            "on every access",
            r"^(?!.*@IBOutlet)[^\n]*\b(?:var|let)\s+\w+\s*:\s*"
            r"[A-Z][\w.<>\[\], ]*?!(?=\s*$|\s*=[^=]|\s*\{)",
            re.M,
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
        _SHELL_EXEC,
    ),
    runtime_rules=(
        _rule(
            "poly-debug-leftover",
            Severity.MEDIUM,
            "debugger breakpoint left in source (binding.pry / byebug)",
            r"\bbinding\.pry\b|\bbyebug\b",
        ),
        _UNBOUNDED,
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
        _SHELL_EXEC,
        _rule(
            "poly-php-loose-eq",
            Severity.MEDIUM,
            "== loose comparison ('0' == 0 is true) — use ===",
            r"(?<![=!<>])==(?!=)(?!\s*null\b)",
        ),
        _rule(
            "poly-php-sql-interp",
            Severity.HIGH,
            "variable interpolated/concatenated into an SQL query — "
            "use prepared statements with bound parameters",
            r"(?:->\s*(?:query|exec|prepare)\s*\(|\b(?:mysqli|mysql|pg)_query\s*\()"
            r"(?:[^;\n]*?\"[^\"\n]*\$\w+|[^;\n]*?['\"]\s*\.\s*\$\w+)",
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
        _UNBOUNDED,
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
        _rule(
            "poly-sql-drop",
            Severity.HIGH,
            "DROP TABLE / DROP DATABASE — irreversible data loss",
            r"(?i)\bdrop\s+(table|database)\b",
        ),
        _rule(
            "poly-sql-exec-injection",
            Severity.HIGH,
            "EXEC / sp_executesql with dynamic SQL — T-SQL injection risk, "
            "use sp_executesql with parameterized arguments",
            r"(?i)\b(?:EXEC\s*\(|sp_executesql\s+@?\w+|EXECUTE\s+@?\w+)",
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

_CSS = LangSpec(
    language="css",
    extensions=(".css", ".scss", ".less", ".sass"),
    # Stylesheets have no call graph; wiring does not apply.
    supports_wiring=False,
    runtime_rules=(
        _rule(
            "poly-css-important",
            Severity.INFO,
            "!important — specificity escalation; prefer a more specific selector",
            r"!\s*important\b",
        ),
        _rule(
            "poly-css-high-zindex",
            Severity.INFO,
            "z-index >= 100 — stacking-context escalation; define a z-scale",
            r"(?i)z-index\s*:\s*\d{3,}",
        ),
    ),
)

_YAML = LangSpec(
    language="yaml",
    extensions=(".yaml", ".yml"),
    supports_wiring=False,
    phd_rules=(
        _rule(
            "poly-k8s-run-as-root",
            Severity.HIGH,
            "container may run as root — set securityContext.runAsNonRoot: true "
            "and specify runAsUser",
            r"(?s)containers:\s*[-\s]\s*name:.*?(?=containers:|$)"
            r"(?!.*?securityContext:\s*\n\s*runAsNonRoot:\s*true)",
        ),
        _rule(
            "poly-k8s-latest-tag",
            Severity.MEDIUM,
            "container image uses :latest tag or no tag — "
            "non-reproducible deploys, pin to a digest or versioned tag",
            r"image:\s*[^\s:]+\s*(?:$|:(?:latest\s*$|$))",
            re.M,
        ),
        _rule(
            "poly-k8s-privileged",
            Severity.HIGH,
            "privileged: true — container has full host access",
            r"privileged:\s*true",
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
    _CSS,
    _YAML,
)

# Lookup by language name (plus the common `typescript`/`c` aliases).
SPECS: dict[str, LangSpec] = {s.language: s for s in _ALL_SPECS}
SPECS["typescript"] = _JS
SPECS["c"] = _CPP

# AST checkers keyed by language. A missing tree-sitter grammar wheel is an
# environment limitation: recorded in _AST_SKIPS and surfaced as a SKIP note
# in the phd output. Any other import failure means the rule pack itself is
# broken and must raise — a blanket `except ImportError: pass` here once hid
# a wholesale deletion of the JS rules. Only ModuleNotFoundError is caught
# below, so a pack that imports but lost its `run` still fails loudly.
_AST_CHECKS: dict[str, object] = {}
_AST_SKIPS: dict[str, str] = {}


def _ast_skip_or_raise(exc: ModuleNotFoundError, *languages: str) -> None:
    """Record an expected missing-grammar skip; re-raise anything else."""
    if exc.name and exc.name.startswith("tree_sitter"):
        for lang in languages:
            _AST_SKIPS[lang] = f"{exc.name} not installed"
        return
    raise exc


try:
    from audit_code.adapters.javascript.ast_rules import run as _js_ast_run
except ModuleNotFoundError as exc:
    _ast_skip_or_raise(exc, "javascript", "typescript")
else:
    _AST_CHECKS["javascript"] = _js_ast_run
    _AST_CHECKS["typescript"] = _js_ast_run

try:
    from audit_code.adapters.rust.phd import run as _rust_ast_run
except ModuleNotFoundError as exc:
    _ast_skip_or_raise(exc, "rust")
else:
    _AST_CHECKS["rust"] = _rust_ast_run

try:
    from audit_code.adapters.go.phd import run as _go_ast_run
except ModuleNotFoundError as exc:
    _ast_skip_or_raise(exc, "go")
else:
    _AST_CHECKS["go"] = _go_ast_run

try:
    from audit_code.adapters.java.phd import run as _java_ast_run
except ModuleNotFoundError as exc:
    _ast_skip_or_raise(exc, "java")
else:
    _AST_CHECKS["java"] = _java_ast_run

try:
    from audit_code.adapters.csharp.phd import run as _cs_ast_run
except ModuleNotFoundError as exc:
    _ast_skip_or_raise(exc, "csharp")
else:
    _AST_CHECKS["csharp"] = _cs_ast_run

try:
    from audit_code.adapters.kotlin.phd import run as _kt_ast_run
except ModuleNotFoundError as exc:
    _ast_skip_or_raise(exc, "kotlin")
else:
    _AST_CHECKS["kotlin"] = _kt_ast_run

try:
    from audit_code.adapters.swift.phd import run as _sw_ast_run
except ModuleNotFoundError as exc:
    _ast_skip_or_raise(exc, "swift")
else:
    _AST_CHECKS["swift"] = _sw_ast_run

try:
    from audit_code.adapters.php.phd import run as _php_ast_run
except ModuleNotFoundError as exc:
    _ast_skip_or_raise(exc, "php")
else:
    _AST_CHECKS["php"] = _php_ast_run

try:
    from audit_code.adapters.cpp.phd import run as _cpp_ast_run

    _AST_CHECKS["cpp"] = _cpp_ast_run
except ImportError as exc:
    _ast_skip_or_raise(exc, "cpp")

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


def _is_inline_function_expr(text: str, start: int) -> bool:
    """True when a `(` immediately precedes the definition keyword — a named
    function EXPRESSION (IIFE or inline callback), as opposed to a statement.
    `(function foo() {...})()` invokes `foo` by the surrounding parens, not by
    name reference, so requiring a second occurrence of the name is wrong."""
    i = start - 1
    while i >= 0 and text[i] in " \t\n\r":
        i -= 1
    return i >= 0 and text[i] == "("


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
                if _is_inline_function_expr(text, m.start()):
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


# A local `const/let/var/function fetch = ...` binding shadows the global
# fetch() API for the whole file — every bare `fetch(...)` call site then
# invokes that local wrapper, not a network call, so fetch-specific rules
# don't apply anywhere in such a file.
_FETCH_SHADOW_RE = re.compile(r"\b(?:const|let|var|function\s*\*?)\s+fetch\b")


def _apply_rules(
    root: Path, sources: dict[Path, str], rules: tuple[Rule, ...], lang: str
) -> list[Finding]:
    findings: list[Finding] = []
    for path, text in sources.items():
        fetch_shadowed = _FETCH_SHADOW_RE.search(text) is not None
        for rule in rules:
            if fetch_shadowed and rule.rule_id == "poly-js-fetch-no-signal":
                continue
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

    notes: tuple[str, ...] = ()
    if kind == "wiring":
        findings = wiring_scan(root, sources, spec)
    elif kind == "phd":
        findings = _apply_rules(root, sources, spec.phd_rules + _SHARED_PHD, language)
        check = _AST_CHECKS.get(language)
        if check is not None:
            findings.extend(check(root, files))  # type: ignore[operator]
        elif language in _AST_SKIPS:
            notes = (
                f"  SKIP: {language} AST rules not run "
                f"({_AST_SKIPS[language]}); regex rules still ran",
            )
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

    return _to_result(audit_id, kind, language, findings, len(files), notes)


def _to_result(
    audit_id: str,
    kind: str,
    language: str,
    findings: list[Finding],
    n_files: int,
    notes: tuple[str, ...] = (),
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
    lines.extend(notes)
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
