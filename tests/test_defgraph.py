"""In-process tests for defgraph module — intra-file call graph. T1 anchor."""

import json
import tempfile
from pathlib import Path

from audit_code.defgraph import build_def_graph, run
from audit_code.models import AuditStatus


def _write(root: Path, name: str, text: str) -> Path:
    p = root / name
    p.write_text(text, encoding="utf-8")
    return p


def test_python_def_graph():
    with tempfile.TemporaryDirectory() as d:
        p = _write(
            Path(d),
            "m.py",
            "def helper():\n    pass\n\n"
            "def mid():\n    helper()\n\n"
            "def main():\n    mid()\n    obj.helper()\n",
        )
        g = build_def_graph(p)
        assert g == {"helper": set(), "mid": {"helper"}, "main": {"mid", "helper"}}


def test_python_nested_def_attribution():
    with tempfile.TemporaryDirectory() as d:
        p = _write(
            Path(d),
            "m.py",
            "def leaf():\n    pass\n\n"
            "def outer():\n"
            "    def inner():\n"
            "        leaf()\n"
            "    inner()\n",
        )
        g = build_def_graph(p)
        # leaf() is called by inner, not outer
        assert g["outer"] == {"inner"}
        assert g["inner"] == {"leaf"}


def test_python_ignores_external_calls():
    with tempfile.TemporaryDirectory() as d:
        p = _write(Path(d), "m.py", "def main():\n    print('hi')\n    len([])\n")
        g = build_def_graph(p)
        assert g == {"main": set()}


def test_js_def_graph():
    with tempfile.TemporaryDirectory() as d:
        p = _write(
            Path(d),
            "m.js",
            "function helper() {}\n" "function main() { helper(); console.log(1); }\n",
        )
        g = build_def_graph(p)
        if g is None:  # grammar not installed
            return
        assert g["main"] == {"helper"}


def test_rust_def_graph():
    with tempfile.TemporaryDirectory() as d:
        p = _write(
            Path(d),
            "m.rs",
            "fn helper() {}\n" 'fn main() { helper(); println!("x"); }\n',
        )
        g = build_def_graph(p)
        if g is None:
            return
        assert g["main"] == {"helper"}


def test_go_def_graph():
    with tempfile.TemporaryDirectory() as d:
        p = _write(
            Path(d),
            "m.go",
            "package main\n" "func helper() {}\n" "func main() { helper() }\n",
        )
        g = build_def_graph(p)
        if g is None:
            return
        assert g["main"] == {"helper"}


# One snippet per language, each defining helper + main where main calls helper.
_LANG_SNIPPETS = {
    "m.py": "def helper():\n    pass\n\ndef main():\n    helper()\n",
    "m.js": "function helper() {}\nfunction main() { helper(); }\n",
    "m.ts": "function helper(): void {}\nfunction main(): void { helper(); }\n",
    "m.rs": "fn helper() {}\nfn main() { helper(); }\n",
    "m.go": "package main\nfunc helper() {}\nfunc main() { helper() }\n",
    "m.java": "class M { void helper() {} void main() { helper(); } }\n",
    "m.kt": "fun helper() {}\nfun main() { helper() }\n",
    "m.swift": "func helper() {}\nfunc main() { helper() }\n",
    "m.php": "<?php\nfunction helper() {}\nfunction main() { helper(); }\n",
    "m.cs": "class M { void helper() {} void main() { helper(); } }\n",
    "m.c": "void helper() {}\nvoid main() { helper(); }\n",
}


def test_all_ten_languages_main_calls_helper():
    for fname, snippet in _LANG_SNIPPETS.items():
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), fname, snippet)
            g = build_def_graph(p)
            assert g is not None, f"{fname}: no graph (grammar missing?)"
            assert g.get("main") == {"helper"}, f"{fname}: {g}"
            assert g.get("helper") == set(), f"{fname}: {g}"


# Multi-hop chains: a → b → c → d, one per language family.
_CHAIN_SNIPPETS = {
    "m.py": (
        "def d():\n    pass\n\ndef c():\n    d()\n\n"
        "def b():\n    c()\n\ndef a():\n    b()\n"
    ),
    "m.js": (
        "function d() {}\nfunction c() { d(); }\n"
        "function b() { c(); }\nfunction a() { b(); }\n"
    ),
    "m.rs": ("fn d() {}\nfn c() { d(); }\nfn b() { c(); }\nfn a() { b(); }\n"),
    "m.go": (
        "package main\nfunc d() {}\nfunc c() { d() }\n"
        "func b() { c() }\nfunc a() { b() }\n"
    ),
    "m.java": (
        "class M { void d() {} void c() { d(); } "
        "void b() { c(); } void a() { b(); } }\n"
    ),
    "m.kt": "fun d() {}\nfun c() { d() }\nfun b() { c() }\nfun a() { b() }\n",
    "m.swift": "func d() {}\nfunc c() { d() }\nfunc b() { c() }\nfunc a() { b() }\n",
    "m.php": (
        "<?php\nfunction d() {}\nfunction c() { d(); }\n"
        "function b() { c(); }\nfunction a() { b(); }\n"
    ),
    "m.cs": (
        "class M { void d() {} void c() { d(); } "
        "void b() { c(); } void a() { b(); } }\n"
    ),
    "m.c": ("void d() {}\nvoid c() { d(); }\nvoid b() { c(); }\nvoid a() { b(); }\n"),
}


def test_multi_hop_chain_all_languages():
    from audit_code.graph import trace_downstream, trace_upstream

    for fname, snippet in _CHAIN_SNIPPETS.items():
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), fname, snippet)
            g = build_def_graph(p)
            assert g is not None, f"{fname}: no graph (grammar missing?)"
            assert g.get("a") == {"b"}, f"{fname}: {g}"
            assert g.get("b") == {"c"}, f"{fname}: {g}"
            assert g.get("c") == {"d"}, f"{fname}: {g}"
            # BFS respects depth: +1 from a stops at b, +3 reaches d
            down1 = trace_downstream(g, "a", 1)
            assert set(down1) == {"a", "b"}, f"{fname}: {down1}"
            down3 = trace_downstream(g, "a", 3)
            assert set(down3) == {"a", "b", "c", "d"}, f"{fname}: {down3}"
            # Upstream: -3 from d walks back to a
            up3 = trace_upstream(g, "d", 3)
            assert set(up3) == {"a", "b", "c", "d"}, f"{fname}: {up3}"


def test_multi_hop_run_depth_flags():
    with tempfile.TemporaryDirectory() as d:
        _write(Path(d), "m.py", _CHAIN_SNIPPETS["m.py"])
        result = run(Path(d), "m.py", "a", forward=1, back=1)
        assert result.status == AuditStatus.PASS
        assert "b" in result.stdout and "d" not in result.stdout.replace("def", "")
        result = run(Path(d), "m.py", "a", forward=3, back=1, json_out=True)
        data = json.loads(result.stdout)
        assert data["downstream"] == ["b", "c", "d"]


def test_unsupported_extension_returns_none():
    with tempfile.TemporaryDirectory() as d:
        p = _write(Path(d), "m.txt", "hello")
        assert build_def_graph(p) is None


def test_run_tree_output():
    with tempfile.TemporaryDirectory() as d:
        _write(
            Path(d),
            "m.py",
            "def helper():\n    pass\n\ndef main():\n    helper()\n",
        )
        result = run(Path(d), "m.py", "main")
        assert result.status == AuditStatus.PASS
        assert "► main" in result.stdout
        assert "helper" in result.stdout


def test_run_upstream_callers():
    with tempfile.TemporaryDirectory() as d:
        _write(
            Path(d),
            "m.py",
            "def helper():\n    pass\n\ndef main():\n    helper()\n",
        )
        result = run(Path(d), "m.py", "helper")
        assert result.status == AuditStatus.PASS
        assert "main" in result.stdout


def test_run_json_output():
    with tempfile.TemporaryDirectory() as d:
        _write(
            Path(d),
            "m.py",
            "def helper():\n    pass\n\ndef main():\n    helper()\n",
        )
        result = run(Path(d), "m.py", "main", json_out=True)
        assert result.status == AuditStatus.PASS
        data = json.loads(result.stdout)
        assert data["def"] == "main"
        assert "helper" in data["downstream"]
        assert {"from": "main", "to": "helper", "type": "call"} in data["edges"]


def test_run_fuzzy_def_match():
    with tempfile.TemporaryDirectory() as d:
        _write(Path(d), "m.py", "def run_all_checks():\n    pass\n")
        result = run(Path(d), "m.py", "all_checks")
        assert result.status == AuditStatus.PASS
        assert "run_all_checks" in result.stdout


def test_run_def_not_found_lists_defs():
    with tempfile.TemporaryDirectory() as d:
        _write(Path(d), "m.py", "def alpha():\n    pass\n\ndef beta():\n    pass\n")
        result = run(Path(d), "m.py", "nope")
        assert result.status == AuditStatus.ERROR
        assert "alpha" in result.stderr


def test_run_file_not_found():
    with tempfile.TemporaryDirectory() as d:
        result = run(Path(d), "ghost.py", "main")
        assert result.status == AuditStatus.ERROR
        assert "file not found" in result.stderr
