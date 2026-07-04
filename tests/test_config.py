"""Config loader + result-model tests.

load_project_config is the first thing every audit run touches; these pin
its contract: defaults without a toml, deep merge with one, defaults again
(never a crash) on malformed input, and no shared mutable state between runs.
"""

from audit_code.config import DEFAULTS, load_project_config
from audit_code.models import AuditResult, AuditStatus, Finding, Severity

# ── load_project_config ──


def test_defaults_when_no_toml(tmp_path):
    cfg = load_project_config(tmp_path)
    assert cfg["audit"]["languages"] == []
    assert cfg["gate"]["mutation_kill_percent"] == 60
    assert cfg["paths"]["tests"] == ["tests"]


def test_toml_overrides_merge_deeply(tmp_path):
    (tmp_path / "audit-code.toml").write_text(
        '[audit]\nlanguages = ["go", "rust"]\n\n'
        "[gate]\nmutation_kill_percent = 80\n",
        encoding="utf-8",
    )
    cfg = load_project_config(tmp_path)
    assert cfg["audit"]["languages"] == ["go", "rust"]
    assert cfg["gate"]["mutation_kill_percent"] == 80
    # sibling keys in overridden sections keep their defaults
    assert cfg["gate"]["baseline"] == "HEAD"
    assert cfg["audit"]["profiles"] == []
    # untouched sections keep their defaults
    assert cfg["reporting"]["json"] == ""


def test_malformed_toml_falls_back_to_defaults(tmp_path):
    (tmp_path / "audit-code.toml").write_text(
        "[audit\nlanguages = not-a-string\n", encoding="utf-8"
    )
    cfg = load_project_config(tmp_path)
    assert cfg["audit"]["languages"] == DEFAULTS["audit"]["languages"]


def test_explicit_config_path_wins_over_project_toml(tmp_path):
    (tmp_path / "audit-code.toml").write_text(
        '[audit]\nlanguages = ["python"]\n', encoding="utf-8"
    )
    other = tmp_path / "elsewhere.toml"
    other.write_text('[audit]\nlanguages = ["sql"]\n', encoding="utf-8")
    cfg = load_project_config(tmp_path, str(other))
    assert cfg["audit"]["languages"] == ["sql"]


def test_loads_are_isolated_copies(tmp_path):
    """Mutating one loaded config must never leak into the next load
    (or into DEFAULTS itself)."""
    cfg1 = load_project_config(tmp_path)
    cfg1["audit"]["languages"].append("corrupted")
    cfg1["gate"]["mutation_kill_percent"] = 999
    cfg2 = load_project_config(tmp_path)
    assert cfg2["audit"]["languages"] == []
    assert cfg2["gate"]["mutation_kill_percent"] == 60
    assert DEFAULTS["audit"]["languages"] == []


# ── models: AuditResult / Finding invariants ──


def _finding(sev: Severity) -> Finding:
    return Finding(rule_id="T", severity=sev, message="m")


def test_post_init_counts_severities_from_findings():
    r = AuditResult(
        audit_id="t",
        status=AuditStatus.WARN,
        findings=[
            _finding(Severity.HIGH),
            _finding(Severity.MEDIUM),
            _finding(Severity.MEDIUM),
            _finding(Severity.INFO),
        ],
    )
    assert (r.high, r.medium, r.info) == (1, 2, 1)


def test_post_init_keeps_explicit_counts():
    r = AuditResult(
        audit_id="t",
        status=AuditStatus.FAIL,
        findings=[_finding(Severity.HIGH)],
        high=5,
    )
    assert r.high == 5  # explicit count is authoritative, not re-derived


def test_is_failure_truth_table():
    failing = (AuditStatus.FAIL, AuditStatus.CRASH, AuditStatus.ERROR)
    passing = (AuditStatus.PASS, AuditStatus.WARN, AuditStatus.SKIP)
    for status in failing:
        assert AuditResult(audit_id="t", status=status).is_failure
    for status in passing:
        assert not AuditResult(audit_id="t", status=status).is_failure
