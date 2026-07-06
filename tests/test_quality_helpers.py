"""Tests for quality.py's low-level helpers (_run subprocess wrapper, _def_spans)."""

import sys

from audit_code import quality as q


def test_run_success_captures_output(tmp_path):
    rc, out = q._run([sys.executable, "-c", "print('hello-run')"], tmp_path)
    assert rc == 0 and "hello-run" in out


def test_run_missing_binary_returns_minus_two(tmp_path):
    rc, out = q._run(["definitely-not-a-real-binary-xyz"], tmp_path)
    assert rc == -2 and "failed to launch" in out


def test_run_timeout_returns_minus_one(tmp_path):
    rc, out = q._run(
        [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, timeout=1
    )
    assert rc == -1 and "timed out" in out


def test_def_spans_extracts_functions_and_methods(tmp_path):
    src = tmp_path / "sample.py"
    src.write_text(
        "def top():\n"
        "    return 1\n"
        "\n"
        "class C:\n"
        "    def method(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    spans = q._def_spans(src)
    names = {s[0] for s in spans}
    assert "top" in names and "C.method" in names


def test_def_spans_syntax_error_returns_empty(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    assert q._def_spans(bad) == []


# ── Q5 coverage cache ────────────────────────────────────────────────────────


def _mini_project(root):
    (root / "src").mkdir()
    (root / "src" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_m.py").write_text("def test_x():\n    assert 1\n", "utf-8")


def test_q5_fingerprint_changes_when_source_changes(tmp_path):
    _mini_project(tmp_path)
    fp1 = q._q5_fingerprint(tmp_path, tmp_path / "tests")
    (tmp_path / "src" / "m.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    fp2 = q._q5_fingerprint(tmp_path, tmp_path / "tests")
    assert fp1 != fp2


def test_q5_fingerprint_stable_when_unchanged(tmp_path):
    _mini_project(tmp_path)
    fp1 = q._q5_fingerprint(tmp_path, tmp_path / "tests")
    fp2 = q._q5_fingerprint(tmp_path, tmp_path / "tests")
    assert fp1 == fp2


def test_q5_fingerprint_tracks_pytest_args(tmp_path):
    _mini_project(tmp_path)
    a = q._q5_fingerprint(tmp_path, tmp_path / "tests", "-p no:logfire")
    b = q._q5_fingerprint(tmp_path, tmp_path / "tests", "-x")
    assert a != b


def test_q5_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDIT_NO_Q5_CACHE", raising=False)
    _mini_project(tmp_path)
    # Isolate the cache dir to this test's tmp so real caches are untouched.
    monkeypatch.setattr(q, "_q5_cache_dir", lambda root: tmp_path / "cache")
    data = tmp_path / ".coverage"
    data.write_bytes(b"fake-coverage-data")
    fp = q._q5_fingerprint(tmp_path, tmp_path / "tests")

    assert q._q5_cache_load(tmp_path, fp) is None  # cold
    q._q5_cache_save(tmp_path, fp, data)
    hit = q._q5_cache_load(tmp_path, fp)
    assert hit is not None and hit.read_bytes() == b"fake-coverage-data"


def test_q5_cache_miss_on_fingerprint_change(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDIT_NO_Q5_CACHE", raising=False)
    monkeypatch.setattr(q, "_q5_cache_dir", lambda root: tmp_path / "cache")
    data = tmp_path / ".coverage"
    data.write_bytes(b"x")
    q._q5_cache_save(tmp_path, "fingerprint-A", data)
    assert q._q5_cache_load(tmp_path, "fingerprint-A") is not None
    assert q._q5_cache_load(tmp_path, "fingerprint-B") is None


def test_q5_cache_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "_q5_cache_dir", lambda root: tmp_path / "cache")
    data = tmp_path / ".coverage"
    data.write_bytes(b"x")
    q._q5_cache_save(tmp_path, "k", data)
    monkeypatch.setenv("AUDIT_NO_Q5_CACHE", "1")
    assert q._q5_cache_load(tmp_path, "k") is None  # forced miss


def test_q5_cache_dir_is_deterministic_and_per_root(tmp_path):
    # Exercises the real _q5_cache_dir (other cache tests monkeypatch it out).
    d1 = q._q5_cache_dir(tmp_path)
    d2 = q._q5_cache_dir(tmp_path)
    assert d1 == d2
    assert "audit_q5_cache" in str(d1)
    other = tmp_path / "sub"
    other.mkdir()
    assert q._q5_cache_dir(other) != d1
