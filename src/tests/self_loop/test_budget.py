"""Tests for BudgetTracker."""

from self_loop.budget import BudgetTracker


def test_initial_state():
    bt = BudgetTracker(max_total_usd=10.0, per_run_usd=3.0)
    assert bt.spent == 0.0
    assert bt.can_afford_next_run()


def test_can_afford_after_partial_spend():
    bt = BudgetTracker(max_total_usd=10.0, per_run_usd=3.0)
    bt.record(2.0)
    assert bt.can_afford_next_run()  # 8.0 remaining >= 3.0


def test_cannot_afford_when_budget_tight():
    bt = BudgetTracker(max_total_usd=10.0, per_run_usd=3.0)
    bt.record(8.5)
    assert not bt.can_afford_next_run()  # 1.5 remaining < 3.0


def test_cannot_afford_when_exhausted():
    bt = BudgetTracker(max_total_usd=5.0, per_run_usd=3.0)
    bt.record(5.0)
    assert not bt.can_afford_next_run()


def test_load_restores_spent():
    bt = BudgetTracker(max_total_usd=10.0, per_run_usd=3.0)
    bt.load(7.0)
    assert bt.spent == 7.0


def test_load_exactly_affordable():
    bt = BudgetTracker(max_total_usd=10.0, per_run_usd=3.0)
    bt.load(7.0)
    # 10 - 7 = 3 >= 3, so affordable
    assert bt.can_afford_next_run()


def test_record_accumulates():
    bt = BudgetTracker(max_total_usd=10.0, per_run_usd=2.0)
    bt.record(1.5)
    bt.record(2.5)
    assert bt.spent == 4.0
