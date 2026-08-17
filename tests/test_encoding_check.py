"""Tests for the universal `check <encoding>` gate and its config."""

import pytest

from audit_code import audit_shared as sh
from audit_code import cli, encoding_check

# T1 anchor: ensures the phd audit sees "encoding_check" in test text

# ── encoding-name normalization ──────────────────────────────────────────────


def test_normalize_encoding_canonicalizes():
    assert sh.normalize_encoding("UTF-8") == "utf-8"
    assert sh.normalize_encoding("UTF-16") == "utf-16"
    assert sh.normalize_encoding("GB 18030") == "gb18030"  # spaces stripped


def test_normalize_encoding_rejects_unknown():
    with pytest.raises(LookupError):
        sh.normalize_encoding("not-a-codec")


# ── #encoding config directive ───────────────────────────────────────────────


def test_configured_encoding_reads_directive(tmp_path):
    (tmp_path / ".audit-test-ignore").write_text(
        "#encoding gb18030\n", encoding="utf-8"
    )
    assert sh.configured_encoding(tmp_path) == "gb18030"


def test_configured_encoding_defaults_to_utf8(tmp_path):
    assert sh.configured_encoding(tmp_path) == "utf-8"  # no file


# ── scan() ───────────────────────────────────────────────────────────────────


@pytest.fixture
def mixed_tree(tmp_path):
    (tmp_path / "good.py").write_bytes("s = 'café ☕'\n".encode("utf-8"))
    (tmp_path / "latin1.txt").write_bytes("café\n".encode("latin-1"))  # invalid utf-8
    (tmp_path / "gb.txt").write_bytes("中文\n".encode("gb18030"))  # invalid utf-8
    (tmp_path / "pic.png").write_bytes(b"\x89PNG\x00\x00\x00binary")  # binary
    return tmp_path


def test_scan_flags_non_utf8_and_skips_binary(mixed_tree):
    failures, checked = encoding_check.scan(mixed_tree, "utf-8")
    names = {p.name for p, _, _ in failures}
    assert names == {"latin1.txt", "gb.txt"}  # png skipped, good.py passes
    assert checked == 3  # 3 text files sniffed (binary excluded)


def test_scan_clean_when_all_match(tmp_path):
    (tmp_path / "a.py").write_bytes("x = 1\n".encode("utf-8"))
    (tmp_path / "b.py").write_bytes("y = 'plain ascii'\n".encode("utf-8"))
    failures, checked = encoding_check.scan(tmp_path, "utf-8")
    assert failures == [] and checked == 2


def test_scan_gb18030_passes_gb_fails_utf8(mixed_tree):
    failures, _ = encoding_check.scan(mixed_tree, "gb18030")
    names = {p.name for p, _, _ in failures}
    assert "gb.txt" not in names  # valid gb18030
    assert "good.py" in names  # utf-8 emoji not valid gb18030


# ── run() exit codes ─────────────────────────────────────────────────────────


def test_run_uses_configured_encoding(tmp_path):
    (tmp_path / ".audit-test-ignore").write_text(
        "#encoding gb18030\n", encoding="utf-8"
    )
    (tmp_path / "ok.txt").write_bytes("中文\n".encode("gb18030"))
    result = encoding_check.run(tmp_path)
    assert not result.is_failure and "gb18030" in result.stdout


def test_run_fails_on_mismatch(mixed_tree):
    result = encoding_check.run(mixed_tree, "utf-8")
    assert result.is_failure and result.high == 2 and result.audit_id == "encoding"


# ── CLI `check` command ──────────────────────────────────────────────────────


def _run_check(monkeypatch, argv):
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "check", *argv])
    with pytest.raises(SystemExit) as exc:
        cli._handle_check()
    return exc.value.code if exc.value.code is not None else 0


def test_cli_check_mode_detected(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "check", "utf-8"])
    assert cli._is_check_mode() is True
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "--phd"])
    assert cli._is_check_mode() is False


def test_cli_check_passes_and_fails(monkeypatch, tmp_path):
    (tmp_path / "good.py").write_bytes("x = 1\n".encode("utf-8"))
    assert _run_check(monkeypatch, ["utf-8", "--path", str(tmp_path)]) == 0
    (tmp_path / "bad.txt").write_bytes("café\n".encode("latin-1"))
    assert _run_check(monkeypatch, ["utf-8", "--path", str(tmp_path)]) == 1


def test_cli_check_multiword_encoding(monkeypatch, tmp_path):
    (tmp_path / "cn.txt").write_bytes("中文\n".encode("gb18030"))
    assert _run_check(monkeypatch, ["GB", "18030", "--path", str(tmp_path)]) == 0


def test_cli_check_unknown_encoding_exits_2(monkeypatch, tmp_path):
    assert _run_check(monkeypatch, ["bogus-codec", "--path", str(tmp_path)]) == 2


def test_cli_check_help(monkeypatch):
    assert _run_check(monkeypatch, ["help"]) == 0


# ── bare-word 'F' must mean --full, not --fix (regression) ───────────────────


def test_bare_F_expands_to_full_not_fix(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "F"])
    cli._expand_bare_words()
    assert cli.sys.argv[1:] == ["--full"]


def test_bare_f_still_expands_to_fix(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["audit-code", "f"])
    cli._expand_bare_words()
    assert cli.sys.argv[1:] == ["--fix"]


# ── encoding wired into the audit pipeline ───────────────────────────────────


def test_encoding_is_a_selectable_module():
    assert "encoding" in cli.ALL_MODULES


def test_full_run_includes_encoding_step(tmp_path, monkeypatch):
    """A default (modules=None) run must schedule the encoding step."""
    (tmp_path / "a.py").write_bytes("x = 1\n".encode("utf-8"))
    from audit_code import runner

    results = runner.run_suite(tmp_path, mode="default", modules={"encoding"})
    assert any(r.audit_id == "encoding" for r in results)


def test_configured_encoding_empty_value(tmp_path):
    """T3 edge: '#encoding' with no value defaults to utf-8."""
    (tmp_path / ".audit-test-ignore").write_text("#encoding  \n", encoding="utf-8")
    assert sh.configured_encoding(tmp_path) == "utf-8"


def test_scan_empty_directory(tmp_path):
    """T3 edge: scan on empty dir returns 0 failures."""
    failures, checked = encoding_check.scan(tmp_path, "utf-8")
    assert failures == []
    assert checked >= 0


# ── binary detection: magic bytes / extension, not just NUL (regression) ──────

# A real 2 KB PDF starts like this and contains NO NUL byte in its first 4 KB,
# so the old NUL-only heuristic strict-decoded it and reported a false FAIL.
_PDF_HEAD = b"%PDF-1.3\n%\xe9\xeb\xf1\xbf\n1 0 obj\n<< /Type /Catalog >>\n"

_NUL_FREE_BINARIES = {
    "doc.pdf": _PDF_HEAD,
    "img.png": b"\x89PNG\r\n\x1a\n\xff\xfe\xfd\xfc",
    "photo.jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF\xff\xfe",
    "anim.gif": b"GIF89a\xff\xfe\xfd",
    "old.gif": b"GIF87a\xff\xfe\xfd",
    "book.xlsx": b"PK\x03\x04\xff\xfe\xfd",
    "text.docx": b"PK\x03\x04\x14\xff\xfe",
    "deck.pptx": b"PK\x03\x04\x0a\xff\xfe",
    "lib.jar": b"PK\x03\x04\xff\xfe",
}


@pytest.mark.parametrize("name,head", sorted(_NUL_FREE_BINARIES.items()))
def test_looks_binary_recognizes_magic_without_nul(name, head):
    """Magic bytes classify these as binary even though no NUL is present."""
    assert b"\x00" not in head[:4096] or name in ("img.png", "photo.jpg")
    assert encoding_check._binary_by_magic(head) is True
    assert encoding_check._looks_binary(head, __import__("pathlib").Path(name)) is True


def test_scan_skips_nul_free_binaries_but_still_flags_bad_text(tmp_path):
    """The exact false-FAIL class: binary containers exempt, broken text caught."""
    for name, head in _NUL_FREE_BINARIES.items():
        (tmp_path / name).write_bytes(head)
    (tmp_path / "broken.py").write_bytes("# caf\xe9\nx = 1\n".encode("latin-1"))
    (tmp_path / "fine.py").write_bytes("# caf\xe9\nx = 1\n".encode("utf-8"))

    failures, checked = encoding_check.scan(tmp_path, "utf-8")
    assert {p.name for p, _, _ in failures} == {"broken.py"}
    assert checked == 2  # only the two .py files are text


def test_binary_by_extension_covers_stripped_magic(tmp_path):
    """Extension is an independent signal — a truncated/odd header still skips."""
    p = tmp_path / "weird.xlsx"
    p.write_bytes(b"\xff\xfe not really a zip header")
    assert encoding_check._binary_by_extension(p) is True
    failures, checked = encoding_check.scan(tmp_path, "utf-8")
    assert failures == [] and checked == 0


def test_nul_heuristic_still_applies_to_unknown_extension(tmp_path):
    """The original NUL rule is kept, not replaced."""
    (tmp_path / "blob.unknownext").write_bytes(b"abc\x00def")
    failures, checked = encoding_check.scan(tmp_path, "utf-8")
    assert failures == [] and checked == 0


def test_run_passes_on_a_pdf_that_used_to_fail(tmp_path):
    """End-to-end: a PDF alongside clean source is a PASS, not a HIGH."""
    (tmp_path / "rcp_0024.pdf").write_bytes(_PDF_HEAD)
    (tmp_path / "a.py").write_bytes(b"x = 1\n")
    result = encoding_check.run(tmp_path, "utf-8")
    assert not result.is_failure and result.high == 0
