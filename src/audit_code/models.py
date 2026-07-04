"""Shared result models — every audit returns these."""

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    INFO = "INFO"


class AuditStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"
    CRASH = "CRASH"


@dataclass
class Finding:
    """One specific issue found by an audit."""

    rule_id: str
    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    language: str | None = None
    source: str = ""
    suggestion: str | None = None


@dataclass
class AuditResult:
    """The complete result of running one audit."""

    audit_id: str
    status: AuditStatus
    findings: list[Finding] = field(default_factory=list)
    completed: bool = True
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    tool_missing: bool = False
    high: int = 0
    medium: int = 0
    info: int = 0

    def __post_init__(self):
        if not self.high and not self.medium and not self.info:
            for f in self.findings:
                if f.severity == Severity.HIGH:
                    self.high += 1
                elif f.severity == Severity.MEDIUM:
                    self.medium += 1
                elif f.severity == Severity.INFO:
                    self.info += 1

    @property
    def is_clean(self) -> bool:
        return self.status in (AuditStatus.PASS, AuditStatus.SKIP)

    @property
    def is_failure(self) -> bool:
        return self.status in (AuditStatus.FAIL, AuditStatus.CRASH, AuditStatus.ERROR)


# Exit codes
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SETUP = 2
EXIT_CRASH = 3
