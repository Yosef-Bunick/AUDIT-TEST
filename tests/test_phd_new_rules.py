# ruff: noqa: S101, S603
"""Tests for new Python PhD rules: C7, C8, C9, SEC4, B4, G3, T7, F5."""

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
