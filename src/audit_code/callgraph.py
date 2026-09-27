"""callgraph.py — cross-file, function-level call graph for Python.

One node per module, class, function and method, keyed by its fully
qualified name. Calls are resolved in levels and every edge records which
level produced it:

  L1  imports — plain names, aliases, relative imports, re-exports
  L2  the class system — self/cls/super(), inherited methods, X() -> __init__,
      typed instances (x = X(); annotations; self.attr = X())
  L3  guesses (opt-in, --guess) — obj.m() with an unknown type, by method name

Standard library only. Files that do not parse are skipped and reported.
Queries (callers/callees/subgraph), PageRank and traceback/symbol
localization live here too so the whole tool runs as one script.
"""

import argparse
import ast
import builtins
import gc
import json
import os
import re
import sys
from collections import deque
from pathlib import Path

from audit_code.audit_shared import (
    configured_encoding,
    force_utf8_streams,
    iter_py_files,
    parse_text,
)
from audit_code.audit_wiring import is_test

# ── config ──────────────────────────────────────────────────────────────────

# Overridable via [callgraph] in audit-code.toml. Weights score localization
# candidates; stopwords are general English/Python filler, not domain terms.
DEFAULTS: dict = {
    "depth": 3,
    "top": 10,
    "guess_cap": 5,
    "w_dist": 1.0,
    "w_stack": 0.5,
    "w_rank": 0.3,
    "w_name": 0.8,
    "w_feed": 1.5,
    "feed_hops": 3,
    "issue_seeds": 10,
    "test_penalty": 1.0,
    "stopwords": [
        "the", "and", "for", "with", "from", "this", "that", "not", "none",
        "true", "false", "return", "self", "cls", "def", "class", "when",
        "should", "error", "get", "set", "init", "new", "call", "value",
    ],  # fmt: skip
}

_KIND = {"module": "mod", "class": "cls", "function": "fn", "method": "fn"}
_LINK_TYPES = frozenset({"calls", "instantiates", "references", "decorates"})
_DEF_T = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_SKIP_T = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)
_BUILTINS = frozenset(dir(builtins))
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))
_IMPLICIT_CLASSMETHODS = frozenset(
    {"__new__", "__init_subclass__", "__class_getitem__"}
)
_LIB_MARKERS = ("site-packages", "dist-packages")
_MISS = object()
# Literal expressions have a builtin type: calls on them are external.
_LITERALS = {
    ast.Constant: "object", ast.JoinedStr: "str", ast.List: "list",
    ast.ListComp: "list", ast.Tuple: "tuple", ast.Dict: "dict",
    ast.DictComp: "dict", ast.Set: "set", ast.SetComp: "set",
    ast.GeneratorExp: "generator",
}  # fmt: skip

_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)'
)
# pytest report frames: `tests\x.py:66: ` / `app/x.py:120: TypeError` / `x.py:9: in f`
_PYTEST_FRAME_RE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\s:\"<>|]+\.py):(?P<line>\d+):(?:\s+in\s+(?P<func>\S+))?"
)
_PYTEST_SECTION_RE = re.compile(r"^_{3,} .+ _{3,}$", re.MULTILINE)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def load_config(root: Path) -> dict:
    """DEFAULTS merged with the target's [callgraph] table."""
    from audit_code.config import load_project_config

    cfg = dict(DEFAULTS)
    cfg.update(load_project_config(root).get("callgraph", {}) or {})
    return cfg


# ── model ───────────────────────────────────────────────────────────────────


class Node:
    """A module, class, function or method."""

    __slots__ = ("id", "kind", "name", "mod", "line", "end")

    def __init__(self, nid, kind, name, mod, line, end):
        self.id, self.kind, self.name = nid, kind, name
        self.mod, self.line, self.end = mod, line, end


class _Mod:
    __slots__ = ("name", "rel", "is_pkg", "text", "tree", "scope", "_lines")

    def __init__(self, name, rel, is_pkg, text, tree):
        self.name, self.rel, self.is_pkg = name, rel, is_pkg
        self.text, self.tree, self.scope, self._lines = text, tree, None, None

    def lines(self) -> list[str]:
        if self._lines is None:
            self._lines = self.text.splitlines()
        return self._lines


class _Scope:
    """Names bound in one module, class body or function body."""

    __slots__ = (
        "owner", "mod", "parent", "is_module", "cls", "defs", "imports",
        "stars", "stores", "assigns", "anns", "params", "cache",
    )  # fmt: skip

    def __init__(self, owner, mod, parent, is_module=False, cls=None):
        self.owner, self.mod, self.parent = owner, mod, parent
        self.is_module, self.cls = is_module, cls
        self.defs: dict[str, str] = {}
        self.imports: dict[str, str] = {}
        self.stars: list[str] = []
        self.stores: dict[str, int] = {}
        self.assigns: dict[str, ast.expr] = {}
        self.anns: dict[str, ast.expr] = {}
        self.params: dict[str, tuple] = {}
        self.cache: dict = {}


class _Cls:
    __slots__ = (
        "scope",
        "bases_expr",
        "enclosing",
        "attr_types",
        "bases",
        "ext_base",
        "mro",
    )

    def __init__(self, scope, bases_expr, enclosing):
        self.scope, self.bases_expr, self.enclosing = scope, bases_expr, enclosing
        self.attr_types: dict[str, tuple] = {}
        self.bases: list[str] | None = None
        self.ext_base = False
        self.mro: list[str] | None = None


def _abs_module(mod: _Mod, level: int, module: str | None) -> str | None:
    """Absolute dotted target of `from <level dots><module> import ...`."""
    if not level:
        return module
    parts = mod.name.split(".")
    if not mod.is_pkg:
        parts = parts[:-1]
    if level > 1:
        if level - 1 > len(parts):
            return None
        parts = parts[: len(parts) - (level - 1)]
    if module:
        parts = parts + module.split(".")
    return ".".join(parts) or None


def _iter_stmts(body):
    """Every statement in one scope, not descending into nested defs."""
    stack = list(reversed(body))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, _DEF_T):
            continue
        for f in ("body", "orelse", "finalbody", "handlers", "cases"):
            sub = getattr(n, f, None)
            if sub:
                stack.extend(reversed(sub))


def _target_names(t, out: list[str]) -> None:
    """Names bound by an assignment target (tuples and starred unpacked)."""
    tt = type(t)
    if tt is ast.Name:
        out.append(t.id)
    elif tt is ast.Tuple or tt is ast.List:
        for e in t.elts:
            _target_names(e, out)
    elif tt is ast.Starred:
        _target_names(t.value, out)


def _c3(seqs: list[list[str]]) -> list[str] | None:
    seqs = [list(s) for s in seqs if s]
    out: list[str] = []
    while seqs:
        for s in seqs:
            head = s[0]
            if not any(head in o[1:] for o in seqs):
                break
        else:
            return None
        out.append(head)
        seqs = [o[1:] if o[0] == head else o for o in seqs]
        seqs = [o for o in seqs if o]
    return out


# ── graph ───────────────────────────────────────────────────────────────────


class CallGraph:
    """Build with CallGraph(root).build(); then query."""

    def __init__(self, root: Path, guess: bool = False, guess_cap: int = 5):
        self.root = Path(root).resolve()
        self.guess, self.guess_cap = guess, guess_cap
        self.nodes: dict[str, Node] = {}
        self.mods: dict[str, _Mod] = {}
        self.classes: dict[str, _Cls] = {}
        self.edges: dict[tuple[str, str, str], list] = {}
        self.skipped: list[tuple[str, str]] = []
        self.stats = dict.fromkeys(
            (
                "calls",
                "resolved",
                "external",
                "unknown_attr",
                "unknown_name",
                "guess_too_many",
            ),
            0,
        )
        self._jobs: list[tuple[str, list, _Scope]] = []
        self._ast_ids: dict[int, str] = {}
        self._returns: dict[str, tuple] = {}
        self._fq_memo: dict[str, tuple | None] = {}
        self._mattr: dict[tuple[str, str], tuple | None] = {}
        self._attr_memo: dict[tuple[str, str], tuple | None] = {}
        self._by_method: dict[str, list[str]] | None = None
        self._out: dict[str, list] | None = None
        self._in: dict[str, list] | None = None
        self._pr: dict[str, float] | None = None

    # ── build ──

    def build(self) -> "CallGraph":
        # Parsing allocates millions of AST nodes that all stay alive; the
        # cyclic GC would rescan them repeatedly for nothing (~3x build time).
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            self._build()
        finally:
            if was_enabled:
                gc.enable()
        return self

    def _build(self) -> None:
        self._load_modules()
        for m in list({id(m): m for m in self.mods.values()}.values()):
            self._guarded(m.rel, m.name, self._index_module, m)
        for cid in list(self.classes):
            self._guarded(self.nodes[cid].mod.rel, cid, self._inherits, cid)
        for owner, body, scope in self._jobs:
            self._guarded(
                self.nodes[owner].mod.rel, owner, self._walk, owner, body, scope
            )
        self._jobs = []

    def _guarded(self, rel: str, where: str, fn, *args) -> None:
        """Run one unit of work (a module, class or body); a failure skips it.

        A resolution fault — pathological AST, cyclic hierarchy, a node the
        linker did not expect — must cost one unit, not the whole graph. It
        is reported in ``skipped`` with its location, never swallowed.
        """
        try:
            fn(*args)
        except RecursionError:
            self.skipped.append((rel, f"too deeply nested in {where}"))
        except Exception as e:  # audit: ok (isolate one unit; reported in skipped)
            self.skipped.append(
                (rel, f"internal error in {where}: {type(e).__name__}: {e}")
            )

    def _inherits(self, cid: str) -> None:
        for b in self._bases(cid):
            self._edge(cid, b, "inherits", self.nodes[cid].line, 1)

    def _load_modules(self) -> None:
        enc = configured_encoding(self.root)
        files = sorted(iter_py_files(self.root))
        pkg_dirs = {p.parent for p in files if p.name == "__init__.py"}
        loaded = []
        for p in files:
            rel = p.relative_to(self.root).as_posix()
            try:
                text = p.read_text(encoding=enc, errors="replace")
            except OSError as e:
                self.skipped.append((rel, f"unreadable: {e}"))
                continue
            try:
                tree, err = parse_text(p, text)
            except (ValueError, RecursionError) as e:  # null bytes, deep nesting
                tree, err = None, str(e)
            if tree is None:
                self.skipped.append((rel, f"does not parse: {err}"))
                continue
            is_pkg = p.name == "__init__.py"
            parts = [] if is_pkg else [p.stem]
            d = p.parent
            while d in pkg_dirs:
                parts.append(d.name)
                d = d.parent
            name = ".".join(reversed(parts)) or p.parent.name
            if name in self.mods:  # two scripts with one stem — fall back to the path
                name = ".".join(Path(rel).with_suffix("").parts)
            m = _Mod(name, rel, is_pkg, text, tree)
            self.mods[name] = m
            n_lines = text.count("\n") + 1
            self.nodes[name] = Node(
                name, "module", name.rsplit(".", 1)[-1], m, 1, n_lines
            )
            loaded.append(m)
        # Path-suffix aliases resolve namespace packages and sys.path-style
        # script imports; only unambiguous ones, never shadowing the stdlib.
        counts: dict[str, list[_Mod]] = {}
        for m in loaded:
            parts = Path(m.rel).with_suffix("").parts
            if m.is_pkg:
                parts = parts[:-1]
            for i in range(len(parts)):
                counts.setdefault(".".join(parts[i:]), []).append(m)
        for alias, ms in counts.items():
            if len(ms) == 1 and alias not in self.mods and alias:
                if "." in alias or alias not in _STDLIB:
                    self.mods[alias] = ms[0]

    def _new_node(self, nid, kind, d, mod) -> str:
        start = min([d.lineno] + [x.lineno for x in d.decorator_list])
        end = d.end_lineno or d.lineno
        n = self.nodes.get(nid)
        if n is not None and n.kind == "module":  # def shadows a same-named submodule
            nid = f"{nid}:{d.lineno}"
            n = self.nodes.get(nid)
        if n is None:
            self.nodes[nid] = Node(nid, kind, d.name, mod, start, end)
        else:  # redefinition (property setter, if/else variants): one node
            n.line, n.end = min(n.line, start), max(n.end, end)
        self._ast_ids[id(d)] = nid
        return nid

    def _index_module(self, m: _Mod) -> None:
        s = _Scope(m.name, m, None, is_module=True)
        m.scope = s
        self._index_scope(m.tree.body, s, m, m.name)
        self._jobs.append((m.name, m.tree.body, s))

    def _index_scope(self, body, scope, mod, owner_id, cls_id=None, self_name=None):
        for d in self._prescan(body, scope, mod, cls_id, self_name):
            nid = scope.defs.setdefault(d.name, f"{owner_id}.{d.name}")
            in_class = (
                owner_id if scope.cls == owner_id and not scope.is_module else None
            )
            self._index_def(d, nid, scope, mod, in_class)

    def _index_def(self, d, nid, enclosing: _Scope, mod: _Mod, class_id: str | None):
        # Class bodies do not enclose the functions/classes defined in them.
        parent = (
            enclosing.parent
            if (enclosing.cls == enclosing.owner and class_id)
            else enclosing
        )
        if isinstance(d, ast.ClassDef):
            nid = self._new_node(nid, "class", d, mod)
            s = _Scope(nid, mod, parent, cls=nid)
            c = self.classes.get(nid)
            if c is None:
                c = self.classes[nid] = _Cls(s, d.bases, enclosing)
            self._index_scope(d.body, s, mod, nid, cls_id=nid)
            for name, ann in s.anns.items():
                c.attr_types.setdefault(name, (s, ann, True))
        else:
            nid = self._new_node(nid, "method" if class_id else "function", d, mod)
            s = _Scope(nid, mod, parent, cls=class_id)
            self_name = self._bind_params(d, s, class_id, enclosing)
            self._returns.setdefault(nid, (enclosing, d.returns))
            self._index_scope(d.body, s, mod, nid, cls_id=class_id, self_name=self_name)
        self._jobs.append((nid, d.body, s))

    def _bind_params(self, d, s: _Scope, class_id, enclosing) -> str | None:
        a = d.args
        positional = a.posonlyargs + a.args
        for arg in positional + a.kwonlyargs:
            s.params[arg.arg] = ("ann", enclosing, arg.annotation)
        for arg in (a.vararg, a.kwarg):
            if arg is not None:
                s.params[arg.arg] = ("ann", enclosing, None)
        if not (class_id and positional):
            return None
        decos = {
            x.id if isinstance(x, ast.Name) else x.attr
            for x in d.decorator_list
            if isinstance(x, (ast.Name, ast.Attribute))
        }
        if "staticmethod" in decos:
            return None
        first = positional[0].arg
        if "classmethod" in decos or d.name in _IMPLICIT_CLASSMETHODS:
            s.params[first] = ("cls", class_id)
            return None
        s.params[first] = ("self", class_id)
        return first

    def _prescan(self, body, scope: _Scope, mod: _Mod, cls_id, self_name) -> list:
        """Bind a scope's names; return its nested def/class nodes."""
        nested = []
        glob: set[str] = set()
        attr_types = self.classes[cls_id].attr_types if (cls_id and self_name) else None
        stored: list[str] = []
        for n in _iter_stmts(body):
            t = type(n)
            if t in _DEF_T:
                nested.append(n)
            elif t is ast.Assign:
                for tg in n.targets:
                    _target_names(tg, stored)
                if len(n.targets) == 1:
                    tg = n.targets[0]
                    if type(tg) is ast.Name:
                        scope.assigns.setdefault(tg.id, n.value)
                    elif attr_types is not None and _is_self_attr(tg, self_name):
                        attr_types.setdefault(tg.attr, (scope, n.value, False))
            elif t is ast.AnnAssign:
                tg = n.target
                _target_names(tg, stored)
                if type(tg) is ast.Name:
                    scope.anns.setdefault(tg.id, n.annotation)
                elif attr_types is not None and _is_self_attr(tg, self_name):
                    attr_types.setdefault(tg.attr, (scope, n.annotation, True))
            elif t is ast.Import:
                for a in n.names:
                    if a.asname:
                        scope.imports[a.asname] = a.name
                    else:
                        top = a.name.split(".", 1)[0]
                        scope.imports.setdefault(top, top)
                    self._import_edge(mod, a.name, (), n.lineno)
            elif t is ast.ImportFrom:
                base = _abs_module(mod, n.level, n.module)
                if base is None:
                    continue
                names = []
                for a in n.names:
                    if a.name == "*":
                        scope.stars.append(base)
                    else:
                        scope.imports[a.asname or a.name] = f"{base}.{a.name}"
                        names.append(a.name)
                self._import_edge(mod, base, names, n.lineno)
            elif t in (ast.Global, ast.Nonlocal):
                glob.update(n.names)
            elif t is ast.AugAssign:
                _target_names(n.target, stored)
            elif t is ast.For or t is ast.AsyncFor:
                _target_names(n.target, stored)
            elif t is ast.With or t is ast.AsyncWith:
                for item in n.items:
                    if item.optional_vars is not None:
                        _target_names(item.optional_vars, stored)
            elif t is ast.ExceptHandler:
                if n.name:
                    stored.append(n.name)
        for name in stored:
            scope.stores[name] = scope.stores.get(name, 0) + 1
        for g in glob:  # rebinding an outer name is not a local binding
            scope.stores.pop(g, None)
            scope.assigns.pop(g, None)
        return nested

    def _import_edge(self, mod: _Mod, base: str, names, line: int) -> None:
        targets = [self.mods.get(f"{base}.{x}") for x in names] or [None]
        for t in targets:
            t = t or self.mods.get(base)
            if t is not None and t is not mod:
                self._edge(mod.name, t.name, "imports", line, 1)

    # ── resolution ──

    def _lookup(self, scope: _Scope, name: str):
        s = scope
        while s is not None:
            if s.is_module:
                r = self._module_attr(s.mod, name)
                if r is None and name in _BUILTINS:
                    return ("ext", f"builtins.{name}", 1)
                return r
            r = s.cache.get(name, _MISS)
            if r is not _MISS:
                return r
            if (
                name in s.params
                or name in s.defs
                or name in s.imports
                or name in s.anns
                or name in s.stores
            ):
                s.cache[name] = None  # cycle guard: x = x.y()
                r = s.cache[name] = self._local(s, name)
                return r
            s = s.parent
        return None

    def _local(self, s: _Scope, name: str):
        p = s.params.get(name)
        if p is not None:
            if p[0] == "self":
                return ("inst", p[1], 2)
            if p[0] == "cls":
                return ("cls", p[1], 2)
            return self._ann(p[1], p[2])
        d = s.defs.get(name)
        if d is not None:
            n = self.nodes.get(d)
            return (_KIND[n.kind], d, 1) if n else None
        imp = s.imports.get(name)
        if imp is not None:
            return self._fq(imp)
        ann = s.anns.get(name)
        if ann is not None:
            return self._ann(s, ann)
        if s.stores.get(name) == 1:
            v = s.assigns.get(name)
            if v is not None:
                return self._value(s, v)
        return None

    def _value(self, s: _Scope, v):
        t = type(v)
        if t is ast.Call:
            return self._call_result(s, v)
        if t is ast.Name or t is ast.Attribute:
            return self._expr(s, v)
        lit = _LITERALS.get(t)
        return ("ext", f"builtins.{lit}", 1) if lit else None

    def _expr(self, s: _Scope, e):
        t = type(e)
        if t is ast.Name:
            return self._lookup(s, e.id)
        if t is ast.Attribute:
            v = e.value
            if (
                type(v) is ast.Call
                and type(v.func) is ast.Name
                and v.func.id == "super"
            ):
                return self._super_attr(s, v, e.attr)
            base = self._expr(s, v)
            return self._attr(base, e.attr) if base is not None else None
        if t is ast.Call:
            return self._call_result(s, e)
        lit = _LITERALS.get(t)
        return ("ext", f"builtins.{lit}", 1) if lit else None

    def _call_result(self, s: _Scope, call):
        f = self._expr(s, call.func)
        if f is None:
            return None
        k = f[0]
        if k == "cls":
            return ("inst", f[1], f[2])
        if k == "fn":
            return self._returns_of(f[1])
        if k == "ext":
            return ("ext", f"{f[1]}()", f[2])
        return None

    def _returns_of(self, fid: str):
        key = ("<returns>", fid)
        r = self._attr_memo.get(key, _MISS)
        if r is not _MISS:
            return r
        self._attr_memo[key] = None
        scope, ann = self._returns.get(fid, (None, None))
        r = self._ann(scope, ann) if scope is not None else None
        self._attr_memo[key] = r
        return r

    def _ann(self, s: _Scope, a):
        """An annotation that names a repo class → an instance of it (L2)."""
        if a is None:
            return None
        t = type(a)
        if t is ast.Constant and isinstance(a.value, str):
            try:
                a = ast.parse(a.value, mode="eval").body
            except SyntaxError:
                return None
            t = type(a)
        if t is ast.BinOp and isinstance(a.op, ast.BitOr):
            sides = [x for x in (a.left, a.right) if not _is_none(x)]
            return self._ann(s, sides[0]) if len(sides) == 1 else None
        if t is ast.Subscript:
            head = (
                a.value.attr
                if type(a.value) is ast.Attribute
                else getattr(a.value, "id", "")
            )
            inner = a.slice
            if head == "Optional":
                return self._ann(s, inner)
            if head == "Annotated" and type(inner) is ast.Tuple and inner.elts:
                return self._ann(s, inner.elts[0])
            a = a.value  # list[X], dict[K, V]: the container is the type
        r = self._expr(s, a)
        if r is None:
            return None
        if r[0] == "cls":
            return ("inst", r[1], 2)
        return ("ext", r[1], 2) if r[0] == "ext" else None

    def _fq(self, fq: str):
        r = self._fq_memo.get(fq, _MISS)
        if r is not _MISS:
            return r
        self._fq_memo[fq] = None
        r = self._fq_memo[fq] = self._fq_uncached(fq)
        return r

    def _fq_uncached(self, fq: str):
        n = self.nodes.get(fq)
        if n is not None:
            return (_KIND[n.kind], fq, 1)
        m = self.mods.get(fq)
        if m is not None:
            return ("mod", m.name, 1)
        parts = fq.split(".")
        for i in range(len(parts) - 1, 0, -1):
            m = self.mods.get(".".join(parts[:i]))
            if m is None:
                continue
            r = self._module_attr(m, parts[i])
            for a in parts[i + 1 :]:
                if r is None:
                    break
                r = self._attr(r, a)
            return r
        return ("ext", fq, 1)

    def _module_attr(self, m: _Mod, name: str):
        key = (m.name, name)
        r = self._mattr.get(key, _MISS)
        if r is not _MISS:
            return r
        self._mattr[key] = None
        s = m.scope
        r = None
        d = s.defs.get(name)
        if d is not None:
            n = self.nodes.get(d)
            r = (_KIND[n.kind], d, 1) if n else None
        elif name in s.imports:
            r = self._fq(s.imports[name])
        else:
            sub = self.mods.get(f"{m.name}.{name}")
            if sub is not None:
                r = ("mod", sub.name, 1)
            elif name in s.anns or name in s.stores:
                r = self._local(s, name)
            elif not name.startswith("_"):
                for star in s.stars:
                    sm = self.mods.get(star)
                    if sm is None:
                        r = (
                            ("ext", f"{star}.{name}", 1)
                            if star.split(".")[0] not in self.mods
                            else None
                        )
                    else:
                        r = self._module_attr(sm, name)
                    if r is not None and r[0] != "ext":
                        break
        self._mattr[key] = r
        return r

    def _attr(self, ref, attr: str):
        k, i, lvl = ref
        if k == "mod":
            r = self._module_attr(self.mods[i], attr)
            return (r[0], r[1], max(r[2], lvl)) if r else None
        if k == "cls" or k == "inst":
            hit = self._member(i, attr)
            if hit is not None:
                nid, depth = hit
                inherited = 2 if (k == "inst" or depth) else lvl
                return (_KIND[self.nodes[nid].kind], nid, max(lvl, inherited))
            if k == "inst":
                t = self._attr_type(i, attr)
                if t is not None:
                    return t
            if self._has_ext_base(i):
                return ("ext", f"{i}.{attr}", lvl)
            return None
        if k == "ext":
            return ("ext", f"{i}.{attr}", lvl)
        return None

    def _member(self, cid: str, attr: str):
        """(node id, MRO depth) of attr defined on cid or a repo ancestor."""
        key = (cid, attr)
        r = self._attr_memo.get(key, _MISS)
        if r is not _MISS:
            return r
        self._attr_memo[key] = None
        r = None
        for depth, c in enumerate(self._mro(cid)):
            cs = self.classes[c].scope
            nid = cs.defs.get(attr)
            if nid is not None and nid in self.nodes:
                r = (nid, depth)
                break
            if attr in cs.imports or attr in cs.assigns:
                x = self._lookup(cs, attr)
                if x is not None and x[0] in ("fn", "cls"):
                    r = (x[1], depth)
                    break
        self._attr_memo[key] = r
        return r

    def _attr_type(self, cid: str, attr: str):
        key = (cid, "." + attr)
        r = self._attr_memo.get(key, _MISS)
        if r is not _MISS:
            return r
        self._attr_memo[key] = None
        r = None
        for c in self._mro(cid):
            spec = self.classes[c].attr_types.get(attr)
            if spec is not None:
                s, expr, is_ann = spec
                r = self._ann(s, expr) if is_ann else self._value(s, expr)
                if r is not None:
                    r = (r[0], r[1], 2)
                    break
        self._attr_memo[key] = r
        return r

    def _super_attr(self, s: _Scope, call, attr: str):
        cid = None
        if call.args:
            c = self._expr(s, call.args[0])
            cid = c[1] if c and c[0] == "cls" else None
        else:
            sc = s
            while sc is not None and cid is None:
                cid = sc.cls if sc.cls in self.classes else None
                sc = sc.parent
        if cid is None:
            return None
        for c in self._mro(cid)[1:]:
            nid = self.classes[c].scope.defs.get(attr)
            if nid is not None and nid in self.nodes:
                return (_KIND[self.nodes[nid].kind], nid, 2)
        return ("ext", f"super.{attr}", 2) if self._has_ext_base(cid) else None

    def _bases(self, cid: str) -> list[str]:
        c = self.classes[cid]
        if c.bases is not None:
            return c.bases
        c.bases = []
        out = []
        for b in c.bases_expr:
            if type(b) is ast.Subscript:  # Generic[T], Base[int]
                b = b.value
            r = self._expr(c.enclosing, b)
            if r is not None and r[0] == "cls" and r[1] != cid:
                out.append(r[1])
            else:
                c.ext_base = True
        c.bases = out
        return out

    def _mro(self, cid: str) -> list[str]:
        c = self.classes[cid]
        if c.mro is not None:
            return c.mro
        c.mro = [cid]  # cycle guard
        bases = self._bases(cid)
        merged = _c3([self._mro(b) for b in bases] + [bases])
        if merged is None:  # inconsistent hierarchy: plain depth-first order
            merged = list(dict.fromkeys(x for b in bases for x in self._mro(b)))
        c.mro = [cid] + [x for x in merged if x != cid]
        return c.mro

    def _has_ext_base(self, cid: str) -> bool:
        return any(self.classes[c].ext_base for c in self._mro(cid))

    # ── walking bodies ──

    def _walk(self, caller: str, body, scope: _Scope) -> None:
        stack = [(n, True) for n in body]
        while stack:
            n, ref_ok = stack.pop()
            t = type(n)
            if t is ast.Call:
                self._on_call(caller, scope, n)
                f = n.func
                if type(f) is ast.Attribute:
                    stack.append((f.value, False))
                elif type(f) is not ast.Name:
                    stack.append((f, False))
                stack.extend((a, True) for a in n.args)
                stack.extend((k.value, True) for k in n.keywords)
            elif t is ast.Name:
                if ref_ok and type(n.ctx) is ast.Load:
                    self._on_ref(caller, scope, n)
            elif t is ast.Attribute:
                if ref_ok and type(n.ctx) is ast.Load:
                    self._on_ref(caller, scope, n)
                stack.append((n.value, False))
            elif t in _DEF_T:
                self._on_decorators(scope, n)
                stack.extend((x, False) for x in n.decorator_list)
                if t is not ast.ClassDef:
                    # Defaults resolve in this scope but belong to the def:
                    # `auth=Depends(require_auth)` is the endpoint's dependency.
                    a = n.args
                    dflts = a.defaults + [x for x in a.kw_defaults if x is not None]
                    self._walk(self._ast_ids.get(id(n), caller), dflts, scope)
            elif t is ast.Lambda:
                stack.append((n.body, True))
                stack.extend((x, True) for x in n.args.defaults)
            elif t is ast.AnnAssign:
                if n.value is not None:
                    stack.append((n.value, True))
            elif t in _SKIP_T:
                continue
            else:
                stack.extend((c, True) for c in ast.iter_child_nodes(n))

    def _on_call(self, caller: str, scope: _Scope, call) -> None:
        st = self.stats
        st["calls"] += 1
        f = call.func
        r = self._expr(scope, f)
        line = call.lineno
        if r is None:
            if type(f) is ast.Attribute:
                st["unknown_attr"] += 1
                if self.guess:
                    self._guess(caller, f.attr, line)
            else:
                st["unknown_name"] += 1
            return
        k, tgt, lvl = r
        if k == "ext":
            st["external"] += 1
            return
        st["resolved"] += 1
        if k == "fn":
            self._edge(caller, tgt, "calls", line, lvl)
        elif k == "cls":
            self._edge(caller, tgt, "instantiates", line, lvl)
            hit = self._member(tgt, "__init__")
            if hit is not None:
                self._edge(caller, hit[0], "calls", line, 2)
        elif k == "inst":
            hit = self._member(tgt, "__call__")
            if hit is not None:
                self._edge(caller, hit[0], "calls", line, 2)

    def _on_ref(self, caller: str, scope: _Scope, e) -> None:
        r = self._expr(scope, e)
        if r is not None and r[0] in ("fn", "cls") and r[1] != caller:
            self._edge(caller, r[1], "references", e.lineno, r[2])

    def _on_decorators(self, scope: _Scope, d) -> None:
        nid = self._ast_ids.get(id(d))
        if nid is None:
            return
        for dec in d.decorator_list:
            r = self._expr(scope, dec.func if type(dec) is ast.Call else dec)
            if r is not None and r[0] in ("fn", "cls"):
                self._edge(r[1], nid, "decorates", dec.lineno, r[2])

    def _guess(self, caller: str, attr: str, line: int) -> None:
        if attr.startswith("__") and attr.endswith("__"):
            return
        if self._by_method is None:
            self._by_method = {}
            for n in self.nodes.values():
                if n.kind == "method":
                    self._by_method.setdefault(n.name, []).append(n.id)
        cands = self._by_method.get(attr, ())
        if len(cands) == 1:
            self._edge(caller, cands[0], "calls", line, 3, "medium")
        elif len(cands) <= self.guess_cap:
            for c in cands:
                self._edge(caller, c, "calls", line, 3, "low")
        else:
            self.stats["guess_too_many"] += 1

    def _edge(self, src, tgt, etype, line, level, conf="high") -> None:
        key = (src, tgt, etype)
        e = self.edges.get(key)
        if e is None:
            self.edges[key] = [level, conf, [line]]
            return
        e[2].append(line)
        if level < e[0]:
            e[0], e[1] = level, conf

    # ── queries ──

    def _adj(self):
        if self._out is None:
            self._out, self._in = {}, {}
            for (s, t, ty), e in self.edges.items():
                if ty in _LINK_TYPES:
                    self._out.setdefault(s, []).append((t, ty, e))
                    self._in.setdefault(t, []).append((s, ty, e))
        return self._out, self._in

    def find(self, query: str) -> list[str]:
        """Node ids matching an id, a dotted suffix, or `file.py:LINE`."""
        if query in self.nodes:
            return [query]
        m = re.match(r"^(.+\.py):(\d+)$", query)
        if m:
            hit = self.node_at(m.group(1), int(m.group(2)))
            return [hit] if hit else []
        suffix = "." + query
        return sorted(n for n in self.nodes if n.endswith(suffix))

    def node_at(self, file: str, line: int) -> str | None:
        """Innermost node in the repo file best matching *file* that spans *line*."""
        rel = self.match_file(file)
        if rel is None:
            return None
        best = None
        for n in self.nodes.values():
            if n.mod.rel == rel and n.line <= line <= n.end:
                if best is None or n.line >= best.line:
                    best = n
        return best.id if best else None

    def match_file(self, path: str) -> str | None:
        """Repo-relative path for a (possibly foreign, absolute) file path."""
        parts = Path(path.replace("\\", "/")).parts
        if not parts:
            return None
        lib = any(x in parts for x in _LIB_MARKERS)
        best, best_len, tie = None, 0, False
        for m in {id(m): m for m in self.mods.values()}.values():
            rp = Path(m.rel).parts
            if rp[-1] != parts[-1]:
                continue
            k = 0
            while k < min(len(rp), len(parts)) and rp[-1 - k] == parts[-1 - k]:
                k += 1
            need = 1 if (len(rp) == 1 and not lib) else min(2, len(rp))
            if k < need:
                continue
            if k > best_len:
                best, best_len, tie = m.rel, k, False
            elif k == best_len:
                tie = True
        return None if tie else best

    def walk(self, start: str, direction: str, depth: int) -> dict[str, int]:
        """BFS distance map from *start* over callers ("in") or callees ("out")."""
        out, inn = self._adj()
        adj = inn if direction == "in" else out
        dist = {start: 0}
        q = deque([start])
        while q:
            cur = q.popleft()
            d = dist[cur]
            if d >= depth:
                continue
            for nxt, _ty, _e in adj.get(cur, ()):
                if nxt not in dist:
                    dist[nxt] = d + 1
                    q.append(nxt)
        return dist

    def pagerank(self, alpha: float = 0.85, iters: int = 60) -> dict[str, float]:
        if self._pr is not None:
            return self._pr
        ids = list(self.nodes)
        ix = {n: i for i, n in enumerate(ids)}
        n_nodes = len(ids) or 1
        out = [set() for _ in ids]
        for s, t, ty in self.edges:
            if ty in _LINK_TYPES and s != t:
                out[ix[s]].add(ix[t])
        out_l = [list(o) for o in out]
        pr = [1.0 / n_nodes] * n_nodes
        base = (1.0 - alpha) / n_nodes
        for _ in range(iters):
            dangling = alpha * sum(p for p, o in zip(pr, out_l) if not o) / n_nodes
            nxt = [base + dangling] * n_nodes
            for i, o in enumerate(out_l):
                if o:
                    share = alpha * pr[i] / len(o)
                    for j in o:
                        nxt[j] += share
            delta = sum(abs(a - b) for a, b in zip(nxt, pr))
            pr = nxt
            if delta < 1e-10:
                break
        self._pr = dict(zip(ids, pr))
        return self._pr

    # ── output ──

    def node_json(self, nid: str, text: bool = True) -> dict:
        n = self.nodes[nid]
        d = {
            "id": nid, "name": n.name, "kind": n.kind,
            "file": n.mod.rel, "line": n.line, "end_line": n.end,
        }  # fmt: skip
        if text:
            d["text"] = "\n".join(n.mod.lines()[n.line - 1 : n.end])
        return d

    def edge_json(self, key) -> dict:
        s, t, ty = key
        level, conf, lines = self.edges[key]
        return {
            "source": s, "target": t, "type": ty, "key": 0,
            "line": lines[0], "lines": lines, "level": f"L{level}", "confidence": conf,
        }  # fmt: skip

    def to_json(self, only: set[str] | None = None, text: bool = True) -> dict:
        ids = [n for n in self.nodes if only is None or n in only]
        keep = set(ids)
        edges = [self.edge_json(k) for k in self.edges if k[0] in keep and k[1] in keep]
        return {
            "directed": True,
            "multigraph": True,
            "graph": {"root": str(self.root), "stats": self.summary()},
            "nodes": [self.node_json(n, text) for n in ids],
            "edges": edges,
        }

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for n in self.nodes.values():
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        etypes: dict[str, dict[str, int]] = {}
        for (_s, _t, ty), e in self.edges.items():
            per = etypes.setdefault(ty, {})
            per[f"L{e[0]}"] = per.get(f"L{e[0]}", 0) + 1
        return {
            "files": len({id(m) for m in self.mods.values()}),
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "node_kinds": kinds,
            "edge_types": etypes,
            "calls": dict(self.stats),
            "skipped": [{"file": f, "reason": r} for f, r in self.skipped],
        }


def _is_self_attr(tg, self_name: str | None) -> bool:
    return (
        type(tg) is ast.Attribute
        and type(tg.value) is ast.Name
        and tg.value.id == self_name
    )


def _is_none(e) -> bool:
    return type(e) is ast.Constant and e.value is None


# ── localization ────────────────────────────────────────────────────────────


def tokens(text: str, stop: frozenset[str]) -> set[str]:
    """Lower-cased identifier pieces (snake and camel split), minus filler."""
    out = set()
    for w in _WORD_RE.findall(text):
        for piece in w.split("_"):
            for t in _CAMEL_RE.findall(piece):
                t = t.lower()
                if len(t) >= 3 and t not in stop:
                    out.add(t)
    return out


def parse_traceback(text: str) -> list[tuple[str, int, str]]:
    """(file, line, func) frames of the last traceback in *text*, outermost first.

    Reads Python tracebacks (`File "x.py", line N, in f`) and, failing that,
    pytest's own report format (`x.py:N: in f` / `x.py:N: Error`), where the
    function name may be absent — frames then map by line alone.
    """
    blocks = text.split("Traceback (most recent call last):")
    for block in reversed(blocks[1:] if len(blocks) > 1 else blocks):
        frames = []
        for ln in block.splitlines():
            m = _FRAME_RE.match(ln)
            if m:
                frames.append((m.group("file"), int(m.group("line")), m.group("func")))
        if frames:
            return frames
    for section in reversed(_PYTEST_SECTION_RE.split(text)):
        frames = []
        for ln in section.splitlines():
            m = _PYTEST_FRAME_RE.match(ln)
            if m:
                frames.append(
                    (m.group("file"), int(m.group("line")), m.group("func") or "")
                )
        if frames:
            return frames
    return []


def issue_seeds(g: CallGraph, issue: str, stop: frozenset[str], cap: int) -> list[str]:
    """Starting points named by the issue text alone.

    Exact identifier mentions first; then non-test functions whose name
    pieces the issue shares (`base_currency` for "home currency"), best
    overlap first, PageRank breaking ties, capped at *cap*.
    """
    words = {
        w for w in _WORD_RE.findall(issue) if len(w) >= 4 and w.lower() not in stop
    }
    exact = [n.id for n in g.nodes.values() if n.kind != "module" and n.name in words]
    issue_toks = tokens(issue, stop)
    pr = g.pagerank()
    fuzzy = []
    for n in g.nodes.values():
        if n.kind == "module" or n.id in exact or is_test(Path(n.mod.rel)):
            continue
        toks = tokens(n.name, stop)
        shared = len(toks & issue_toks)
        if shared and shared / len(toks) >= 0.5:
            fuzzy.append((-shared / len(toks), -shared, -pr.get(n.id, 0.0), n.id))
    fuzzy.sort()
    return (exact + [f[-1] for f in fuzzy])[: max(cap, len(exact))]


def _frame_node(g: CallGraph, file: str, line: int, func: str) -> str | None:
    rel = g.match_file(file)
    if rel is None:
        return None
    hit = g.node_at(rel, line)
    if hit is not None and (func == "<module>" or g.nodes[hit].name == func):
        return hit
    named = [n.id for n in g.nodes.values() if n.mod.rel == rel and n.name == func]
    return named[0] if len(named) == 1 else hit  # line drift: trust a unique name


def _def_ast(g: CallGraph, nid: str):
    """The FunctionDef node for a function/method node (cached parse)."""
    n = g.nodes[nid]
    tree, _err = parse_text(g.root / n.mod.rel, n.mod.text)
    for d in ast.walk(tree) if tree is not None else ():
        if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.name == n.name:
            start = min([d.lineno] + [x.lineno for x in d.decorator_list])
            if start == n.line:
                return d
    return None


def _own_nodes(fn):
    """Nodes of one function body, not descending into nested defs/lambdas."""
    stack = list(fn.body)
    while stack:
        x = stack.pop()
        yield x
        if not isinstance(x, _DEF_T) and type(x) is not ast.Lambda:
            stack.extend(ast.iter_child_nodes(x))


def feeders(g: CallGraph, nid: str, line: int, hops: int = 3) -> dict[str, int]:
    """Callees whose results flow into *line* of function *nid*.

    Hop 0: calls made on the line itself. Hop k: calls in the latest
    assignment (at or before the line) to a name read by a hop k-1 line —
    the functions that produced the values the crashing line consumed.
    """
    fn = _def_ast(g, nid) if g.nodes[nid].kind in ("function", "method") else None
    if fn is None:
        return {}
    loads: dict[int, set[str]] = {}
    binds: dict[str, list[tuple[int, int]]] = {}
    for x in _own_nodes(fn):
        t = type(x)
        if t is ast.Name and type(x.ctx) is ast.Load:
            loads.setdefault(x.lineno, set()).add(x.id)
        elif t in (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor):
            tgts = x.targets if t is ast.Assign else [x.target]
            names: list[str] = []
            for tg in tgts:
                _target_names(tg, names)
            end = (
                x.lineno if t in (ast.For, ast.AsyncFor) else (x.end_lineno or x.lineno)
            )
            for nm in names:
                binds.setdefault(nm, []).append((x.lineno, end))
    hop_of: dict[int, int] = {line: 0}
    frontier = {line}
    for hop in range(1, hops + 1):
        nxt: set[int] = set()
        names = set().union(*(loads.get(ln, set()) for ln in frontier))
        for nm in names:
            spans = [sp for sp in binds.get(nm, ()) if sp[0] <= line]
            if not spans:
                continue
            a, b = max(spans)
            for ln in range(a, b + 1):
                if ln not in hop_of:
                    hop_of[ln] = hop
                    nxt.add(ln)
        if not nxt:
            break
        frontier = nxt
    out: dict[str, int] = {}
    for (s, t, ty), e in g.edges.items():
        if s == nid and ty in ("calls", "instantiates") and t != nid:
            hops_hit = [hop_of[ln] for ln in e[2] if ln in hop_of]
            if hops_hit:
                out[t] = min(hops_hit)
    return out


def localize(
    g: CallGraph,
    cfg: dict,
    frames: list[tuple[str, int, str]] | None = None,
    symbols: list[str] | None = None,
    issue: str = "",
) -> dict:
    """Rank suspect functions from a traceback and/or seed symbols."""
    depth, top = int(cfg["depth"]), int(cfg["top"])
    stop = frozenset(cfg["stopwords"])
    seeds: dict[str, int] = {}
    on_stack: set[str] = set()
    mapped = []
    for i, (f, ln, fn) in enumerate(reversed(frames or [])):
        nid = _frame_node(g, f, ln, fn)
        mapped.append({"file": f, "line": ln, "func": fn, "node": nid})
        if nid is not None and g.nodes[nid].kind != "module":
            on_stack.add(nid)
            seeds.setdefault(nid, len(on_stack) - 1)
    mapped.reverse()
    sym_seeds = []
    for s in symbols or []:
        sym_seeds.extend(g.find(s))
    if not seeds and not sym_seeds and issue:
        sym_seeds = issue_seeds(g, issue, stop, int(cfg["issue_seeds"]))
    for s in sym_seeds:
        seeds[s] = 0
    # The crash site's inputs: callees whose results reach the crashing line.
    feed: dict[str, int] = {}
    crash = next((f for f in reversed(mapped) if f["node"] in on_stack), None)
    if crash is not None:
        feed = feeders(g, crash["node"], crash["line"], int(cfg["feed_hops"]))
    # Multi-source BFS both ways from every seed: callers, and callees — a
    # frame that received a bad value (or a test asserting on one) points at
    # code it called, which has already returned and so is not on the stack.
    dist = dict(seeds)
    for f in feed:
        dist[f] = min(dist.get(f, 1), 1)
    for s, d0 in seeds.items():
        for direction in ("in", "out"):
            for n, d in g.walk(s, direction, depth).items():
                if d + d0 < dist.get(n, 1 << 30):
                    dist[n] = d + d0
    issue_toks = tokens(issue, stop) if issue else set()
    pr = g.pagerank()
    pr_max = max(pr.values(), default=0.0) or 1.0
    scored = []
    for nid, d in dist.items():
        n = g.nodes[nid]
        if n.kind == "module":
            continue
        name_toks = tokens(nid.split(".", 1)[-1] if "." in nid else nid, stop)
        overlap = (
            len(name_toks & issue_toks) / len(name_toks)
            if name_toks and issue_toks
            else 0.0
        )
        score = (
            cfg["w_dist"] / (1 + d)
            + cfg["w_stack"] * (nid in on_stack)
            + cfg["w_rank"] * pr.get(nid, 0.0) / pr_max
            + cfg["w_name"] * overlap
            + (cfg["w_feed"] / (1 + feed[nid]) if nid in feed else 0.0)
            - cfg["test_penalty"] * (is_test(Path(n.mod.rel)) and nid not in on_stack)
        )
        scored.append((score, nid, d, overlap))
    scored.sort(key=lambda x: (-x[0], x[2], x[1]))
    return {
        "frames": mapped,
        "seeds": sorted(seeds),
        "candidates": len(scored),
        "top": [
            {
                "rank": i + 1, "id": nid, "score": round(sc, 4),
                "file": g.nodes[nid].mod.rel, "line": g.nodes[nid].line,
                "dist": d, "on_stack": nid in on_stack, "name_overlap": round(ov, 3),
                "feeds_crash": feed.get(nid),
            }  # fmt: skip
            for i, (sc, nid, d, ov) in enumerate(scored[:top])
        ],
    }


# ── CLI ─────────────────────────────────────────────────────────────────────


def _tree(g: CallGraph, start: str, direction: str, depth: int) -> str:
    out, inn = g._adj()
    adj = inn if direction == "in" else out
    arrow = "←" if direction == "in" else "→"
    lines = [f"► {start}  ({g.nodes[start].mod.rel}:{g.nodes[start].line})"]
    seen = {start}

    def rec(nid: str, d: int) -> None:
        if d > depth:
            return
        for nxt, ty, e in sorted(adj.get(nid, ()), key=lambda x: x[0]):
            src = nxt if direction == "in" else nid
            where = f"{g.nodes[src].mod.rel}:{e[2][0]}"
            tag = f"L{e[0]} {ty} @ {where}" + ("" if e[1] == "high" else f" ({e[1]})")
            again = nxt in seen
            lines.append(
                f"{'    ' * (d - 1)}{arrow} {nxt}  [{tag}]"
                + ("  (seen)" if again else "")
            )
            if not again:
                seen.add(nxt)
                rec(nxt, d + 1)

    rec(start, 1)
    return "\n".join(lines)


def _summary_text(g: CallGraph, top_rank: int = 0) -> str:
    s = g.summary()
    c = s["calls"]
    kinds = " · ".join(f"{k} {v}" for k, v in sorted(s["node_kinds"].items()))
    lines = [
        f"callgraph: {s['files']} files, {s['nodes']} nodes, {s['edges']} edges"
        + (f" ({len(s['skipped'])} skipped)" if s["skipped"] else ""),
        f"  nodes: {kinds}",
    ]
    for ty, per in sorted(s["edge_types"].items()):
        lv = ", ".join(f"{k} {v}" for k, v in sorted(per.items()))
        lines.append(f"  {ty:<13}{sum(per.values()):>6}  ({lv})")
    lines.append(
        f"  call sites: {c['calls']} → resolved {c['resolved']}, external {c['external']}, "
        f"unknown obj.m() {c['unknown_attr']}, unknown name {c['unknown_name']}"
    )
    for sk in s["skipped"]:
        lines.append(f"  skipped {sk['file']}: {sk['reason']}")
    if top_rank:
        lines.append("  top by PageRank:")
        pr = g.pagerank()
        for nid in sorted(pr, key=lambda k: -pr[k])[:top_rank]:
            lines.append(f"    {pr[nid]:.5f}  {nid}")
    return "\n".join(lines)


def _pick(g: CallGraph, query: str) -> tuple[str | None, str]:
    hits = g.find(query)
    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        return None, f"callgraph: no node matches {query!r}"
    return None, f"callgraph: {query!r} is ambiguous: " + ", ".join(hits[:12])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="audit-test callgraph",
        description="Cross-file, function-level call graph (L1 imports, L2 classes, L3 guesses).",
    )
    ap.add_argument("--path", "-p", default=".", help="project root (default: cwd)")
    q = ap.add_mutually_exclusive_group()
    q.add_argument("--callers", metavar="X", help="walk backward to what calls X")
    q.add_argument("--callees", metavar="X", help="walk forward to what X calls")
    q.add_argument(
        "--subgraph", metavar="A,B,C", help="these nodes and the edges between them"
    )
    q.add_argument("--rank", action="store_true", help="top nodes by PageRank")
    q.add_argument(
        "--from-traceback", metavar="FILE", help="rank suspects from a traceback"
    )
    q.add_argument(
        "--from-symbols", metavar="A,B,C", help="rank suspects from named symbols"
    )
    ap.add_argument(
        "--issue", metavar="FILE", help="issue text: name-match ranking signal"
    )
    ap.add_argument("--depth", type=int, help="walk depth (default from config: 3)")
    ap.add_argument(
        "--top", type=int, help="how many results (default from config: 10)"
    )
    ap.add_argument(
        "--guess", action="store_true", help="add L3 edges for obj.m() calls"
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--no-text", action="store_true", help="omit node source text in --json"
    )
    return ap


def _read(path: str, enc: str) -> str:
    return Path(path).read_text(encoding=enc, errors="replace")


def main(argv: list[str] | None = None) -> int:
    """`audit-test callgraph ...` — returns a process exit code."""
    force_utf8_streams()
    a = build_parser().parse_args(argv)
    root = Path(a.path).resolve()
    if not root.is_dir():
        print(f"callgraph: not a directory: {root}", file=sys.stderr)
        return 2
    cfg = load_config(root)
    if a.depth is not None:
        cfg["depth"] = a.depth
    if a.top is not None:
        cfg["top"] = a.top
    g = CallGraph(root, guess=a.guess, guess_cap=int(cfg["guess_cap"])).build()
    enc = configured_encoding(root)
    depth = int(cfg["depth"])
    try:
        issue = _read(a.issue, enc) if a.issue else ""
    except OSError as e:
        print(f"callgraph: {e}", file=sys.stderr)
        return 2

    if a.callers or a.callees:
        nid, err = _pick(g, a.callers or a.callees)
        if nid is None:
            print(err, file=sys.stderr)
            return 2
        direction = "in" if a.callers else "out"
        if a.json:
            dist = g.walk(nid, direction, depth)
            data = g.to_json(set(dist), text=not a.no_text)
            for n in data["nodes"]:
                n["dist"] = dist[n["id"]]
            data["graph"] = {
                "root": nid,
                "direction": "callers" if a.callers else "callees",
            }
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(_tree(g, nid, direction, depth))
        return 0

    if a.subgraph:
        ids, errs = set(), []
        for q in (x.strip() for x in a.subgraph.split(",") if x.strip()):
            nid, err = _pick(g, q)
            if nid is None:
                errs.append(err)
            else:
                ids.add(nid)
        for err in errs:
            print(err, file=sys.stderr)
        data = g.to_json(ids, text=not a.no_text)
        if a.json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            for e in data["edges"]:
                print(
                    f"{e['source']} -[{e['level']} {e['type']}]-> {e['target']}  (line {e['line']})"
                )
        return 2 if errs else 0

    if a.from_traceback or a.from_symbols or (a.issue and not a.rank):
        try:
            frames = (
                parse_traceback(_read(a.from_traceback, enc))
                if a.from_traceback
                else None
            )
        except OSError as e:
            print(f"callgraph: {e}", file=sys.stderr)
            return 2
        syms = [x.strip() for x in (a.from_symbols or "").split(",") if x.strip()]
        res = localize(g, cfg, frames=frames, symbols=syms, issue=issue)
        if a.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        mapped = sum(1 for f in res["frames"] if f["node"])
        if frames is not None:
            print(f"traceback: {len(res['frames'])} frames, {mapped} in this repo")
        print(
            f"seeds: {', '.join(res['seeds']) or '(none)'}  ·  {res['candidates']} candidates"
        )
        for c in res["top"]:
            why = f"d={c['dist']}" + (" stack" if c["on_stack"] else "")
            why += f" name={c['name_overlap']}" if c["name_overlap"] else ""
            why += (
                f" feeds-crash(hop {c['feeds_crash']})"
                if c["feeds_crash"] is not None
                else ""
            )
            print(
                f"{c['rank']:>3}  {c['score']:.3f}  {c['id']}  ({c['file']}:{c['line']})  {why}"
            )
        return 0 if res["seeds"] else 2

    if a.json:
        if a.rank:
            pr = g.pagerank()
            top = sorted(pr, key=lambda k: -pr[k])[: int(cfg["top"])]
            print(
                json.dumps(
                    [{"id": n, "pagerank": round(pr[n], 6)} for n in top], indent=2
                )
            )
        else:
            print(json.dumps(g.to_json(text=not a.no_text), ensure_ascii=False))
        return 0
    print(_summary_text(g, int(cfg["top"]) if a.rank else 0))
    return 0


def exit_fast(code: int) -> None:
    """End a CLI process without tearing down the parsed ASTs.

    A build leaves millions of AST nodes alive in the shared parse cache;
    freeing them one by one at interpreter exit costs about as long as the
    build itself. Flush, then let the OS reclaim the memory (as mypy does).
    Only for process entry points — never call this from in-process code.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    exit_fast(main())
