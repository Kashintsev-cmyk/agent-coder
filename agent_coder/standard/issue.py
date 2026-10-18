"""Issue: единица отклонения от стандарта bots/ (публичный контракт)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Severity = Literal["major", "minor"]


@dataclass(frozen=True, slots=True)
class Issue:
    """Одно отклонение. Поля 1:1 совпадают с JSON tool'а."""

    path: str
    severity: Severity
    issue: str
    expected: str
    found: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Issue:
        return cls(
            path=str(data["path"]),
            severity=data["severity"],
            issue=str(data["issue"]),
            expected=str(data["expected"]),
            found=str(data["found"]),
        )
