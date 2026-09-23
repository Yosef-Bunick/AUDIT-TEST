"""Import alias for the `audit-test` distribution.

The distribution installs as ``audit-test`` and its console scripts are
``audit-test`` / ``audit-code``, but the implementation package is
``audit_code``.  A user who installs ``audit-test`` naturally reaches for
``python -m audit_test`` — most of all on locked-down Windows, where the
console script cannot run at all (AppLocker commonly blocks executing .exe
files out of %APPDATA%) and ``python -m`` is the only route in.  Making the
obvious name work is cheaper than explaining why it doesn't.

Everything lives in :mod:`audit_code`; this package only forwards.
"""

from audit_code.cli import main

__all__ = ["main"]
