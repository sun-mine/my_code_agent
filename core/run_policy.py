"""Small, shared execution budgets for interactive runs and evaluation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RunLimits:
    max_rounds: int = 40
    max_tool_calls: int = 100
    max_seconds: float = 600
    max_tokens: int = 100000
    max_repair_attempts: int = 2

    def __post_init__(self):
        for name in ("max_rounds", "max_tool_calls", "max_seconds", "max_tokens"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must not be negative")


class RunBudgetExceeded(Exception):
    pass
