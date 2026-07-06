"""Tests for the single-pass project profiler (profiler.py).

T1 anchor: "profiler" — referenced by test imports.
"""

import subprocess
import sys

import pytest

from audit_code import profiler


def _write(root, name, text):
    p = root / name
    p.write_text(text, encoding="utf-8")
    return p


# ── structure ────────────────────────────────────────────────────────────────


def test_profile_counts_structure(tmp_path):
    _write(
        tmp_path,
        "mod.py",
        "import os\n\n"
        "def public_one():\n"
        "    for _ in range(3):\n"
        "        pass\n\n"
        "def _private():\n"
        "    while True:\n"
        "        break\n\n"
        "class Widget:\n"
        "    def method(self):\n"
        "        return 1\n"
        "    def _hidden(self):\n"
        "        return 2\n",
    )
    prof = profiler.profile_project(tmp_path)
    s = prof["structure"]
    assert s["file_count"] == 1
    assert s["function_count"] == 4  # 2 module fns + 2 methods
    assert s["public_function_count"] == 2  # public_one + Widget.method
    assert s["class_count"] == 1
    assert s["loop_count"] == 2  # one for, one while
    assert s["classes"] == {"Widget": ["method"]}  # _hidden excluded
    assert "os" in s["imports"]
    assert prof["metrics"]["parse_seconds"] >= 0.0


def test_public_function_records_have_location(tmp_path):
    _write(tmp_path, "a.py", "def do_thing(x, y):\n    return x + y\n")
    prof = profiler.profile_project(tmp_path)
    fns = prof["structure"]["public_functions"]
    assert fns == [{"name": "do_thing", "file": "a.py", "line": 1, "args": 2}]


# ── single pass across multiple files ────────────────────────────────────────


def test_profile_aggregates_multiple_files(tmp_path):
    _write(tmp_path, "one.py", "def alpha():\n    pass\n")
    _write(tmp_path, "two.py", "def beta():\n    pass\n")
    prof = profiler.profile_project(tmp_path)
    assert prof["structure"]["file_count"] == 2
    names = {f["name"] for f in prof["structure"]["public_functions"]}
    assert names == {"alpha", "beta"}


# ── not hardcoded: capabilities are config-driven ────────────────────────────


def test_no_config_reports_no_capabilities(tmp_path):
    _write(tmp_path, "m.py", "concealer = 'blush'\ndef render_face():\n    pass\n")
    prof = profiler.profile_project(tmp_path)
    # No domain vocabulary is assumed without config.
    assert prof["capabilities"] == {}


def test_config_driven_capabilities(tmp_path):
    _write(
        tmp_path,
        "m.py",
        "LABEL = 'apply concealer then blush'\n" "def render_layer():\n" "    pass\n",
    )
    cfg = {
        "profile": {
            "capabilities": {"products": ["concealer", "blush", "eyeliner"]},
            "pipeline_verbs": ["render"],
        }
    }
    prof = profiler.profile_project(tmp_path, config=cfg)
    assert prof["capabilities"]["products"] == ["blush", "concealer"]
    assert prof["architecture"]["pipeline_stages"] == ["render_layer"]


def test_config_heavy_libs_override_compute_ops(tmp_path):
    _write(
        tmp_path,
        "m.py",
        "import mylib\n"
        "def go():\n"
        "    mylib.crunch()\n"
        "    mylib.grind()\n"
        "    other()\n",
    )
    base = profiler.profile_project(tmp_path)
    assert base["performance"]["compute_ops"] == 0  # mylib not a default heavy lib
    cfg = {"profile": {"heavy_libs": ["mylib"]}}
    tuned = profiler.profile_project(tmp_path, config=cfg)
    assert tuned["performance"]["compute_ops"] == 2


# ── UTF-8 / any file ─────────────────────────────────────────────────────────


def test_profile_handles_utf8(tmp_path):
    _write(
        tmp_path,
        "u.py",
        "# résumé 🐇 café\nMESSAGE = 'naïve façade'\ndef greet():\n    pass\n",
    )
    prof = profiler.profile_project(tmp_path)
    assert prof["structure"]["file_count"] == 1
    assert prof["structure"]["public_function_count"] == 1


def test_profile_survives_bad_bytes(tmp_path):
    # Latin-1 bytes that are not valid UTF-8 must not crash (errors=replace).
    p = tmp_path / "bad.py"
    p.write_bytes(b"X = 'caf\xe9'\ndef ok():\n    pass\n")
    prof = profiler.profile_project(tmp_path, encoding="utf-8")
    assert prof["structure"]["file_count"] == 1


def test_syntax_error_file_is_skipped_not_fatal(tmp_path):
    _write(tmp_path, "good.py", "def ok():\n    pass\n")
    _write(tmp_path, "broken.py", "def (:\n")
    prof = profiler.profile_project(tmp_path)
    assert prof["structure"]["file_count"] == 1
    assert prof["structure"]["files_skipped"] == 1


# ── audit-test rules: skip dirs are honoured ─────────────────────────────────


def test_skips_pycache_and_venv(tmp_path):
    _write(tmp_path, "real.py", "def real():\n    pass\n")
    (tmp_path / "__pycache__").mkdir()
    _write(tmp_path / "__pycache__", "cached.py", "def cached():\n    pass\n")
    (tmp_path / ".venv").mkdir()
    _write(tmp_path / ".venv", "dep.py", "def dep():\n    pass\n")
    prof = profiler.profile_project(tmp_path)
    names = {f["name"] for f in prof["structure"]["public_functions"]}
    assert names == {"real"}


# ── architecture heuristics ──────────────────────────────────────────────────


def test_monolith_detection(tmp_path):
    body = "\n".join(f"    x{i} = {i}" for i in range(200))
    _write(tmp_path, "big.py", f"def process():\n{body}\n")
    prof = profiler.profile_project(tmp_path)
    assert prof["architecture"]["is_monolith"] is True
    assert prof["architecture"]["pipeline_separated"] is False


def test_modular_detection(tmp_path):
    src = "".join(f"def step_{i}():\n    return {i}\n\n" for i in range(6))
    _write(tmp_path, "mods.py", src)
    prof = profiler.profile_project(tmp_path)
    assert prof["architecture"]["is_monolith"] is False
    assert prof["architecture"]["pipeline_separated"] is True


# ── compare_projects ─────────────────────────────────────────────────────────


def test_compare_projects_keys_by_dir(tmp_path):
    (tmp_path / "projA").mkdir()
    _write(tmp_path / "projA", "a.py", "def a():\n    pass\n")
    (tmp_path / "projB").mkdir()
    _write(tmp_path / "projB", "b.py", "def b():\n    pass\n")
    results = profiler.compare_projects(tmp_path)
    assert set(results) == {"projA", "projB"}
    assert results["projA"]["structure"]["public_function_count"] == 1


def test_compare_projects_skip_accepts_string_or_list(tmp_path):
    for name in ("keep", "drop1", "drop2"):
        (tmp_path / name).mkdir()
        _write(tmp_path / name, "m.py", "def f():\n    pass\n")
    as_list = profiler.compare_projects(tmp_path, ["drop1", "drop2"])
    as_str = profiler.compare_projects(tmp_path, "drop1, drop2")
    assert set(as_list) == {"keep"}
    assert set(as_str) == {"keep"}


def test_compare_projects_honours_default_skips(tmp_path):
    (tmp_path / "node_modules").mkdir()
    _write(tmp_path / "node_modules", "x.py", "def x():\n    pass\n")
    (tmp_path / "app").mkdir()
    _write(tmp_path / "app", "y.py", "def y():\n    pass\n")
    results = profiler.compare_projects(tmp_path)
    assert set(results) == {"app"}


# ── _normalize_skip unit ─────────────────────────────────────────────────────


def test_normalize_skip_forms():
    assert profiler._normalize_skip(None) == set()
    assert profiler._normalize_skip("a, b c") == {"a", "b", "c"}
    assert profiler._normalize_skip(["a", "", "b"]) == {"a", "b"}


# ── audit-HIGH integration (Tool 1) ──────────────────────────────────────────


class _FakeResult:
    def __init__(self, high):
        self.high = high

        class _S:
            value = "FAIL" if high else "PASS"

        self.status = _S()


def test_audit_high_counts_shape(monkeypatch, tmp_path):
    from audit_code import phd, wiring

    monkeypatch.setattr(wiring, "run", lambda p: _FakeResult(3))
    monkeypatch.setattr(phd, "run", lambda p, *a, **k: _FakeResult(5))
    counts = profiler.audit_high_counts(tmp_path)
    assert counts["wiring_high"] == 3
    assert counts["phd_high"] == 5
    assert counts["total_high"] == 8


def test_compare_off_by_default_has_no_audit(monkeypatch, tmp_path):
    (tmp_path / "p").mkdir()
    _write(tmp_path / "p", "m.py", "def f():\n    pass\n")
    # Guard: audit_high_counts must not be invoked when include_audit is False.
    monkeypatch.setattr(
        profiler,
        "audit_high_counts",
        lambda p: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    results = profiler.compare_projects(tmp_path)
    assert "audit" not in results["p"]


def test_compare_include_audit_attaches_counts(monkeypatch, tmp_path):
    (tmp_path / "p").mkdir()
    _write(tmp_path / "p", "m.py", "def f():\n    pass\n")
    monkeypatch.setattr(
        profiler,
        "audit_high_counts",
        lambda p: {"wiring_high": 1, "phd_high": 2, "total_high": 3},
    )
    results = profiler.compare_projects(tmp_path, include_audit=True)
    assert results["p"]["audit"]["total_high"] == 3


def test_compare_table_shows_ahigh_column():
    from audit_code import cli

    results = {
        "proj": {
            "structure": {
                "loc": 10,
                "function_count": 1,
                "class_count": 0,
                "loop_count": 0,
            },
            "metrics": {"parse_seconds": 0.0},
            "architecture": {"is_monolith": False},
            "performance": {"compute_ops": 0, "estimated_speed": "medium"},
            "audit": {"total_high": 7},
        }
    }
    table = cli._compare_table(results)
    assert "AHIGH" in table
    assert " 7" in table


def test_compare_table_hides_ahigh_without_audit():
    from audit_code import cli

    results = {
        "proj": {
            "structure": {
                "loc": 10,
                "function_count": 1,
                "class_count": 0,
                "loop_count": 0,
            },
            "metrics": {"parse_seconds": 0.0},
            "architecture": {"is_monolith": False},
            "performance": {"compute_ops": 0, "estimated_speed": "medium"},
        }
    }
    assert "AHIGH" not in cli._compare_table(results)


# ── classify_dead_symbols (Tool 6) ───────────────────────────────────────────


def test_classify_critical_uses_pipeline_verbs(tmp_path):
    _write(tmp_path, "engine.py", "def render_overlay():\n    return 1\n")
    dead = [{"name": "render_overlay", "file": "engine.py", "line": 1}]
    cfg = {"profile": {"pipeline_verbs": ["render"]}}
    result = profiler.classify_dead_symbols(tmp_path, dead, config=cfg)
    assert [d["name"] for d in result["critical"]] == ["render_overlay"]
    assert result["accuracy_impact"] is True


def test_classify_utility_bucket(tmp_path):
    _write(tmp_path, "m.py", "def lerp(a, b, t):\n    return a\n")
    dead = [{"name": "lerp", "file": "m.py", "line": 1}]
    cfg = {"profile": {"utility_markers": ["lerp", "clamp"]}}
    result = profiler.classify_dead_symbols(tmp_path, dead, config=cfg)
    assert [d["name"] for d in result["utility"]] == ["lerp"]
    assert result["accuracy_impact"] is False


def test_classify_no_config_neutral_name_is_other(tmp_path):
    _write(tmp_path, "m.py", "def widget_thing():\n    return 1\n")
    dead = [{"name": "widget_thing", "file": "m.py", "line": 1}]
    result = profiler.classify_dead_symbols(tmp_path, dead)
    assert result["critical"] == []
    assert [d["name"] for d in result["other"]] == ["widget_thing"]


def test_classify_default_verbs_flag_critical(tmp_path):
    # No config: the general-purpose default verbs still catch obvious pipeline
    # names (render/apply/process/...), so a dead 'render_x' is critical.
    _write(tmp_path, "m.py", "def render_x():\n    return 1\n")
    dead = [{"name": "render_x", "file": "m.py", "line": 1}]
    result = profiler.classify_dead_symbols(tmp_path, dead)
    assert [d["name"] for d in result["critical"]] == ["render_x"]


def test_classify_drops_symbol_referenced_elsewhere(tmp_path):
    _write(tmp_path, "engine.py", "def render_overlay():\n    return 1\n")
    _write(tmp_path, "app.py", "from engine import render_overlay\n")
    dead = [{"name": "render_overlay", "file": "engine.py", "line": 1}]
    cfg = {"profile": {"pipeline_verbs": ["render"]}}
    result = profiler.classify_dead_symbols(tmp_path, dead, config=cfg)
    # referenced in app.py → not actually dead → dropped from every bucket
    assert result["critical"] == []
    assert result["other"] == []


def test_classify_skips_nameless_entry(tmp_path):
    _write(tmp_path, "m.py", "x = 1\n")
    result = profiler.classify_dead_symbols(tmp_path, [{"file": "m.py"}])
    assert result["summary"] == "0 dead features, 0 dead utilities, 0 other"


# ── wiring.collect_dead_symbols + CLI (integration, real subprocess) ──────────


def test_collect_dead_symbols_finds_dead(tmp_path):
    from audit_code import wiring

    _write(
        tmp_path,
        "engine.py",
        "def render_frame():\n    return 1\n\n\n"
        "def used():\n    return render_frame()\n\n\n"
        "def orphan_helper():\n    return 2\n",
    )
    _write(tmp_path, "app.py", "from engine import used\n\nprint(used())\n")
    dead = wiring.collect_dead_symbols(tmp_path)
    names = {d["name"] for d in dead}
    assert "orphan_helper" in names
    assert "used" not in names  # used is wired via app.py


def test_cli_deadcode_runs(tmp_path):
    _write(tmp_path, "engine.py", "def orphan():\n    return 1\n")
    out = subprocess.run(
        [sys.executable, "-m", "audit_code", "deadcode", "--path", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert out.returncode == 0
    assert "dead code:" in out.stdout


# ── in-process handler coverage ──────────────────────────────────────────────
# Subprocess smoke tests above prove end-to-end dispatch, but the parent
# coverage run can't see into a child process, so the handler bodies (and their
# _split_path_json / _emit / *_summary helpers) would read as "never executed".
# These call the handlers directly so their execution is actually proven.


def test_handle_profile_inprocess(monkeypatch, capsys, tmp_path):
    from audit_code import cli

    _write(tmp_path, "m.py", "def f():\n    pass\n")
    monkeypatch.setattr(sys, "argv", ["audit-test", "profile", "--path", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli._handle_profile()
    assert exc.value.code == 0
    assert "files 1" in capsys.readouterr().out


def test_handle_compare_inprocess(monkeypatch, capsys, tmp_path):
    from audit_code import cli

    (tmp_path / "proj").mkdir()
    _write(tmp_path / "proj", "m.py", "def f():\n    pass\n")
    monkeypatch.setattr(sys, "argv", ["audit-test", "compare", "--path", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli._handle_compare()
    assert exc.value.code == 0
    assert "proj" in capsys.readouterr().out


def test_handle_deadcode_inprocess(monkeypatch, capsys, tmp_path):
    from audit_code import cli

    _write(tmp_path, "engine.py", "def orphan():\n    return 1\n")
    monkeypatch.setattr(
        sys, "argv", ["audit-test", "deadcode", "--path", str(tmp_path)]
    )
    with pytest.raises(SystemExit) as exc:
        cli._handle_deadcode()
    assert exc.value.code == 0
    assert "dead code:" in capsys.readouterr().out


def test_handle_profile_json_inprocess(monkeypatch, capsys, tmp_path):
    from audit_code import cli

    _write(tmp_path, "m.py", "def f():\n    pass\n")
    out_json = tmp_path / "prof.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["audit-test", "profile", "--path", str(tmp_path), "--json", str(out_json)],
    )
    with pytest.raises(SystemExit):
        cli._handle_profile()
    assert out_json.exists()
    assert "structure" in out_json.read_text(encoding="utf-8")


# ── CLI smoke ────────────────────────────────────────────────────────────────


def test_cli_profile_runs(tmp_path):
    _write(tmp_path, "m.py", "def f():\n    pass\n")
    out = subprocess.run(
        [sys.executable, "-m", "audit_code", "profile", "--path", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert out.returncode == 0
    assert "files 1" in out.stdout


def test_cli_compare_runs(tmp_path):
    (tmp_path / "p1").mkdir()
    _write(tmp_path / "p1", "m.py", "def f():\n    pass\n")
    out = subprocess.run(
        [sys.executable, "-m", "audit_code", "compare", "--path", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert out.returncode == 0
    assert "p1" in out.stdout
