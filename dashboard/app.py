"""FastAPI app for the trading dashboard.

Read-only, by construction: there is no route that places, modifies or cancels an
order, and no route that writes to the bot's config. If you want to change what
the bot does, change config.yaml — not this.

Routes
------
GET /                    the single-page UI
GET /api/overview        config + headline stats + prop-firm state (one call)
GET /api/trades          blotter rows, newest first, with filters
GET /api/equity          equity curve + trailing floor + per-day breakdown
GET /api/friction        cost analysis + breakeven win-rate maths
GET /api/frequency       trade-rate, hour-of-day, R distribution
GET /api/live            open positions + kill-switch flags (if reported)
GET /api/health          liveness probe
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard import metrics
from dashboard.models import utcnow
from dashboard.store import DEFAULT_BLOTTER, DEFAULT_CONFIG, DEFAULT_STATE, TradeStore

STATIC_DIR = Path(__file__).parent / "static"


def create_app(blotter: str = DEFAULT_BLOTTER, config: str = DEFAULT_CONFIG,
               state: str = DEFAULT_STATE, demo: bool = False) -> FastAPI:
    store = TradeStore(blotter_path=blotter, state_path=state, config_path=config, demo=demo)

    app = FastAPI(
        title="TradeyMcGrady dashboard",
        description="Read-only monitor for the futures prop-firm day-trading bot.",
        version="1.0.0",
    )
    app.state.store = store

    def _filtered(symbol: str | None, since: str | None, limit: int | None):
        trades = store.trades()
        if symbol:
            wanted = {s.strip().upper() for s in symbol.split(",") if s.strip()}
            trades = [t for t in trades if t.symbol in wanted]
        if since:
            trades = [t for t in trades if t.exit_time.date().isoformat() >= since]
        if limit:
            trades = trades[-limit:]
        return trades

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "time": utcnow().isoformat(), "trades": len(store.trades())}

    @app.get("/api/overview")
    def overview() -> dict:
        ctx = store.context()
        trades = store.trades()
        prop = ctx.prop
        eq = metrics.equity_series(trades, prop)
        fr = metrics.friction_report(trades, ctx.backtest, ctx.target_r)
        return {
            "generated_at": utcnow().isoformat(),
            "config": ctx.describe(),
            "source": store.source(),
            "summary": metrics.summary(trades, float(prop["account_size"])),
            "prop": {
                "name": prop.get("name"),
                "account_size": eq["account_size"],
                "current_equity": eq["current_equity"],
                "current_floor": eq["current_floor"],
                "current_headroom": eq["current_headroom"],
                "min_headroom": eq["min_headroom"],
                "min_headroom_pct": eq["min_headroom_pct"],
                "daily_loss_limit": eq["daily_loss_limit"],
                "trailing_drawdown": eq["trailing_drawdown"],
                "mll_breached": eq["mll_breached"],
                "mll_breach_time": eq["mll_breach_time"],
                "daily_limit_breaches": eq["daily_limit_breaches"],
                "profit_target": eq["profit_target"],
                "target_progress_pct": eq["target_progress_pct"],
                "today": eq["days"][-1] if eq["days"] else None,
            },
            "friction": {
                "total_cost": fr["total_cost"],
                "cost_per_trade": fr["cost_per_trade"],
                "cost_share_of_gross_pct": fr["cost_share_of_gross_pct"],
                "avg_friction_r": fr["avg_friction_r"],
                "breakeven_win_rate": fr["breakeven_win_rate"],
                "breakeven_win_rate_with_friction": fr["breakeven_win_rate_with_friction"],
                "actual_win_rate": fr["actual_win_rate"],
            },
            "live": store.live_state(),
        }

    @app.get("/api/trades")
    def trades_route(
        symbol: str | None = Query(None, description="comma-separated symbols"),
        since: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
        limit: int = Query(500, ge=1, le=5000),
    ) -> dict:
        trades = _filtered(symbol, since, limit)
        return {
            "count": len(trades),
            "trades": [t.to_dict() for t in reversed(trades)],
        }

    @app.get("/api/equity")
    def equity_route(symbol: str | None = None, since: str | None = None) -> dict:
        ctx = store.context()
        data = metrics.equity_series(_filtered(symbol, since, None), ctx.prop)
        data["rolling"] = metrics.rolling_metrics(_filtered(symbol, since, None))
        return data

    @app.get("/api/friction")
    def friction_route(symbol: str | None = None, since: str | None = None) -> dict:
        ctx = store.context()
        return metrics.friction_report(_filtered(symbol, since, None), ctx.backtest, ctx.target_r)

    @app.get("/api/frequency")
    def frequency_route(symbol: str | None = None, since: str | None = None) -> dict:
        ctx = store.context()
        return metrics.frequency_report(
            _filtered(symbol, since, None), float(ctx.prop["daily_loss_limit"])
        )

    @app.get("/api/live")
    def live_route() -> dict:
        return store.live_state()

    @app.get("/")
    def index() -> FileResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():  # pragma: no cover - only if the package is broken
            raise HTTPException(500, "dashboard static assets are missing")
        return FileResponse(page)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
