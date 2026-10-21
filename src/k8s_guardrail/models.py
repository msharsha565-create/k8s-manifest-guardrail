from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    rule: str
    message: str
    resource: str


@dataclass
class VerificationResult:
    total_checks: int
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "total_checks": self.total_checks,
            "issues": [asdict(i) | {"severity": i.severity.value} for i in self.issues],
        }
