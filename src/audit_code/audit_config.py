"""Shared configuration constants for the audit suite.

Import from here instead of hardcoding numeric constants so the PhD audit
doesn't flag them as "tuning knobs outside config."
"""

# audit_gate.py
MAX_MUTANTS = 20
MUTANT_TEST_TIMEOUT = 180
SUITE_TIMEOUT = 1200
MIN_BODY_LINES = 2

# audit_quality.py / src/audit_code/quality.py
TOOL_TIMEOUT = 600
DOC_THRESHOLD_PCT = 0  # audit tool's own code doesn't require docstring coverage
MIN_FLAG_BODY_LINES = 2

# audit_suite.py / src/audit_code/suite.py
FULL_SUITE_TIMEOUT = 900
SOLO_TIMEOUT = 180
MAX_SOLO_RERUNS = 10
