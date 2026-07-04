"""Language adapters — one per supported language."""

from pathlib import Path

from audit_code.adapters.cpp.adapter import CppAdapter
from audit_code.adapters.csharp.adapter import CsharpAdapter
from audit_code.adapters.go.adapter import GoAdapter
from audit_code.adapters.html.adapter import HtmlAdapter
from audit_code.adapters.java.adapter import JavaAdapter
from audit_code.adapters.javascript.adapter import JavaScriptAdapter
from audit_code.adapters.python.adapter import PythonAdapter
from audit_code.adapters.rust.adapter import RustAdapter
from audit_code.adapters.sql.adapter import SqlAdapter

ALL = [
    PythonAdapter,
    JavaScriptAdapter,
    JavaAdapter,
    GoAdapter,
    RustAdapter,
    CsharpAdapter,
    CppAdapter,
    HtmlAdapter,
    SqlAdapter,
]


def discover(target_root: Path) -> list:
    """Return the adapter classes for every language detected in the target.

    Detection only — the runner decides when to run syntax checks and test
    suites so their results land in the report.
    """
    return [a for a in ALL if a.detect(target_root)]
