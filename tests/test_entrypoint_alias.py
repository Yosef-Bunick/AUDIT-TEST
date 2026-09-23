"""T1 anchor: audit_test alias package (`python -m audit_test`).

The distribution is named `audit-test` while the implementation package is
`audit_code`, so the name users reach for must not dead-end — especially on
locked-down Windows, where `python -m` is the only way in when AppLocker
blocks the console script.
"""

import subprocess
import sys

import audit_test
from audit_code.cli import main as cli_main


def test_alias_reexports_cli_main():
    assert audit_test.main is cli_main


def test_python_dash_m_audit_test_runs():
    """The alias must be executable, not merely importable."""
    proc = subprocess.run(
        [sys.executable, "-m", "audit_test", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage: audit-test" in proc.stdout


def test_both_module_names_share_one_entry_point():
    import audit_code.cli as impl

    assert audit_test.main.__module__ == impl.main.__module__
