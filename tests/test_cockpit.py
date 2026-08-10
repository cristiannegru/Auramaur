"""Cockpit venue-accounting regression tests."""

from __future__ import annotations

import pytest

from auramaur.db.database import Database
from auramaur.monitoring.cockpit import _portfolio_pnl
from config.settings import Settings


@pytest.mark.asyncio
async def test_portfolio_pnl_splits_venues_and_uses_fee_basis():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            """INSERT INTO markets (id,exchange,question,last_updated)
               VALUES ('P','polymarket','Poly?',datetime('now')),
                      ('K','kalshi','Kalshi?',datetime('now'))"""
        )
        await db.execute(
            """INSERT INTO portfolio
               (market_id,exchange,side,size,avg_price,current_price,token,is_paper)
               VALUES ('P','polymarket','BUY',2,.5,.6,'YES',0),
                      ('K','kalshi','BUY',4,.55,.5,'NO',0)"""
        )
        await db.execute(
            """INSERT INTO cost_basis
               (market_id,token,size,avg_cost,total_cost,is_paper)
               VALUES ('P','YES',2,.5,1,0),
                      ('K','NO',4,.55,2.2,0)"""
        )
        await db.commit()

        result = await _portfolio_pnl(db, Settings(), 0)
        assert result["position_count"] == 2
        assert result["venue_summaries"]["polymarket"]["count"] == 1
        assert result["venue_summaries"]["polymarket"]["value"] == pytest.approx(1.2)
        assert result["venue_summaries"]["kalshi"]["count"] == 1
        assert result["venue_summaries"]["kalshi"]["cost"] == pytest.approx(2.2)
        assert result["venue_summaries"]["kalshi"]["value"] == pytest.approx(2.0)
        assert result["venue_summaries"]["kalshi"]["mark_value"] == pytest.approx(2.0)
        assert result["venue_summaries"]["kalshi"]["pnl"] == pytest.approx(-.2)
    finally:
        await db.close()
