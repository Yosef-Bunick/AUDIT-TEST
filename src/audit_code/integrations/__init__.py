"""External tool integrations."""

from audit_code.integrations.codeql import run as _codeql
from audit_code.integrations.dependency_scan import run as _deps
from audit_code.integrations.megalinter import run as _megalinter
from audit_code.integrations.secret_scan import run as _secret
from audit_code.integrations.semgrep import run as _semgrep

TOOLS = {
    "semgrep": _semgrep,
    "megalinter": _megalinter,
    "codeql": _codeql,
    "secret-scan": _secret,
    "dependency-scan": _deps,
}
