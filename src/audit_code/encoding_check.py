"""encoding_check.py — universal source-encoding gate.

Verifies that every text file under a project decodes cleanly under a chosen
codec (strict, no error replacement).  The codec is either passed explicitly
(`audit-test check gb18030`) or read from the target project's
`.audit-test-ignore` (`#encoding <name>`), defaulting to UTF-8.

  check utf-8    -> every file must be valid UTF-8
  check ascii    -> every file must be pure ASCII
  check gb18030  -> every file must be valid GB 18030

Binary files are skipped so images/archives/documents do not false-fail.  A NUL
byte alone is NOT a sufficient test: a small PDF often contains none in its
first 4 KB (`%PDF-1.3\n%\xe9\xeb\xf1\xbf` — no NUL, invalid UTF-8), so it used
to be strict-decoded and reported as a broken source file.  Detection is now
magic-bytes first, then extension, then the NUL heuristic.

Skip/exclude and focus-group rules are honoured via should_audit.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from audit_code.audit_shared import (
    EXCLUDE_DIRS,
    configured_encoding,
    force_utf8_streams,
    normalize_encoding,
    should_audit,
)
from audit_code.models import AuditResult, AuditStatus

# Only the first slice is sniffed for NUL bytes when classifying binary files.
_BINARY_SNIFF_BYTES = 4096

# Leading signatures of binary container formats that are routinely committed to
# source trees (fixtures, docs, assets). Each is checked at offset 0.
_BINARY_MAGIC: tuple[bytes, ...] = (
    b"%PDF",  # PDF
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG / JFIF / Exif
    b"GIF87a",  # GIF
    b"GIF89a",
    b"PK\x03\x04",  # zip family: zip/xlsx/docx/pptx/odt/jar/whl/egg
    b"PK\x05\x06",  # empty zip archive
    b"PK\x07\x08",  # spanned zip archive
    b"BM",  # BMP
    b"II*\x00",  # TIFF little-endian
    b"MM\x00*",  # TIFF big-endian
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"7z\xbc\xaf\x27\x1c",  # 7-zip
    b"Rar!\x1a\x07",  # RAR
    b"\x00asm",  # WebAssembly
    b"\x7fELF",  # ELF binary
    b"MZ",  # DOS/PE executable, .dll, .exe
    b"\xca\xfe\xba\xbe",  # Java class / Mach-O fat
    b"OggS",  # Ogg
    b"fLaC",  # FLAC
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",  # legacy OLE2: .doc/.xls/.ppt/.msi
    b"SQLite format 3\x00",  # SQLite database
    b"\x00\x01\x00\x00\x00",  # TrueType font
    b"OTTO",  # OpenType font
    b"wOFF",  # WOFF
    b"wOF2",  # WOFF2
)

# Container formats whose signature sits at a non-zero offset.
_BINARY_MAGIC_AT: tuple[tuple[int, bytes], ...] = (
    (4, b"ftyp"),  # MP4/HEIF/AVIF family
    (8, b"WEBP"),  # RIFF....WEBP
    (8, b"AVI "),  # RIFF....AVI
    (8, b"WAVE"),  # RIFF....WAVE
)

# Extension fallback for formats with weak or absent magic (and a cheap
# short-circuit for the common ones). Lowercase, leading dot.
_BINARY_EXTS = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".ico",
        ".icns",
        ".webp",
        ".avif",
        ".heic",
        ".psd",
        ".ai",
        ".eps",
        ".zip",
        ".jar",
        ".war",
        ".whl",
        ".egg",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".tar",
        ".lz4",
        ".zst",
        ".xlsx",
        ".xlsm",
        ".xlsb",
        ".docx",
        ".dotx",
        ".pptx",
        ".potx",
        ".xls",
        ".doc",
        ".ppt",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".obj",
        ".o",
        ".a",
        ".lib",
        ".pyc",
        ".pyo",
        ".pyd",
        ".class",
        ".wasm",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".mdb",
        ".dat",
        ".pack",
        ".idx",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        ".mp3",
        ".mp4",
        ".m4a",
        ".wav",
        ".flac",
        ".ogg",
        ".avi",
        ".mov",
        ".mkv",
        ".webm",
        ".wmv",
        ".pkl",
        ".pickle",
        ".npy",
        ".npz",
        ".h5",
        ".hdf5",
        ".pt",
        ".pth",
        ".onnx",
        ".safetensors",
        ".parquet",
        ".feather",
        ".arrow",
        ".pb",
    }
)


def _binary_by_extension(path: Path) -> bool:
    """True if *path*'s suffix names a known binary container format."""
    return path.suffix.lower() in _BINARY_EXTS


def _binary_by_magic(raw: bytes) -> bool:
    """True if *raw* starts with a known binary-format signature."""
    if raw.startswith(_BINARY_MAGIC):
        return True
    if raw[:4] == b"\x00\x00\x01\x00":  # ICO
        return True
    for offset, sig in _BINARY_MAGIC_AT:
        if raw[offset : offset + len(sig)] == sig:
            return True
    return False


def _looks_binary(raw: bytes, path: Path | None = None) -> bool:
    """Is this file binary (and therefore exempt from the encoding gate)?

    Three independent signals, cheapest-decisive first. The NUL heuristic alone
    used to be the whole test, which false-FAILed every small PDF/JPEG that
    happens to carry no NUL in its first 4 KB.
    """
    if path is not None and _binary_by_extension(path):
        return True
    if _binary_by_magic(raw):
        return True
    return b"\x00" in raw[:_BINARY_SNIFF_BYTES]


def scan(root: Path, encoding: str) -> tuple[list[tuple[Path, int, str]], int]:
    """Return (failures, files_checked).

    Each failure is (path, byte_offset, reason) for a file that does NOT decode
    under *encoding*.
    """
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if should_audit(p):
                candidates.append(p)

    def _check(p: Path) -> tuple[Path, int, str] | bool | None:
        """None = unreadable, False = binary, True = ok, tuple = failure."""
        if _binary_by_extension(p):
            return False  # no read at all for known binary containers
        try:
            raw = p.read_bytes()
        except OSError:
            return None
        if _looks_binary(raw, p):
            return False
        try:
            raw.decode(encoding)
        except UnicodeDecodeError as e:
            return (p, e.start, e.reason)
        return True

    # Reads dominate on a cold filesystem cache and release the GIL —
    # thread-pooling them cuts the wall time several-fold on large trees.
    with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as ex:
        results = list(ex.map(_check, candidates))

    failures = [r for r in results if isinstance(r, tuple)]
    checked = sum(1 for r in results if r is True) + len(failures)
    return failures, checked


def run(target_root: Path, encoding: str | None = None) -> AuditResult:
    """Check every text file decodes under the chosen (or configured) encoding.

    Language-agnostic; runs on any project.  Encoding precedence: explicit arg ->
    the target's `#encoding` -> utf-8.  Returns an AuditResult so it slots into
    the normal audit pipeline; each undecodable file counts as one HIGH finding.
    """
    force_utf8_streams()
    root = target_root.resolve()
    enc = normalize_encoding(encoding or configured_encoding(root))

    failures, checked = scan(root, enc)

    bar = "=" * 74
    lines = [
        bar,
        f"ENCODING [{enc}] — {len(failures)} file(s) not decodable / {checked} checked",
        bar,
    ]
    if failures:
        for path, offset, reason in failures[:50]:
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            lines.append(f"  {rel}  byte {offset}: {reason}")
        if len(failures) > 50:
            lines.append(f"  ... and {len(failures) - 50} more")
    else:
        lines.append(f"  all {checked} text file(s) are valid {enc}")

    return AuditResult(
        audit_id="encoding",
        status=AuditStatus.FAIL if failures else AuditStatus.PASS,
        high=len(failures),
        stdout="\n".join(lines),
    )
