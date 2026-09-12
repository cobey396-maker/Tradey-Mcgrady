"""Everything the dashboard displays, computed from a list of closed trades.

Two ideas drive the choice of metrics:

1. **Friction is the frequency tax.** Commission and slippage are charged per
   trade, not per dollar risked. Doubling trade count doubles the tax while the
   edge per trade stays the same, so `friction_report` is the first thing to look
   at on any higher-frequency variant.
2. **The trailing drawdown is what ends accounts, not the P&L.** `equity_series`
   reconstructs the Topstep-style floor alongside the equity curve so a rising
   curve that is creeping toward its floor is visible.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date

from dashboard.instruments import is_fallback, lookup, round_turn_cost
from dashboard.models import Trade


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


# ---------------------------------------------------------------------------
# Headline performance
# ---------------------------------------------------------------------------
def summary(trades: list[Trade], account_size: float) -> dict:
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "net_pnl": 0.0, "gross_pnl": 0.0, "total_costs": 0.0, "return_pct": 0.0,
            "avg_r": 0.0, "expectancy": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "largest_win": 0.0, "largest_loss": 0.0, "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0, "avg_duration_min": 0.0, "max_win_streak": 0,
            "max_loss_streak": 0, "sharpe_per_trade": 0.0, "ending_equity": account_size,
        }

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    net = sum(t.pnl for t in trades)
    gross = sum(t.gross_pnl for t in trades)

    equity = account_size
    peak = account_size
    max_dd = 0.0
    for t in trades:
        equity += t.pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    rs = [t.r_multiple for t in trades]
    win_streak = loss_streak = best_win = best_loss = 0
    for t in trades:
        if t.pnl > 0:
            win_streak, loss_streak = win_streak + 1, 0
        else:
            loss_streak, win_streak = loss_streak + 1, 0
        best_win = max(best_win, win_streak)
        best_loss = max(best_loss, loss_streak)

    r_sd = statistics.pstdev(rs) if len(rs) > 1 else 0.0

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100.0 * _safe_div(len(wins), len(trades)), 2),
        # A profit factor with no losing trades is undefined, not infinite.
        "profit_factor": round(_safe_div(gross_profit, gross_loss), 3) if gross_loss else None,
        "net_pnl": round(net, 2),
        "gross_pnl": round(gross, 2),
        "total_costs": round(gross - net, 2),
        "return_pct": round(100.0 * _safe_div(net, account_size), 3),
        "avg_r": round(_safe_div(sum(rs), len(rs)), 4),
        "expectancy": round(_safe_div(net, len(trades)), 2),
        "avg_win": round(_safe_div(gross_profit, len(wins)), 2),
        "avg_loss": round(-_safe_div(gross_loss, len(losses)), 2),
        "largest_win": round(max((t.pnl for t in trades), default=0.0), 2),
        "largest_loss": round(min((t.pnl for t in trades), default=0.0), 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(100.0 * _safe_div(max_dd, peak), 3),
        "avg_duration_min": round(_safe_div(sum(t.duration_min for t in trades), len(trades)), 1),
        "max_win_streak": best_win,
        "max_loss_streak": best_loss,
        # Per-trade Sharpe on R. Not annualised on purpose: annualising a 60-day
        # sample multiplies the sampling error along with the number.
        "sharpe_per_trade": round(_safe_div(_safe_div(sum(rs), len(rs)), r_sd), 3),
        "ending_equity": round(account_size + net, 2),
    }


# ---------------------------------------------------------------------------
# Prop-firm state: the two kill-switches
# ---------------------------------------------------------------------------
def equity_series(trades: list[Trade], prop: dict) -> dict:
    """Closed-trade equity curve with the Topstep trailing floor reconstructed.

    The floor trails on END-OF-DAY balance only and never moves down; once it
    reaches the starting balance it locks there. Real breaches are evaluated on
    live equity including unrealized P&L — a blotter of closed trades cannot see
    that, so the `min_headroom` here is an upper bound on the true headroom.
    """
    account_size = float(prop.get("account_size", 50_000.0))
    trailing = float(prop.get("trailing_drawdown", 2_000.0))
    lock_at_start = bool(prop.get("lock_at_start_balance", True))
    daily_limit = float(prop.get("daily_loss_limit", 1_000.0))

    floor = account_size - trailing
    equity = account_size
    points: list[dict] = []
    by_day: dict[date, dict] = {}
    day_start_equity = account_size
    current_day: date | None = None
    min_headroom = equity - floor
    breach_time = None
    daily_breaches: list[str] = []

    points.append({"t": None, "equity": round(equity, 2), "floor": round(floor, 2),
                   "label": "start", "pnl": 0.0})

    for t in trades:
        day = t.session_date
        if current_day is not None and day != current_day:
            # Roll the day: the floor trails on the closing balance of the day
            # that just ended, and only upward.
            floor = max(floor, equity - trailing)
            if lock_at_start:
                floor = min(floor, account_size)
            day_start_equity = equity
        current_day = day

        equity += t.pnl
        headroom = equity - floor
        if headroom < min_headroom:
            min_headroom = headroom
        if headroom <= 0 and breach_time is None:
            breach_time = t.exit_time.isoformat()

        d = by_day.setdefault(day, {"date": day.isoformat(), "pnl": 0.0, "trades": 0,
                                    "wins": 0, "start_equity": round(day_start_equity, 2),
                                    "worst_drawdown": 0.0, "limit_breached": False})
        d["pnl"] += t.pnl
        d["trades"] += 1
        d["wins"] += 1 if t.pnl > 0 else 0
        d["worst_drawdown"] = min(d["worst_drawdown"], d["pnl"])
        if -d["pnl"] >= daily_limit and not d["limit_breached"]:
            d["limit_breached"] = True
            daily_breaches.append(d["date"])

        points.append({
            "t": t.exit_time.isoformat(),
            "equity": round(equity, 2),
            "floor": round(floor, 2),
            "label": f"{t.symbol} {t.side}",
            "pnl": round(t.pnl, 2),
        })

    days = []
    for day in sorted(by_day):
        d = by_day[day]
        d["pnl"] = round(d["pnl"], 2)
        d["worst_drawdown"] = round(d["worst_drawdown"], 2)
        d["limit_used_pct"] = round(100.0 * _safe_div(max(0.0, -d["pnl"]), daily_limit), 1)
        days.append(d)

    return {
        "points": points,
        "days": days,
        "account_size": account_size,
        "daily_loss_limit": daily_limit,
        "trailing_drawdown": trailing,
        "current_equity": round(equity, 2),
        "current_floor": round(floor, 2),
        "current_headroom": round(equity - floor, 2),
        "min_headroom": round(min_headroom, 2),
        "min_headroom_pct": round(100.0 * _safe_div(min_headroom, trailing), 1),
        "mll_breached": breach_time is not None,
        "mll_breach_time": breach_time,
        "daily_limit_breaches": daily_breaches,
        "profit_target": float(prop.get("profit_target", 0.0) or 0.0),
        "target_progress_pct": round(
            100.0 * _safe_div(equity - account_size, float(prop.get("profit_target") or 0.0)), 1
        ) if prop.get("profit_target") else None,
    }


# ---------------------------------------------------------------------------
# Friction: the metric that decides whether frequent trading is viable
# ---------------------------------------------------------------------------
def friction_report(trades: list[Trade], backtest_cfg: dict, target_r: float) -> dict:
    """Quantify what trading costs, and what win rate that cost demands.

    Breakeven win rate with friction f (expressed in R) and target m:

        p * (m - f) = (1 - p) * (1 + f)   =>   p = (1 + f) / (1 + m)

    With f = 0 this is the familiar 1/(1+m). Every unit of friction raises the
    bar, and it raises it *faster* for small targets — which is exactly the
    trade-off a higher-frequency, tighter-stop variant walks into.
    """
    commission = float(backtest_cfg.get("commission_per_contract", 1.24))
    slippage = float(backtest_cfg.get("slippage_ticks", 1))

    per_symbol: dict[str, dict] = {}
    total_cost = 0.0
    total_contracts = 0
    frictions_in_r: list[float] = []

    for t in trades:
        cost = round_turn_cost(t.symbol, commission, slippage) * max(1, t.contracts)
        total_cost += cost
        total_contracts += max(1, t.contracts)
        risk = t.risk_dollars
        if risk > 0:
            frictions_in_r.append(cost / risk)
        s = per_symbol.setdefault(t.symbol, {
            "symbol": t.symbol, "trades": 0, "contracts": 0, "cost": 0.0,
            "net_pnl": 0.0, "gross_pnl": 0.0, "wins": 0,
            "round_turn_per_contract": round(round_turn_cost(t.symbol, commission, slippage), 2),
            "unknown_spec": is_fallback(t.symbol),
        })
        s["trades"] += 1
        s["contracts"] += max(1, t.contracts)
        s["cost"] += cost
        s["net_pnl"] += t.pnl
        s["gross_pnl"] += t.gross_pnl
        s["wins"] += 1 if t.pnl > 0 else 0

    for s in per_symbol.values():
        s["cost"] = round(s["cost"], 2)
        s["net_pnl"] = round(s["net_pnl"], 2)
        s["gross_pnl"] = round(s["gross_pnl"], 2)
        s["win_rate"] = round(100.0 * _safe_div(s["wins"], s["trades"]), 1)
        s["cost_share_pct"] = round(
            100.0 * _safe_div(s["cost"], abs(s["gross_pnl"])), 1) if s["gross_pnl"] else None

    avg_friction_r = _safe_div(sum(frictions_in_r), len(frictions_in_r))
    gross = sum(t.gross_pnl for t in trades)
    net = sum(t.pnl for t in trades)

    return {
        "commission_per_side": commission,
        "slippage_ticks": slippage,
        "total_cost": round(total_cost, 2),
        "total_contracts": total_contracts,
        "cost_per_trade": round(_safe_div(total_cost, len(trades)), 2),
        "gross_pnl": round(gross, 2),
        "net_pnl": round(net, 2),
        # What share of the gross edge the costs consumed. >50% means the broker
        # is the majority shareholder in the strategy.
        "cost_share_of_gross_pct": round(100.0 * _safe_div(total_cost, abs(gross)), 1) if gross else None,
        "avg_friction_r": round(avg_friction_r, 4),
        "target_r": target_r,
        "breakeven_win_rate": round(100.0 * _safe_div(1.0, 1.0 + target_r), 2),
        "breakeven_win_rate_with_friction": round(
            100.0 * _safe_div(1.0 + avg_friction_r, 1.0 + target_r), 2),
        "actual_win_rate": round(
            100.0 * _safe_div(sum(1 for t in trades if t.pnl > 0), len(trades)), 2),
        "per_symbol": sorted(per_symbol.values(), key=lambda s: -s["net_pnl"]),
    }


def frequency_report(trades: list[Trade], daily_limit: float) -> dict:
    """Trade-rate stats — the thing you change when you go higher frequency, and
    the thing that decides how much per-trade risk the daily limit can carry."""
    by_day: dict[date, list[Trade]] = defaultdict(list)
    for t in trades:
        by_day[t.session_date].append(t)

    counts = [len(v) for v in by_day.values()]
    risks = [t.risk_dollars for t in trades if t.risk_dollars > 0]
    avg_risk = _safe_div(sum(risks), len(risks))
    avg_per_day = _safe_div(sum(counts), len(counts))

    # How many average-sized losers fit inside the daily limit. Under ~4 the
    # limit stops being a circuit breaker and becomes a routine stop-out.
    losers_to_limit = _safe_div(daily_limit, avg_risk)

    hours: dict[int, dict] = {}
    for t in trades:
        h = hours.setdefault(t.entry_time.hour, {"hour": t.entry_time.hour, "trades": 0,
                                                 "pnl": 0.0, "wins": 0})
        h["trades"] += 1
        h["pnl"] += t.pnl
        h["wins"] += 1 if t.pnl > 0 else 0
    for h in hours.values():
        h["pnl"] = round(h["pnl"], 2)
        h["win_rate"] = round(100.0 * _safe_div(h["wins"], h["trades"]), 1)

    reasons: dict[str, dict] = {}
    for t in trades:
        r = reasons.setdefault(t.exit_reason, {"reason": t.exit_reason, "trades": 0, "pnl": 0.0})
        r["trades"] += 1
        r["pnl"] += t.pnl
    for r in reasons.values():
        r["pnl"] = round(r["pnl"], 2)

    return {
        "trading_days": len(by_day),
        "trades_per_day_avg": round(avg_per_day, 2),
        "trades_per_day_max": max(counts, default=0),
        "avg_risk_dollars": round(avg_risk, 2),
        "avg_hold_minutes": round(
            _safe_div(sum(t.duration_min for t in trades), len(trades)), 1),
        "losers_to_daily_limit": round(losers_to_limit, 1),
        "expected_losers_per_day": round(
            avg_per_day * (1.0 - _safe_div(sum(1 for t in trades if t.pnl > 0), len(trades))), 2
        ) if trades else 0.0,
        "by_hour": [hours[k] for k in sorted(hours)],
        "by_exit_reason": sorted(reasons.values(), key=lambda r: -r["trades"]),
        "r_histogram": r_histogram(trades),
    }


def r_histogram(trades: list[Trade], bin_width: float = 0.25) -> list[dict]:
    """Distribution of realised R. A frequent-trading system should show a tight
    cluster at the target and at -1R; a long left tail means stops are slipping."""
    if not trades:
        return []
    buckets: dict[int, int] = defaultdict(int)
    for t in trades:
        idx = math.floor(t.r_multiple / bin_width)
        buckets[idx] = buckets[idx] + 1
    lo, hi = min(buckets), max(buckets)
    return [{"r": round(i * bin_width, 3), "count": buckets.get(i, 0)} for i in range(lo, hi + 1)]


def rolling_metrics(trades: list[Trade], window: int = 20) -> list[dict]:
    """Rolling win rate and expectancy — degradation shows up here first."""
    out = []
    for i in range(len(trades)):
        lo = max(0, i - window + 1)
        chunk = trades[lo:i + 1]
        wins = sum(1 for t in chunk if t.pnl > 0)
        out.append({
            "i": i + 1,
            "t": trades[i].exit_time.isoformat(),
            "win_rate": round(100.0 * _safe_div(wins, len(chunk)), 1),
            "expectancy_r": round(_safe_div(sum(c.r_multiple for c in chunk), len(chunk)), 3),
        })
    return out
