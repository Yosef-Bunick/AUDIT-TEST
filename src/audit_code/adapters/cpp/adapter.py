"""C/C++ adapter — per-file `-fsyntax-only` (gcc/clang) or `/Zs` (MSVC cl).
This genuinely parses each translation unit. Missing project headers are
counted but not judged (they need the build system's include paths). Headers
are not compiled standalone — most need a surrounding translation unit."""

import re
from pathlib import Path

from audit_code.adapters.base import (
    MAX_PER_FILE_CHECKS,
    LanguageAdapter,
    TimeBudget,
    rel,
    run_tool,
    which,
)

_UNIT_EXTS = (".c", ".cc", ".cpp", ".cxx")
_GCC_ERR = re.compile(r"^(.*?):(\d+):(?:\d+:)?\s*(?:fatal )?error:\s*(.*)$")
_CL_ERR = re.compile(r"^(.*?)\((\d+)\):\s*(?:fatal )?error\s+C\d+:\s*(.*)$")
_MISSING_HDR = ("No such file or directory", "file not found", "Cannot open include")


class CppAdapter(LanguageAdapter):
    """Language adapter for C/C++ projects."""

    language = "cpp"
    extensions = _UNIT_EXTS + (".h", ".hpp", ".hh")
    marker_files = ("CMakeLists.txt", "Makefile", "meson.build")
    tool_hint = "install gcc/clang (or MSVC cl.exe on PATH)"

    @classmethod
    def check_files(cls, root: Path, files: list):
        cc_cxx = which("g++") or which("clang++")
        cc_c = which("gcc") or which("clang") or cc_cxx
        cl = which("cl")
        if not cc_cxx and not cc_c and not cl:
            return cls.skip("no C/C++ compiler found — cannot check syntax", True)

        units = [f for f in files if f.suffix in _UNIT_EXTS]
        headers = len(files) - len(units)
        if not units:
            return cls.skip(
                f"{headers} header file(s) only — headers are not compiled "
                "standalone (no translation units found)"
            )

        findings, notes = [], []
        missing_hdrs = 0
        budget = TimeBudget()
        checked = 0
        for f in units[:MAX_PER_FILE_CHECKS]:
            if budget.exhausted():
                notes.append(
                    f"time budget exhausted after {checked}/{len(units)} units"
                )
                break
            if cl and not cc_cxx and not cc_c:
                cmd = [cl, "/Zs", "/nologo", str(f)]
                err_re = _CL_ERR
            else:
                preferred = cc_c if f.suffix == ".c" else cc_cxx
                fallback = cc_cxx or cc_c
                if preferred is not None:
                    cmd = [preferred, "-fsyntax-only", "-I", str(f.parent), str(f)]
                elif fallback is not None:
                    mode = "c" if f.suffix == ".c" else "c++"
                    cmd = [
                        fallback,
                        "-x",
                        mode,
                        "-fsyntax-only",
                        "-I",
                        str(f.parent),
                        str(f),
                    ]
                else:  # unreachable: the skip() above guarantees a compiler
                    break
                err_re = _GCC_ERR
            rc, out, err = run_tool(cmd, root, timeout=60)
            checked += 1
            if rc == 0:
                continue
            for ln in (err + "\n" + out).splitlines():
                m = err_re.match(ln.strip())
                if not m:
                    continue
                msg = m.group(3)
                if any(marker in msg for marker in _MISSING_HDR):
                    missing_hdrs += 1
                    continue
                findings.append(
                    cls.finding(msg, file=rel(f, root), line=int(m.group(2)))
                )
        notes.insert(
            0,
            f"{checked}/{len(units)} translation unit(s) parsed "
            f"({headers} header(s) not compiled standalone)",
        )
        if missing_hdrs:
            notes.append(
                f"{missing_hdrs} missing-include error(s) not judged "
                "(needs the build system's include paths)"
            )
        if len(units) > MAX_PER_FILE_CHECKS:
            notes.append(f"capped at {MAX_PER_FILE_CHECKS} files")
        return cls.result(findings, notes)

    @staticmethod
    def test_command(target_root: Path) -> list | None:
        ctest = which("ctest")
        if ctest and (target_root / "build").is_dir():
            return [ctest, "--test-dir", "build", "--output-on-failure"]
        return None
