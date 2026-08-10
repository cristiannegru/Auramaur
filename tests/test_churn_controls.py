"""Anti-churn controls from the 2026-08-06..09 exit-then-rebuy incident.

The revived exit path sold positions the strategies still liked; they
rebought within hours (sometimes above their own sell price) and the next
exit realized the spread again. Separately, fresh entries on wide-spread
books were stop-lossed within a minute of entry because the first mark
carried the spread as an instant paper loss. These tests pin the three
controls: the re-entry cooldown, the entry grace for mark-driven stops,
and the reconciler lookup ordering that stops the stub re-ingest loop.
"""

from types import SimpleNamespace

import pytest

from auramaur.db.database import Database
from auramaur.risk.checks import check_reentry_cooldown


@pytest.mark.asyncio
async def test_reentry_cooldown_blocks_inside_window_and_fails_open():
    blocked = await check_reentry_cooldown(2.0, cooldown_hours=24.0)
    assert not blocked.passed and "cooldown" in blocked.reason

    allowed = await check_reentry_cooldown(30.0, cooldown_hours=24.0)
    assert allowed.passed

    # No recorded exit activity (or a failed lookup) must PASS: this is an
    # anti-churn control, not a safety gate.
    assert (await check_reentry_cooldown(None, cooldown_hours=24.0)).passed
    # Zero disables.
    assert (await check_reentry_cooldown(0.1, cooldown_hours=0.0)).passed


@pytest.mark.asyncio
async def test_entry_grace_defers_stop_loss_but_not_forever(tmp_path):
    """A position younger than the grace window is not stop-lossed; the same
    mark after the window (or with grace disabled) is."""
    from auramaur.risk.portfolio import PortfolioTracker

    db = Database(str(tmp_path / "grace.db"))
    await db.connect()
    try:
        await db.execute(
            """INSERT INTO portfolio (market_id, exchange, side, size,
                   avg_price, current_price, category, token, token_id, is_paper)
               VALUES ('M-fresh', 'polymarket', 'BUY', 50, 0.14, 0.095,
                       'politics', 'YES', 't', 0)""")
        # Fresh cost_basis row: entered "just now".
        await db.execute(
            """INSERT INTO cost_basis (market_id, token, size, avg_cost,
                   total_cost, is_paper, updated_at)
               VALUES ('M-fresh', 'YES', 50, 0.14, 7.0, 0, datetime('now'))""")

        settings = SimpleNamespace(
            is_live=True,
            execution=SimpleNamespace(
                stop_loss_pct=30.0, exit_entry_grace_minutes=30.0,
                profit_target_pct=50.0, profit_target_early_pct=75.0,
                profit_target_late_pct=25.0,
                profit_target_early_fraction_remaining=0.5,
                profit_target_late_fraction_remaining=0.1,
                trailing_stop_activation_pct=12.0,
                trailing_stop_giveback_fraction=0.45,
                edge_erosion_min_pct=0.0, time_decay_hours=0,
                dust_sweep_enabled=False, dust_max_notional=0.0,
                dust_min_age_hours=0,
                free_winners_enabled=False,
                free_winners_max_upside_pct=0.0, free_winners_min_hours=0,
                exit_hold_sample_seconds=3600,
                exit_decision_retention_days=30),
            arbitrage=SimpleNamespace(exchange_fees={}),
        )
        tracker = PortfolioTracker(db, settings)
        discovery = SimpleNamespace(
            get_market=_market_returning(0.095))

        exits = await tracker.check_exits(settings, discovery,
                                          exchange="polymarket")
        assert exits == [], "a -32% mark one minute after entry must not stop"

        # Age the entry past the window: now the stop fires.
        await db.execute(
            "UPDATE cost_basis SET updated_at = datetime('now', '-45 minutes')"
            " WHERE market_id = 'M-fresh'")
        exits = await tracker.check_exits(settings, discovery,
                                          exchange="polymarket")
        assert [(p.market_id, r.value) for p, r in exits] == [
            ("M-fresh", "STOP_LOSS")]
    finally:
        await db.close()


def _market_returning(price):
    async def get_market(market_id):
        return SimpleNamespace(
            id=market_id, outcome_yes_price=price, outcome_no_price=1 - price,
            end_date=None, fee_rate=None, fees_enabled=None,
            clob_token_yes="tok-yes", clob_token_no="tok-no",
            question="q", category="politics", volume=1000.0,
            liquidity=1000.0, fractional_trading_enabled=False)
    return get_market


@pytest.mark.asyncio
async def test_condition_lookup_prefers_the_real_row_over_the_stub(tmp_path):
    """Once the real market row exists, recovery must stop re-firing: the
    condition_id lookup returns the real id, not the legacy stub."""
    from auramaur.broker.reconciler import PositionReconciler

    db = Database(str(tmp_path / "stub.db"))
    await db.connect()
    try:
        cond = "0x" + "ab" * 31
        stub = cond[:16]
        await db.execute(
            """INSERT INTO markets (id, condition_id, question, category,
                   active, last_updated)
               VALUES (?, ?, 'q', 'politics', 1, datetime('now'))""",
            (stub, cond))
        await db.execute(
            """INSERT INTO markets (id, condition_id, question, category,
                   active, last_updated)
               VALUES ('123456', ?, 'q real', 'politics', 1,
                       datetime('now'))""", (cond,))

        rec = PositionReconciler.__new__(PositionReconciler)
        rec._db = db
        found = await rec._find_market_id(cond, "q real", "")
        assert found == "123456"
    finally:
        await db.close()
