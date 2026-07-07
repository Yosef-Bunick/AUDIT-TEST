"""Haskell adapter — `ghc -fno-code` syntax check."""

from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which


class HaskellAdapter(LanguageAdapter):
    language = "haskell"
    extensions = (".hs", ".lhs")
    marker_files = ("stack.yaml", "*.cabal")
    tool_hint = "install GHC from haskell.org"

    @classmethod
    def check_files(cls, root: Path, files: list):
        ghc = which("ghc")
        if not ghc:
            return cls.skip("ghc not found", True)
        # Try stack or cabal first for whole-project check
        if (root / "stack.yaml").exists():
            stack = which("stack")
            if stack:
                rc, out, err = run_tool(
                    [stack, "build", "--dry-run"], root, timeout=120
                )
                if rc != -2:
                    findings = []
                    for ln in (out + "\n" + err).splitlines():
                        ln = ln.strip()
                        if ln and "error" in ln.lower():
                            findings.append(cls.finding(ln[:200]))
                    return cls.result(findings, ["checked via stack build --dry-run"])
        # Fallback: per-file check
        findings = []
        checked = 0
        for f in files[:100]:
            rc, out, err = run_tool([ghc, "-fno-code", str(f)], root, timeout=30)
            if rc == -2:
                continue
            checked += 1
            for ln in (out + "\n" + err).splitlines():
                ln = ln.strip()
                if ln and "error" in ln.lower():
                    findings.append(
                        cls.finding(ln[:200], file=str(f.relative_to(root)))
                    )
        return cls.result(
            findings, [f"{checked} Haskell file(s) checked via ghc -fno-code"]
        )

    @staticmethod
    def test_command(root: Path) -> list | None:
        stack = which("stack")
        if stack and (root / "stack.yaml").exists():
            return [stack, "test"]
        cabal = which("cabal")
        if cabal and any(root.glob("*.cabal")):
            return [cabal, "test"]
        return None
