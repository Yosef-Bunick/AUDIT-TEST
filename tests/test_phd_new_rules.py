# ruff: noqa: S101, S603
"""Tests for new Python PhD rules: C7, C8, C9, SEC4, B4, G3, T7, F5,
SEC6, SEC7, B5, R10."""

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_phd(tmp_path, code, extra_files=None):
    """Run audit_phd.py against tmp_path with given code, return JSON findings."""
    (tmp_path / "mod.py").write_text(code, encoding="utf-8")
    if extra_files:
        for name, content in extra_files.items():
            f = tmp_path / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "audit_code.audit_phd",
            "--path",
            str(tmp_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        cwd=str(_REPO_ROOT),
    )
    return json.loads(r.stdout)


def _ids(data, cid):
    return [f["msg"] for f in data.get(cid, [])]


# ── C7: rmtree without guard ─────────────────────────────────────────────


def test_c7_flags_bare_rmtree(tmp_path):
    data = _run_phd(tmp_path, "import shutil\ndef f(): shutil.rmtree('/tmp')\n")
    assert data.get("C7"), "bare rmtree should be flagged"


def test_c7_skips_ignore_errors(tmp_path):
    data = _run_phd(
        tmp_path, "import shutil\ndef f(): shutil.rmtree('/tmp', ignore_errors=True)\n"
    )
    assert not data.get("C7"), "ignore_errors=True should skip"


def test_c7_skips_try_except(tmp_path):
    data = _run_phd(
        tmp_path,
        "import shutil\ndef f():\n try: shutil.rmtree('/tmp')\n except OSError: pass\n",
    )
    assert not data.get("C7"), "try/except OSError should skip"


# ── C8: except: continue ─────────────────────────────────────────────────


def test_c8_flags_bare_except_continue(tmp_path):
    data = _run_phd(
        tmp_path, "def f():\n for x in range(10):\n  try: g()\n  except: continue\n"
    )
    assert data.get("C8"), "bare except: continue should be flagged"


def test_c8_flags_except_exception_continue(tmp_path):
    data = _run_phd(tmp_path, "def f():\n try: g()\n except Exception: continue\n")
    assert data.get("C8"), "except Exception: continue should be flagged"


def test_c8_skips_specific_except_continue(tmp_path):
    data = _run_phd(tmp_path, "def f():\n try: g()\n except ValueError: continue\n")
    assert not data.get("C8"), "except ValueError: continue should NOT be flagged"


# ── C9: float == ─────────────────────────────────────────────────────────


def test_c9_flags_float_equality(tmp_path):
    data = _run_phd(tmp_path, "def f():\n if 3.14 == x: pass\n")
    assert data.get("C9"), "float == should be flagged"


def test_c9_skips_int_equality(tmp_path):
    data = _run_phd(tmp_path, "def f():\n if 42 == x: pass\n")
    assert not data.get("C9"), "int == should not be flagged"


# ── SEC4: yaml.load unsafe ───────────────────────────────────────────────


def test_sec4_flags_unsafe_yaml_load(tmp_path):
    data = _run_phd(tmp_path, "import yaml\ndef f(): yaml.load(open('x.yaml'))\n")
    assert data.get("SEC4"), "yaml.load() without SafeLoader should be flagged"


def test_sec4_skips_safe_loader(tmp_path):
    data = _run_phd(
        tmp_path,
        "import yaml\ndef f(): yaml.load(open('x.yaml'), Loader=yaml.SafeLoader)\n",
    )
    assert not data.get("SEC4"), "yaml.load with SafeLoader should skip"


# ── B4: tempfile.mktemp / os.tempnam ─────────────────────────────────────


def test_b4_flags_mktemp(tmp_path):
    data = _run_phd(tmp_path, "import tempfile\ndef f(): tempfile.mktemp()\n")
    assert data.get("B4"), "tempfile.mktemp() should be flagged"


def test_b4_flags_tempnam(tmp_path):
    data = _run_phd(tmp_path, "import os\ndef f(): os.tempnam()\n")
    assert data.get("B4"), "os.tempnam() should be flagged"


# ── G3: __init__ returning non-None ──────────────────────────────────────


def test_g3_flags_init_return_value(tmp_path):
    data = _run_phd(tmp_path, "class Foo:\n def __init__(self): return 42\n")
    assert data.get("G3"), "__init__ returning non-None should be flagged"


def test_g3_skips_init_return_none(tmp_path):
    data = _run_phd(tmp_path, "class Foo:\n def __init__(self): return None\n")
    assert not data.get("G3"), "return None in __init__ should skip"


def test_g3_skips_init_no_return(tmp_path):
    data = _run_phd(tmp_path, "class Foo:\n def __init__(self): pass\n")
    assert not data.get("G3"), "no return in __init__ should skip"


# ── T7: mock.patch targets ───────────────────────────────────────────────


def test_t7_flags_missing_target(tmp_path):
    data = _run_phd(
        tmp_path,
        "def real(): pass\n",
        extra_files={
            "tests/test.py": "from unittest import mock\ndef t(): mock.patch('mod.fake')\n"
        },
    )
    assert data.get("T7"), "mock.patch with nonexistent target should be flagged"


def test_t7_flags_missing_module(tmp_path):
    data = _run_phd(
        tmp_path,
        "def real(): pass\n",
        extra_files={
            "tests/test.py": "from unittest import mock\ndef t(): mock.patch('nonexistent.mod.func')\n"
        },
    )
    assert data.get("T7"), "mock.patch with nonexistent module should be flagged"


def test_t7_skips_real_target(tmp_path):
    data = _run_phd(
        tmp_path,
        "def real(): pass\n",
        extra_files={
            "tests/test.py": "from unittest import mock\ndef t(): mock.patch('mod.real')\n"
        },
    )
    assert not data.get("T7"), "mock.patch with real target should skip"


def test_t7_skips_create_true(tmp_path):
    data = _run_phd(
        tmp_path,
        "def real(): pass\n",
        extra_files={
            "tests/test.py": "from unittest import mock\ndef t(): mock.patch('mod.fake', create=True)\n"
        },
    )
    assert not data.get("T7"), "mock.patch with create=True should skip"


# ── F5: lock ordering ────────────────────────────────────────────────────


def test_f5_flags_inconsistent_lock_order(tmp_path):
    code = "la = None; lb = None\ndef f(flag):\n if flag:\n  with la:\n   with lb: pass\n else:\n  with lb:\n   with la: pass\n"
    data = _run_phd(tmp_path, code)
    assert data.get("F5"), "inconsistent lock order should be flagged"


# ── SEC6: SQL built with f-string/format/concat in execute() ─────────────


def test_sec6_flags_fstring_sql(tmp_path):
    data = _run_phd(
        tmp_path,
        'def f(cur, uid): cur.execute(f"SELECT * FROM users WHERE id = {uid}")\n',
    )
    assert data.get("SEC6"), "f-string SQL in execute() should be flagged"


def test_sec6_flags_percent_sql(tmp_path):
    data = _run_phd(
        tmp_path,
        'def f(cur, uid): cur.execute("DELETE FROM t WHERE id = %s" % uid)\n',
    )
    assert data.get("SEC6"), "%-built SQL in execute() should be flagged"


def test_sec6_flags_format_sql(tmp_path):
    data = _run_phd(
        tmp_path,
        'def f(cur, uid): cur.execute("SELECT * FROM t WHERE id={}".format(uid))\n',
    )
    assert data.get("SEC6"), ".format() SQL in execute() should be flagged"


def test_sec6_skips_parameterized(tmp_path):
    data = _run_phd(
        tmp_path,
        'def f(cur, uid): cur.execute("SELECT * FROM t WHERE id = ?", (uid,))\n',
    )
    assert not data.get("SEC6"), "parameterized query should skip"


def test_sec6_skips_non_sql_execute(tmp_path):
    data = _run_phd(
        tmp_path,
        'def f(runner, task): runner.execute(f"run task {task}")\n',
    )
    assert not data.get("SEC6"), "non-SQL execute() should skip"


# ── B5: assert used for validation ───────────────────────────────────────


def test_b5_flags_validation_assert(tmp_path):
    data = _run_phd(tmp_path, "def f(x):\n assert x > 0, 'x must be positive'\n")
    assert data.get("B5"), "validation assert should be flagged"


def test_b5_skips_isinstance_narrowing(tmp_path):
    data = _run_phd(tmp_path, "def f(x):\n assert isinstance(x, int)\n")
    assert not data.get("B5"), "isinstance narrowing assert should skip"


def test_b5_skips_is_not_none_narrowing(tmp_path):
    data = _run_phd(tmp_path, "def f(x):\n assert x is not None\n")
    assert not data.get("B5"), "is-not-None narrowing assert should skip"


# ── SEC7: DEBUG = True in settings module ────────────────────────────────


def test_sec7_flags_debug_true_in_settings(tmp_path):
    data = _run_phd(
        tmp_path,
        "def unused(): pass\n",
        extra_files={"settings.py": "DEBUG = True\n"},
    )
    assert data.get("SEC7"), "DEBUG = True in settings.py should be flagged"


def test_sec7_skips_dev_settings(tmp_path):
    data = _run_phd(
        tmp_path,
        "def unused(): pass\n",
        extra_files={"settings_dev.py": "DEBUG = True\n"},
    )
    assert not data.get("SEC7"), "dev settings variant should skip"


def test_sec7_skips_debug_false(tmp_path):
    data = _run_phd(
        tmp_path,
        "def unused(): pass\n",
        extra_files={"settings.py": "DEBUG = False\n"},
    )
    assert not data.get("SEC7"), "DEBUG = False should skip"


# ── R10: logging.basicConfig() called more than once ─────────────────────


def test_r10_flags_double_basicconfig(tmp_path):
    data = _run_phd(
        tmp_path,
        "import logging\ndef f(): logging.basicConfig(level=1)\n",
        extra_files={
            "other.py": "import logging\ndef g(): logging.basicConfig(level=2)\n"
        },
    )
    assert data.get("R10"), "two basicConfig() calls should be flagged"


def test_r10_skips_single_basicconfig(tmp_path):
    data = _run_phd(
        tmp_path,
        "import logging\ndef f(): logging.basicConfig(level=1)\n",
    )
    assert not data.get("R10"), "one basicConfig() call should skip"


# ── coverage for previously untested HIGH rules ──────────────────────────


def test_sec3_flags_hardcoded_token(tmp_path):
    data = _run_phd(tmp_path, 'KEY = "sk-abcdefghij1234567890XYZ"\n')
    assert data.get("SEC3"), "hardcoded sk- token should be flagged"


def test_sec3_flags_secret_assignment(tmp_path):
    data = _run_phd(tmp_path, 'api_key = "zjx9k2m4n8q1w7e5"\n')
    assert data.get("SEC3"), "secret-named literal assignment should be flagged"


def test_sec3_skips_env_lookup(tmp_path):
    data = _run_phd(tmp_path, "import os\napi_key = os.environ['API_KEY']\n")
    assert not data.get("SEC3"), "env-sourced secret should skip"


def test_sec5_flags_sqlite_without_fk_pragma(tmp_path):
    data = _run_phd(
        tmp_path,
        "from sqlalchemy import create_engine\n"
        "engine = create_engine('sqlite:///app.db')\n",
    )
    assert data.get("SEC5"), "sqlite engine without FK pragma should be flagged"


def test_sec5_skips_with_fk_pragma(tmp_path):
    data = _run_phd(
        tmp_path,
        "from sqlalchemy import create_engine\n"
        "engine = create_engine('sqlite:///app.db')\n"
        "# PRAGMA foreign_keys=ON is set in the connect event\n",
    )
    assert not data.get("SEC5"), "FK pragma mention should skip"


def test_f1_flags_unacquired_lock(tmp_path):
    data = _run_phd(
        tmp_path,
        "import threading\nmy_lock = threading.Lock()\ndef f(): return 1\n",
    )
    assert data.get("F1"), "lock defined but never acquired should be flagged"


def test_f1_skips_acquired_lock(tmp_path):
    data = _run_phd(
        tmp_path,
        "import threading\nmy_lock = threading.Lock()\n"
        "def f():\n with my_lock:\n  return 1\n",
    )
    assert not data.get("F1"), "lock used via `with` should skip"


def test_e1_flags_prompt_frozen_at_import(tmp_path):
    data = _run_phd(tmp_path, "SYSTEM_PROMPT = prompt_for_role('planner')\n")
    assert data.get("E1"), "module-level prompt call should be flagged"


def test_e2_flags_hook_prompt_without_task(tmp_path):
    data = _run_phd(
        tmp_path,
        "def f(): register_raw_prompt('hook', 'just do the thing')\n",
    )
    assert data.get("E2"), "hook prompt without {task} should be flagged"


def test_e2_skips_hook_prompt_with_task(tmp_path):
    data = _run_phd(
        tmp_path,
        "def f(): register_raw_prompt('hook', 'do this: {task}')\n",
    )
    assert not data.get("E2"), "hook prompt with {task} should skip"


def test_p1_flags_import_in_loop(tmp_path):
    data = _run_phd(
        tmp_path,
        "def f():\n for i in range(3):\n  import json\n  json.dumps(i)\n",
    )
    assert data.get("P1"), "import inside a loop should be flagged"


def test_d2_flags_circular_imports(tmp_path):
    data = _run_phd(
        tmp_path,
        "import other\ndef f(): return other.g()\n",
        extra_files={"other.py": "import mod\ndef g(): return mod.f()\n"},
    )
    assert data.get("D2"), "circular module imports should be flagged"


# ── coverage for previously untested MEDIUM/INFO rules ───────────────────


def test_c3_flags_silent_fallback_return(tmp_path):
    data = _run_phd(
        tmp_path,
        "def f():\n try:\n  return g()\n except Exception:\n  return None\n",
    )
    assert data.get("C3"), "silent fallback return should be flagged"


def test_c4_flags_open_outside_with(tmp_path):
    data = _run_phd(tmp_path, "def f():\n return open('x.txt').read()\n")
    assert data.get("C4"), "open().read() chain should be flagged"


def test_c5_flags_exists_then_remove(tmp_path):
    data = _run_phd(
        tmp_path,
        "import os\ndef f(p):\n if os.path.exists(p):\n  os.remove(p)\n",
    )
    assert data.get("C5"), "exists-then-remove TOCTOU should be flagged"


def test_c6_flags_bare_index_on_parsed_json(tmp_path):
    data = _run_phd(
        tmp_path,
        "import json\ndef f(reply):\n d = json.loads(reply)\n return d['field']\n",
    )
    assert data.get("C6"), "bare index on LLM-parsed dict should be flagged"


def test_b2_flags_http_without_timeout(tmp_path):
    data = _run_phd(
        tmp_path,
        "import requests\ndef f(): return requests.get('http://x')\n",
    )
    assert data.get("B2"), "requests.get without timeout should be flagged"


def test_b2_skips_http_with_timeout(tmp_path):
    data = _run_phd(
        tmp_path,
        "import requests\ndef f(): return requests.get('http://x', timeout=5)\n",
    )
    assert not data.get("B2"), "requests.get with timeout should skip"


def test_b3_flags_daemon_thread(tmp_path):
    data = _run_phd(
        tmp_path,
        "import threading\n"
        "def f(): threading.Thread(target=f, daemon=True).start()\n",
    )
    assert data.get("B3"), "daemon thread should be flagged"


def test_f2_flags_global_statement(tmp_path):
    data = _run_phd(tmp_path, "x = 0\ndef f():\n global x\n x = 1\n")
    assert data.get("F2"), "global statement should be flagged"


def test_f3_flags_import_time_io(tmp_path):
    data = _run_phd(tmp_path, "data = open('cfg.json')\n")
    assert data.get("F3"), "import-time open() should be flagged"


def test_f4_flags_bare_cfg_indexing(tmp_path):
    data = _run_phd(tmp_path, "def f(cfg): return cfg['key']\n")
    assert data.get("F4"), "bare cfg[...] indexing should be flagged"


def test_g1_flags_hardcoded_knob(tmp_path):
    data = _run_phd(tmp_path, "MAX_RETRIES = 5\n")
    assert data.get("G1"), "hardcoded tuning knob should be flagged"


def test_d1_flags_duplicate_function_bodies(tmp_path):
    dup = "def helper():\n    return 42\n"
    data = _run_phd(tmp_path, dup, extra_files={"other.py": dup})
    assert data.get("D1"), "identical function bodies in two files should be flagged"


def test_d4_flags_hardcoded_model_string(tmp_path):
    data = _run_phd(tmp_path, "MODEL = 'gpt-4o-mini'\n")
    assert data.get("D4"), "hardcoded model string should be flagged"


def test_d5_flags_scattered_env_read(tmp_path):
    data = _run_phd(tmp_path, "import os\ndef f(): return os.getenv('X')\n")
    assert data.get("D5"), "os.getenv outside config layer should be flagged"


def test_p2_flags_import_in_function(tmp_path):
    data = _run_phd(tmp_path, "def f():\n import json\n return json.dumps(1)\n")
    assert data.get("P2"), "import inside function should be counted"


def test_p3_flags_recompile_in_function(tmp_path):
    data = _run_phd(tmp_path, "import re\ndef f():\n return re.compile('x')\n")
    assert data.get("P3"), "re.compile inside function should be flagged"


def test_p4_flags_settings_lookup_in_loop(tmp_path):
    data = _run_phd(
        tmp_path,
        "def f():\n for i in range(3):\n  s = get_settings()\n  use(s)\n",
    )
    assert data.get("P4"), "settings lookup inside loop should be flagged"


def test_dg1_flags_god_function(tmp_path):
    body = "".join(f" x{i} = {i}\n" for i in range(130))
    data = _run_phd(tmp_path, "def f():\n" + body)
    assert data.get("DG1"), "130-line function should be flagged as god function"


def test_t1_flags_untested_module(tmp_path):
    data = _run_phd(tmp_path, "def prod_only(): return 1\n")
    assert data.get("T1"), "module with no referencing test should be flagged"


def test_t4_flags_assertion_free_test(tmp_path):
    data = _run_phd(
        tmp_path,
        "def real(): return 1\n",
        extra_files={"tests/test_mod.py": "import mod\ndef test_x():\n mod.real()\n"},
    )
    assert data.get("T4"), "assertion-free test should be flagged"


def test_f5_skips_consistent_order(tmp_path):
    code = "la = None; lb = None\ndef f():\n with la:\n  with lb: pass\n"
    data = _run_phd(tmp_path, code)
    assert not data.get("F5"), "consistent lock order should skip"


def test_f5_skips_single_lock(tmp_path):
    code = "la = None\ndef f():\n with la: pass\n"
    data = _run_phd(tmp_path, code)
    assert not data.get("F5"), "single lock should skip"


# R9: broken structured logging
def test_r9_flags_invalid_logging_kwargs(tmp_path):
    data = _run_phd(tmp_path, "log=None\ndef f(): log.info('msg', entry_id=42)\n")
    assert data.get("R9"), "log.info with invalid kwarg should be flagged"


def test_r9_skips_valid_kwargs(tmp_path):
    data = _run_phd(
        tmp_path, "log=None\ndef f(): log.info('msg', extra={'key': 'val'})\n"
    )
    assert not data.get("R9"), "log.info with extra= should skip"
