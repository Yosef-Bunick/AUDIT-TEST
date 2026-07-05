"""Cross-platform UTF-8 output guarantees for the audit tool.

The audit prints Unicode glyphs (🐇 ═ ✓ ✨).  On Windows the console/pipe
defaults to cp1252, which raises UnicodeEncodeError; macOS/Linux are already
UTF-8.  These tests lock in both halves of the fix:
  * force_utf8_streams()   — reconfigures the current process's stdout+stderr
  * utf8_subprocess_env()  — forces a spawned worker to start in UTF-8
"""

import io
import os
import subprocess
import sys

from audit_code import audit_shared as sh

RABBIT = "\N{RABBIT}"  # U+1F407 — the banner glyph; not encodable in cp1252


def test_utf8_subprocess_env_sets_flags_and_preserves_base():
    env = sh.utf8_subprocess_env({"EXISTING": "1"})
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["EXISTING"] == "1"  # base entries preserved


def test_utf8_subprocess_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("SOME_MARKER", "yes")
    env = sh.utf8_subprocess_env()
    assert env["SOME_MARKER"] == "yes" and env["PYTHONIOENCODING"] == "utf-8"


def test_force_utf8_streams_survives_non_reconfigurable_stream(monkeypatch):
    # StringIO has no reconfigure() — the guard must swallow the AttributeError.
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    sh.force_utf8_streams()  # must not raise
    assert True  # reached without exception = success


def test_force_utf8_streams_switches_encoding_on_a_wrapper(monkeypatch):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    sh.force_utf8_streams()
    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
    assert sys.stderr.encoding.lower().replace("-", "") == "utf8"


def _run_print_rabbit(env):
    return subprocess.run(
        [sys.executable, "-c", "print('\\N{RABBIT}')"],
        capture_output=True,
        env=env,
    )


def test_subprocess_env_prevents_the_windows_style_crash():
    """Emulate a cp1252 child on any OS: it crashes without the fix, passes with it."""
    # Baseline: force a legacy encoding + disable UTF-8 mode → the print crashes.
    hostile = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    baseline = _run_print_rabbit(hostile)
    assert baseline.returncode != 0
    assert b"UnicodeEncodeError" in baseline.stderr

    # Layer the helper on top of that same hostile base → UTF-8 wins, no crash.
    fixed = _run_print_rabbit(sh.utf8_subprocess_env(hostile))
    assert fixed.returncode == 0
    assert RABBIT in fixed.stdout.decode("utf-8")


def test_utf8_subprocess_env_empty_dict():
    """T3 edge: empty base dict still gets UTF-8 flags."""
    env = sh.utf8_subprocess_env({})
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert len(env) == 2  # only the UTF-8 flags
