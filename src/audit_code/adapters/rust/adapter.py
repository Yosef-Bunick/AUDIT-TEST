"""Rust adapter — `cargo check` is the real syntax+type gate for a Cargo
project. Bare .rs files without Cargo.toml cannot be judged (module
resolution needs a crate), so that case is an honest SKIP."""

import re
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which

# short message format: path:line:col: error[E0308]: mismatched types
_ERR = re.compile(r"^(.*?):(\d+):(\d+):\s*error(\[\w+\])?:?\s*(.*)$")


class RustAdapter(LanguageAdapter):
    """Language adapter for Rust projects."""

    language = "rust"
    extensions = (".rs",)
    marker_files = ("Cargo.toml",)
    tool_hint = "install Rust from rustup.rs"

    @classmethod
    def check_files(cls, root: Path, files: list):
        cargo = which("cargo")
        if not cargo:
            return cls.skip("cargo not found — cannot check syntax", True)
        if not (root / "Cargo.toml").exists():
            return cls.skip(
                f"{len(files)} .rs file(s) but no Cargo.toml — bare files "
                "cannot be checked (module resolution needs a crate)"
            )

        rc, out, err = run_tool(
            [cargo, "check", "-q", "--message-format", "short"], root
        )
        if rc == -1:
            return cls.skip("cargo check timed out")
        if rc == -2:
            return cls.skip(f"cargo failed to launch: {err}", True)

        findings = []
        for ln in err.splitlines():
            m = _ERR.match(ln.strip())
            if m:
                findings.append(
                    cls.finding(m.group(5), file=m.group(1), line=int(m.group(2)))
                )
        if rc != 0 and not findings:
            # cargo failed but we could not parse why — fail closed
            tail = "\n".join(err.strip().splitlines()[-5:])
            findings.append(cls.finding(f"cargo check failed (rc={rc}): {tail}"))
        notes = [f"{len(files)} Rust file(s) checked via cargo check"]
        return cls.result(findings, notes)

    @staticmethod
    def test_command(target_root: Path) -> list | None:
        cargo = which("cargo")
        if cargo and (target_root / "Cargo.toml").exists():
            return [cargo, "test", "-q"]
        return None
