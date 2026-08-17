"""Tests for dependency-manifest discovery (audit_code.manifests).

Regression cover for the Q4 CVE-scope defect: a monorepo root whose
pyproject.toml is tool-config only ([tool.mypy]/[tool.ruff]/[tool.pytest]) with
no [project] table yielded ZERO declared dependencies, which made Q4 fall back
to scanning the whole interpreter and report every unrelated installed package
(keras, torch, vllm, jupyterlab...) as a HIGH finding.
"""

from audit_code import manifests, quality

TOOL_ONLY_PYPROJECT = """\
[tool.mypy]
ignore_missing_imports = true

[tool.ruff]
line-length = 90

[tool.pytest.ini_options]
testpaths = ["pkg/backend/tests"]
"""


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── the defect ───────────────────────────────────────────────────────────────


def test_tool_config_only_root_finds_subpackage_manifests(tmp_path):
    _write(tmp_path, "pyproject.toml", TOOL_ONLY_PYPROJECT)
    _write(
        tmp_path,
        "pkg/backend/pyproject.toml",
        '[project]\nname = "backend"\n'
        'dependencies = ["fastapi>=0.110", "SQLModel", "psycopg2-binary"]\n',
    )
    _write(tmp_path, "pkg/backend/requirements.txt", "alembic==1.13\nhttpx\n")

    # The nearest manifest alone declares nothing — the old behaviour.
    assert manifests.deps_in_dir(tmp_path) == set()
    # ...but the tree as a whole declares plenty.
    deps = manifests.declared_dependencies(tmp_path)
    assert deps == {"fastapi", "sqlmodel", "psycopg2-binary", "alembic", "httpx"}
    assert deps, "non-empty result is what prevents the environment fallback"


def test_manifest_sources_names_the_files_used(tmp_path):
    _write(tmp_path, "pyproject.toml", TOOL_ONLY_PYPROJECT)
    _write(
        tmp_path,
        "pkg/pyproject.toml",
        '[project]\nname = "p"\ndependencies = ["rich"]\n',
    )
    srcs = {p.name for p in manifests.manifest_sources(tmp_path)}
    assert "pyproject.toml" in srcs


def test_root_with_real_project_table_short_circuits(tmp_path):
    """When the nearest manifest declares deps, no downward search happens."""
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "root"\ndependencies = ["requests"]\n',
    )
    _write(
        tmp_path, "sub/pyproject.toml", '[project]\nname="s"\ndependencies=["boto3"]\n'
    )
    assert manifests.declared_dependencies(tmp_path) == {"requests"}


def test_genuinely_empty_tree_returns_empty(tmp_path):
    """Only THIS case may legitimately trigger the environment fallback."""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert manifests.declared_dependencies(tmp_path) == set()


# ── manifest formats ─────────────────────────────────────────────────────────


def test_poetry_dependencies_and_groups(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        "[tool.poetry.dependencies]\npython = '^3.12'\nflask = '^3'\n\n"
        "[tool.poetry.group.dev.dependencies]\npytest = '^8'\n",
    )
    assert manifests.declared_dependencies(tmp_path) == {"flask", "pytest"}


def test_optional_dependencies_included(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "p"\ndependencies = ["a"]\n\n'
        '[project.optional-dependencies]\ndev = ["b", "c[extra]>=2"]\n',
    )
    assert manifests.declared_dependencies(tmp_path) == {"a", "b", "c"}


def test_setup_cfg_install_requires(tmp_path):
    _write(
        tmp_path,
        "setup.cfg",
        "[options]\ninstall_requires =\n    numpy>=1.26\n    pandas\n\n"
        "[options.extras_require]\ndev =\n    pytest\n",
    )
    assert manifests.declared_dependencies(tmp_path) == {"numpy", "pandas", "pytest"}


def test_pipfile_packages(tmp_path):
    _write(
        tmp_path,
        "Pipfile",
        '[packages]\nrequests = "*"\n\n[dev-packages]\nblack = "*"\n',
    )
    assert manifests.declared_dependencies(tmp_path) == {"requests", "black"}


def test_requirements_directives_are_not_packages(tmp_path):
    _write(
        tmp_path,
        "requirements.txt",
        "# comment\n-r base.txt\n--index-url https://x\n\nDjango==5.0\n",
    )
    assert manifests.declared_dependencies(tmp_path) == {"django"}


def test_pep503_normalization(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "p"\ndependencies = ["Zope.Interface", "typing_extensions"]\n',
    )
    assert manifests.declared_dependencies(tmp_path) == {
        "zope-interface",
        "typing-extensions",
    }


def test_venv_and_node_modules_are_not_searched(tmp_path):
    _write(tmp_path, "pyproject.toml", TOOL_ONLY_PYPROJECT)
    _write(
        tmp_path,
        ".venv/somepkg/pyproject.toml",
        '[project]\nname = "x"\ndependencies = ["should-not-appear"]\n',
    )
    _write(
        tmp_path,
        "node_modules/x/requirements.txt",
        "also-should-not-appear\n",
    )
    assert manifests.declared_dependencies(tmp_path) == set()


def test_malformed_manifests_do_not_raise(tmp_path):
    _write(tmp_path, "pyproject.toml", "not [ valid = toml")
    _write(tmp_path, "sub/setup.cfg", "%%% not ini %%%")
    assert manifests.declared_dependencies(tmp_path) == set()


def test_depth_limit_is_respected(tmp_path):
    _write(tmp_path, "pyproject.toml", TOOL_ONLY_PYPROJECT)
    _write(
        tmp_path,
        "a/b/c/d/e/pyproject.toml",
        '[project]\nname = "deep"\ndependencies = ["toodeep"]\n',
    )
    assert manifests.declared_dependencies(tmp_path, max_depth=2) == set()
    assert manifests.declared_dependencies(tmp_path, max_depth=6) == {"toodeep"}


# ── the Q4 consumer ──────────────────────────────────────────────────────────


def test_quality_declared_dependencies_delegates(tmp_path):
    _write(tmp_path, "pyproject.toml", TOOL_ONLY_PYPROJECT)
    _write(
        tmp_path,
        "pkg/backend/pyproject.toml",
        '[project]\nname = "b"\ndependencies = ["fastapi"]\n',
    )
    assert quality._declared_dependencies(tmp_path) == {"fastapi"}


def test_q4_scope_is_project_not_environment(tmp_path, monkeypatch):
    """Q4's header must say 'declared', and it must name its manifests."""
    _write(tmp_path, "pyproject.toml", TOOL_ONLY_PYPROJECT)
    _write(
        tmp_path,
        "pkg/pyproject.toml",
        '[project]\nname = "b"\ndependencies = ["fastapi"]\n',
    )
    monkeypatch.setattr(quality, "_tool", lambda *a, **k: None)
    out, findings, counts = [], [], {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    quality._q4_cves(tmp_path, tmp_path, findings, counts, out)
    text = "\n".join(out)
    assert "known CVEs in declared dependencies" in text
    assert "1 declared dep(s) from:" in text
    assert "installed dependencies" not in text


def test_q4_says_so_when_it_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(quality, "_tool", lambda *a, **k: None)
    out, findings, counts = [], [], {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    quality._q4_cves(tmp_path, tmp_path, findings, counts, out)
    text = "\n".join(out)
    assert "known CVEs in installed dependencies" in text
    assert "no dependency manifest found" in text
