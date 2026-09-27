"""In-process tests for callgraph — cross-file function call graph. T1 anchor."""

import json
import tempfile
from pathlib import Path

from audit_code.callgraph import (
    CallGraph,
    localize,
    load_config,
    main,
    parse_traceback,
    tokens,
)

FILES = {
    "pkg/__init__.py": "from .core import Engine\nfrom .util import helper as public_helper\n",
    "pkg/util.py": (
        "def helper():\n    return 1\n\n"
        "def register(fn):\n    return fn\n\n"
        "def other():\n    helper()\n"
    ),
    "pkg/base.py": (
        "class Base:\n"
        "    def __init__(self):\n        self.setup()\n\n"
        "    def setup(self):\n        pass\n\n"
        "    def run(self):\n        return self.step()\n\n"
        "    def step(self):\n        return 0\n"
    ),
    "pkg/core.py": (
        "from . import util\n"
        "from .base import Base\n"
        "from .util import register\n\n"
        "class Engine(Base):\n"
        "    def __init__(self):\n        super().__init__()\n        self.part = Part()\n\n"
        "    def step(self):\n        util.helper()\n        return self.part.spin()\n\n"
        "    @classmethod\n    def make(cls):\n        return cls()\n\n"
        "class Part:\n    def spin(self):\n        return 2\n\n"
        "@register\n"
        "def handler():\n    pass\n\n"
        "def build() -> Engine:\n    return Engine()\n\n"
        "def use():\n    e = build()\n    e.run()\n    callbacks = [handler]\n    return callbacks\n"
    ),
    "app.py": (
        "import pkg\n"
        "from pkg import public_helper\n"
        "from pkg.core import Engine as E\n\n"
        "def main():\n    public_helper()\n    eng = E()\n    eng.step()\n    return pkg.Engine.make()\n\n"
        "def untyped(obj):\n    return obj.spin()\n"
    ),
    "broken.py": "def oops(:\n",
}


def _repo(d: str) -> Path:
    root = Path(d)
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _edges(g: CallGraph, etype: str = "calls") -> dict[tuple[str, str], int]:
    return {(s, t): e[0] for (s, t, ty), e in g.edges.items() if ty == etype}


def test_l1_imports_aliases_relative_and_reexports():
    with tempfile.TemporaryDirectory() as d:
        g = CallGraph(_repo(d)).build()
        calls = _edges(g)
        # re-export through __init__ with an alias
        assert calls[("app.main", "pkg.util.helper")] == 1
        # `from . import util; util.helper()`
        assert calls[("pkg.core.Engine.step", "pkg.util.helper")] == 1
        # plain same-module call
        assert calls[("pkg.util.other", "pkg.util.helper")] == 1
        assert ("app", "pkg.core") in _edges(g, "imports")


def test_l2_class_system():
    with tempfile.TemporaryDirectory() as d:
        g = CallGraph(_repo(d)).build()
        calls = _edges(g)
        assert (
            calls[("pkg.core.Engine.__init__", "pkg.base.Base.__init__")] == 2
        )  # super()
        assert calls[("pkg.base.Base.__init__", "pkg.base.Base.setup")] == 2  # self.m()
        assert (
            calls[("pkg.core.Engine.step", "pkg.core.Part.spin")] == 2
        )  # self.attr = X()
        assert calls[("pkg.core.Engine.make", "pkg.core.Engine.__init__")] == 2  # cls()
        # instance from an alias import, method looked up through the class
        assert calls[("app.main", "pkg.core.Engine.step")] == 2
        # return annotation types `e`; run() is inherited from Base
        assert calls[("pkg.core.use", "pkg.base.Base.run")] == 2
        assert ("pkg.core.Engine", "pkg.base.Base") in _edges(g, "inherits")
        assert ("app.main", "pkg.core.Engine") in _edges(g, "instantiates")
        assert ("pkg.util.register", "pkg.core.handler") in _edges(g, "decorates")
        assert ("pkg.core.use", "pkg.core.handler") in _edges(g, "references")


def test_l3_guess_is_opt_in():
    with tempfile.TemporaryDirectory() as d:
        root = _repo(d)
        assert ("app.untyped", "pkg.core.Part.spin") not in _edges(
            CallGraph(root).build()
        )
        g = CallGraph(root, guess=True).build()
        key = ("app.untyped", "pkg.core.Part.spin", "calls")
        assert g.edges[key][0] == 3 and g.edges[key][1] == "medium"


def test_bad_file_skipped_not_fatal():
    with tempfile.TemporaryDirectory() as d:
        g = CallGraph(_repo(d)).build()
        assert [f for f, _ in g.skipped] == ["broken.py"]
        assert "pkg.core.Engine" in g.nodes


def test_json_schema_and_line_ranges():
    with tempfile.TemporaryDirectory() as d:
        data = CallGraph(_repo(d)).build().to_json()
        node = next(n for n in data["nodes"] if n["id"] == "pkg.core.handler")
        assert node["file"] == "pkg/core.py" and node["text"].startswith("@register")
        edge = data["edges"][0]
        assert {
            "source",
            "target",
            "type",
            "key",
            "line",
            "level",
            "confidence",
        } <= set(edge)


def test_queries_and_pagerank():
    with tempfile.TemporaryDirectory() as d:
        g = CallGraph(_repo(d)).build()
        assert g.find("Engine.step") == ["pkg.core.Engine.step"]
        assert g.find("pkg/core.py:12") == ["pkg.core.Engine.step"]
        callers = g.walk("pkg.util.helper", "in", 2)
        assert {"app.main", "pkg.core.Engine.step", "pkg.util.other"} <= set(callers)
        pr = g.pagerank()
        assert abs(sum(pr.values()) - 1.0) < 1e-6


def test_traceback_localization():
    tb = (
        "Traceback (most recent call last):\n"
        '  File "/somewhere/else/app.py", line 7, in main\n'
        '  File "/somewhere/else/pkg/core.py", line 12, in step\n'
        '  File "/usr/lib/python3.13/site-packages/lib/x.py", line 1, in f\n'
        "ValueError: spin broke\n"
    )
    frames = parse_traceback(tb)
    assert [f[2] for f in frames] == ["main", "step", "f"]
    with tempfile.TemporaryDirectory() as d:
        root = _repo(d)
        g = CallGraph(root).build()
        res = localize(
            g, load_config(root), frames=frames, issue="the spin value broke"
        )
        ids = [c["id"] for c in res["top"]]
        assert ids[0] == "pkg.core.Engine.step"
        assert "app.main" in ids
        assert res["frames"][2]["node"] is None  # library frame


def test_symbol_localization_and_tokens():
    assert tokens("getUserName parse_http_header", frozenset()) == {
        "user", "name", "parse", "http", "header", "get",
    }  # fmt: skip
    with tempfile.TemporaryDirectory() as d:
        root = _repo(d)
        g = CallGraph(root).build()
        res = localize(g, load_config(root), symbols=["Part.spin"])
        assert res["top"][0]["id"] == "pkg.core.Part.spin"


def test_cli_json(capsys):
    with tempfile.TemporaryDirectory() as d:
        root = _repo(d)
        assert (
            main(["--path", str(root), "--callers", "helper", "--json", "--no-text"])
            == 0
        )
        data = json.loads(capsys.readouterr().out)
        assert {"app.main", "pkg.util.other"} <= {n["id"] for n in data["nodes"]}
        assert main(["--path", str(root), "--callers", "nope"]) == 2
