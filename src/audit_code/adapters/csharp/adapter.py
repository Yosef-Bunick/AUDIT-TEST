"""C# adapter — `dotnet build` is the real gate (there is no lightweight
syntax-only checker on a normal PATH). Restore failures (offline, missing
feed) are an honest SKIP, because the compiler never ran."""

import re
from pathlib import Path

from audit_code.adapters.base import (
    LanguageAdapter,
    iter_source_files,
    run_tool,
    which,
)

# MSBuild: path(line,col): error CS1002: ; expected [proj.csproj]
_ERR = re.compile(r"^(.*?)\((\d+),\d+\):\s*error\s+(CS\d+):\s*(.*?)(\s*\[.*\])?$")


class CsharpAdapter(LanguageAdapter):
    """Language adapter for C# projects."""

    language = "csharp"
    extensions = (".cs",)
    marker_files = ()
    tool_hint = "install the .NET SDK from dotnet.microsoft.com"

    @classmethod
    def check_files(cls, root: Path, files: list):
        dotnet = which("dotnet")
        if not dotnet:
            return cls.skip("dotnet not found — cannot check syntax", True)
        projects = list(iter_source_files(root, (".csproj", ".sln", ".slnx")))
        if not projects:
            return cls.skip(
                f"{len(files)} .cs file(s) but no .csproj/.sln — "
                "bare files cannot be built"
            )

        rc, out, err = run_tool([dotnet, "build", "--nologo", "-v:q"], root)
        if rc == -1:
            return cls.skip("dotnet build timed out")
        if rc == -2:
            return cls.skip(f"dotnet failed to launch: {err}", True)

        text = out + "\n" + err
        if rc != 0 and ("error NU" in text or "Restore failed" in text):
            return cls.skip(
                "NuGet restore failed — compiler never ran, cannot judge "
                "(check network/feeds)"
            )

        findings = []
        seen = set()
        for ln in text.splitlines():
            m = _ERR.match(ln.strip())
            if m:
                key = (m.group(1), m.group(2), m.group(3))
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    cls.finding(
                        f"{m.group(3)}: {m.group(4)}",
                        file=m.group(1),
                        line=int(m.group(2)),
                    )
                )
        if rc != 0 and not findings:
            tail = "\n".join(text.strip().splitlines()[-5:])
            findings.append(cls.finding(f"dotnet build failed (rc={rc}): {tail}"))
        notes = [
            f"{len(files)} C# file(s) across {len(projects)} project(s) "
            "checked via dotnet build"
        ]
        return cls.result(findings, notes)

    @staticmethod
    def test_command(target_root: Path) -> list | None:
        dotnet = which("dotnet")
        if not dotnet:
            return None
        projects = list(iter_source_files(target_root, (".csproj", ".sln")))
        if projects:
            return [dotnet, "test", "--nologo"]
        return None
