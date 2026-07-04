"""Java adapter — javac has no syntax-only mode, so we compile with -proc:none
and judge ONLY parse-category errors (whitelist). Errors caused by unresolved
dependencies ("cannot find symbol", "package does not exist") are counted but
not judged — they need the project's classpath, which `mvn/gradle test` (the
adapter's test command) exercises properly."""

import re
import tempfile
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, run_tool, which

_ERR = re.compile(r"^(.*?\.java):(\d+):\s*error:\s*(.*)$")
_SYNTAX_MARKERS = (
    "expected",
    "illegal start",
    "illegal character",
    "reached end of file",
    "premature end",
    "unclosed",
    "malformed",
    "invalid method declaration",
    "class, interface",
    "not a statement",
    "unmatched",
    "orphaned",
    "misplaced",
)


class JavaAdapter(LanguageAdapter):
    """Language adapter for Java projects."""

    language = "java"
    extensions = (".java",)
    marker_files = ("pom.xml", "build.gradle", "build.gradle.kts")
    tool_hint = "install a JDK (javac must be on PATH)"

    @classmethod
    def check_files(cls, root: Path, files: list):
        javac = which("javac")
        if not javac:
            return cls.skip("javac not found — cannot check syntax", True)

        with tempfile.TemporaryDirectory(prefix="audit_java_") as tmp:
            argfile = Path(tmp) / "sources.txt"
            # javac @argfile: quote paths, forward slashes survive on Windows
            argfile.write_text(
                "\n".join(f'"{str(f).replace(chr(92), "/")}"' for f in files),
                encoding="utf-8",
            )
            rc, out, err = run_tool(
                [javac, "-proc:none", "-nowarn", "-d", tmp, f"@{argfile}"],
                root,
            )
            if rc == -1:
                return cls.skip("javac timed out")
            if rc == -2:
                return cls.skip(f"javac failed to launch: {err}", True)

            findings = []
            dep_errors = 0
            for ln in (out + "\n" + err).splitlines():
                m = _ERR.match(ln.strip())
                if not m:
                    continue
                msg = m.group(3)
                if any(marker in msg for marker in _SYNTAX_MARKERS):
                    findings.append(
                        cls.finding(msg, file=m.group(1), line=int(m.group(2)))
                    )
                else:
                    dep_errors += 1
            notes = [f"{len(files)} Java file(s) compiled via javac -proc:none"]
            if dep_errors:
                notes.append(
                    f"{dep_errors} error(s) attributable to unresolved "
                    "dependencies were not judged (needs project classpath — "
                    "run the mvn/gradle test suite for those)"
                )
            return cls.result(findings, notes)

    @staticmethod
    def test_command(target_root: Path) -> list | None:
        if (target_root / "pom.xml").exists():
            for wrapper in ("mvnw.cmd", "mvnw"):
                w = target_root / wrapper
                if w.exists():
                    return [str(w), "-q", "test"]
            mvn = which("mvn")
            return [mvn, "-q", "test"] if mvn else None
        if (target_root / "build.gradle").exists() or (
            target_root / "build.gradle.kts"
        ).exists():
            for wrapper in ("gradlew.bat", "gradlew"):
                w = target_root / wrapper
                if w.exists():
                    return [str(w), "test"]
            gradle = which("gradle")
            return [gradle, "test"] if gradle else None
        return None
