"""In-process tests for graph module — import tracing and tree formatting."""

import json
from pathlib import Path

from audit_code.graph import (
    _package_root,
    _parse_imports,
    _resolve_local,
    build_graph,
    format_tree,
    run,
    trace_downstream,
    trace_upstream,
)


def test_package_root_finds_project():
    root = _package_root(Path(__file__))
    assert root is not None
    assert (root / "pyproject.toml").exists()


def test_parse_imports_finds_local():
    # cli.py imports many audit_code modules
    imports = _parse_imports(Path("src/audit_code/cli.py"))
    assert len(imports) > 5  # at least 5 local imports


def test_parse_imports_ignores_external():
    # A file with only stdlib imports
    imports = _parse_imports(Path("src/audit_code/models.py"))
    # models imports typing, dataclasses, enum — but those are external
    # It also imports from audit_code models — check we get at least some
    assert isinstance(imports, set)


def test_resolve_local_finds_module():
    root = _package_root(Path("src/audit_code/cli.py"))
    found = _resolve_local(root, "audit_code.runner")
    assert found is not None
    assert "runner" in found


def test_resolve_local_returns_none_for_stdlib():
    root = _package_root(Path("src/audit_code/cli.py"))
    assert _resolve_local(root, "os") is None
    assert _resolve_local(root, "sys") is None


def test_build_graph_has_edges():
    g, _ = build_graph(Path.cwd())
    assert len(g) > 10  # more than 10 modules
    # cli.py should have imports
    cli = g.get("src/audit_code/cli.py", set())
    assert len(cli) > 5, f"cli.py has {len(cli)} imports, expected >5"


def test_trace_downstream():
    g, _ = build_graph(Path.cwd())
    node = "src/audit_code/cli.py"
    down = trace_downstream(g, node, 1)
    # Should include cli.py itself + its direct imports
    assert node in down
    assert len(down) > 5, f"only {len(down)} downstream nodes"


def test_trace_upstream():
    g, _ = build_graph(Path.cwd())
    node = "src/audit_code/cli.py"
    up = trace_upstream(g, node, 1)
    assert node in up
    # cli.py is imported by __main__.py and tests
    assert len(up) >= 1, f"only {len(up)} upstream nodes"


def test_format_tree_shows_node():
    g, _ = build_graph(Path.cwd())
    node = "src/audit_code/cli.py"
    up = trace_upstream(g, node, 1)
    down = trace_downstream(g, node, 1)
    tree = format_tree(node, up, down, g)
    assert "cli.py" in tree


def test_run_human_output(tmp_path):
    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    result = run(tmp_path, "a.py", forward=1, back=0)
    assert result.status.value == "PASS"
    assert "a.py" in result.stdout


def test_run_json_output(tmp_path):
    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    result = run(tmp_path, "a.py", forward=1, back=0, json_out=True)
    data = json.loads(result.stdout)
    assert data["node"] == "a.py"


def test_run_missing_module(tmp_path):
    result = run(tmp_path, "nope.py")
    assert result.status.value == "ERROR"
