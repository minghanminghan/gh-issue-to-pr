"""BudgetTracker: track cumulative cost and decide if next run is affordable."""

from __future__ import annotations

from tools.log import get_logger

log = get_logger(__name__)


class BudgetTracker:
    def __init__(self, max_total_usd: float, per_run_usd: float) -> None:
        self.max_total_usd = max_total_usd
        self.per_run_usd = per_run_usd
        self._spent: float = 0.0

    @property
    def spent(self) -> float:
        return self._spent

    def record(self, cost: float) -> None:
        self._spent += cost
        log.debug(f"Budget: recorded ${cost:.4f}, total spent ${self._spent:.4f}/{self.max_total_usd}")

    def can_afford_next_run(self) -> bool:
        remaining = self.max_total_usd - self._spent
        affordable = remaining >= self.per_run_usd
        log.debug(
            f"Budget check: remaining=${remaining:.4f}, per_run=${self.per_run_usd:.4f}, "
            f"affordable={affordable}"
        )
        return affordable

    def load(self, spent: float) -> None:
        """Restore spent amount from persisted state."""
        self._spent = spent
        log.debug(f"Budget loaded: spent=${self._spent:.4f}")
