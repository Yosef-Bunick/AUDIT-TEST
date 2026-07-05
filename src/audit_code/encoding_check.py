"""encoding_check.py — universal source-encoding gate.

Verifies that every text file under a project decodes cleanly under a chosen
codec (strict, no error replacement).  The codec is either passed explicitly
(`audit-test check gb18030`) or read from the target project's
`.audit-test-ignore` (`#encoding <name>`), defaulting to UTF-8.

  check utf-8    -> every file must be valid UTF-8
  check ascii    -> every file must be pure ASCII
  check gb18030  -> every file must be valid GB 18030

Binary files (detected by a NUL byte) are skipped so images/archives do not
false-fail.  Skip/exclude and focus-group rules are honoured via should_audit.
"""

import os
from pathlib import Path

from audit_code.audit_shared import (
    EXCLUDE_DIRS,
    configured_encoding,
    force_utf8_streams,
    normalize_encoding,
    should_audit,
)

# Only the first slice is sniffed for NUL bytes when classifying binary files.
_BINARY_SNIFF_BYTES = 4096


def _looks_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:_BINARY_SNIFF_BYTES]


def scan(root: Path, encoding: str) -> tuple[list[tuple[Path, int, str]], int]:
    """Return (failures, files_checked).

    Each failure is (path, byte_offset, reason) for a file that does NOT decode
    under *encoding*.
    """
    failures: list[tuple[Path, int, str]] = []
    checked = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if not should_audit(p):
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if _looks_binary(raw):
                continue
            checked += 1
            try:
                raw.decode(encoding)
            except UnicodeDecodeError as e:
                failures.append((p, e.start, e.reason))
    return failures, checked


def run(target_root: Path, encoding: str | None = None) -> int:
    """Run the encoding check and print a report. Return an exit code (0 = clean)."""
    force_utf8_streams()
    root = target_root.resolve()
    enc = normalize_encoding(encoding or configured_encoding(root))

    failures, checked = scan(root, enc)

    bar = "=" * 74
    print(bar)
    print(f"ENCODING [{enc}] — {len(failures)} file(s) not decodable / {checked} checked")
    print(bar)
    if not failures:
        print(f"  all {checked} text file(s) are valid {enc}")
        return 0
    for path, offset, reason in failures[:50]:
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        print(f"  {rel}  byte {offset}: {reason}")
    if len(failures) > 50:
        print(f"  ... and {len(failures) - 50} more")
    return 1
