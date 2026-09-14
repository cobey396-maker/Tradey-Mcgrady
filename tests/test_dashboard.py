"""Tests for the dashboard's data layer and API.

The maths tests use hand-computed expectations, not the implementation's own
output — a metric test that asserts what the code already returns proves nothing.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dashboard import metrics
from dashboard.app import create_app
from dashboard.instruments import lookup, round_turn_cost
from dashboard.models import Trade, load_blotter, parse_row
from dashboard.store import BotContext, TradeStore, generate_demo_trades


def make_trade(symbol="MES", pnl=100.0, r=1.0, direction=1, contracts=2,
               entry=5600.0, stop_ticks=40, day=1, hour=10, minutes=30,
               reason="target") -> Trade:
    inst = lookup(symbol)
    stop_dist = stop_ticks * inst.tick_size
    entry_time = datetime(2026, 6, day, hour, 0)
    exit_price = entry + direction * r * stop_dist
    return Trade(
        symbol=symbol, entry_time=entry_time, exit_time=entry_time + timedelta(minutes=minutes),
        direction=direction, entry_price=entry, exit_price=exit_price, contracts=contracts,
        stop=entry - direction * stop_dist, target=entry + direction * stop_dist,
        pnl=pnl, r_multiple=r, exit_reason=reason,
    )


# ---------------------------------------------------------------- instruments
def test_point_value_and_dollars():
    mes = lookup("MES")
    assert mes.point_value == 5.0              # $1.25 per 0.25 tick
    assert mes.dollars(2.0, contracts=3) == 30.0
    assert mes.ticks(2.0) == 8


def test_round_turn_cost_is_two_sides_of_each():
    # MES: 2 x $1.24 commission + 2 x 1 tick x $1.25 = $4.98
    assert round_turn_cost("MES", 1.24, 1) == pytest.approx(4.98)
    # MNQ has the same commission but a cheaper tick, so friction is lower in $
    assert round_turn_cost("MNQ", 1.24, 1) == pytest.approx(3.48)


def test_unknown_symbol_falls_back_without_raising():
    assert lookup("ZZZZ").tick_size == 0.25


# --------------------------------------------------------------------- models
def test_parse_row_handles_both_direction_spellings():
    base = {"entry_time": "2026-06-01 09:40:00", "exit_time": "2026-06-01 10:05:00",
            "entry_price": "5600", "exit_price": "5610", "contracts": "2",
            "stop": "5590", "target": "5615", "pnl": "95.02", "r_multiple": "0.95",
            "exit_reason": "target"}
    assert parse_row({**base, "direction": "long"}).direction == 1
    assert parse_row({**base, "direction": "short"}).direction == -1
    assert parse_row({**base, "direction": "-1"}).direction == -1


def test_gross_pnl_minus_net_is_the_cost_paid():
    # 2 MES contracts, 10 points of favourable move = 10 * $5 * 2 = $100 gross.
    t = make_trade(pnl=90.04, r=1.0, contracts=2, entry=5600.0, stop_ticks=40)
    assert t.gross_pnl == pytest.approx(100.0)
    assert t.gross_pnl - t.pnl == pytest.approx(9.96)   # 2 x $4.98 round turn


def test_risk_dollars_matches_stop_distance():
    # 40 ticks x $1.25 x 3 contracts = $150 planned risk.
    t = make_trade(contracts=3, stop_ticks=40)
    assert t.risk_dollars == pytest.approx(150.0)


def test_load_blotter_skips_corrupt_rows(tmp_path):
    path = tmp_path / "trades.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entry_time", "exit_time", "direction", "entry_price", "exit_price",
                    "contracts", "stop", "target", "pnl", "r_multiple", "exit_reason"])
        w.writerow(["2026-06-01 09:40:00", "2026-06-01 10:05:00", "long", "5600", "5610",
                    "2", "5590", "5615", "95.02", "0.95", "target"])
        w.writerow(["", "", "long", "", "", "", "", "", "", "", ""])   # half-written row
    trades = load_blotter(path)
    assert len(trades) == 1


def test_load_blotter_rejects_a_file_with_the_wrong_columns(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("a,b,c\n1,2,3\n")
    with pytest.raises(ValueError, match="missing columns"):
        load_blotter(path)


def test_missing_blotter_is_empty_not_an_error(tmp_path):
    assert load_blotter(tmp_path / "nope.csv") == []


# -------------------------------------------------------------------- metrics
def test_summary_on_a_known_set():
    trades = [make_trade(pnl=200.0, r=1.0), make_trade(pnl=200.0, r=1.0, day=2),
              make_trade(pnl=-100.0, r=-1.0, day=3), make_trade(pnl=-100.0, r=-1.0, day=4)]
    s = metrics.summary(trades, 50_000.0)
    assert s["trades"] == 4
    assert s["win_rate"] == 50.0
    assert s["profit_factor"] == 2.0          # 400 / 200
    assert s["net_pnl"] == 200.0
    assert s["expectancy"] == 50.0
    assert s["max_drawdown"] == 200.0         # two -100s after the peak
    assert s["max_loss_streak"] == 2


def test_profit_factor_is_none_when_there_are_no_losses():
    s = metrics.summary([make_trade(pnl=100.0)], 50_000.0)
    assert s["profit_factor"] is None


def test_summary_of_nothing_does_not_divide_by_zero():
    s = metrics.summary([], 50_000.0)
    assert s["trades"] == 0 and s["ending_equity"] == 50_000.0


PROP = {"account_size": 50_000.0, "trailing_drawdown": 2_000.0,
        "daily_loss_limit": 1_000.0, "lock_at_start_balance": True, "profit_target": 3_000.0}


def test_trailing_floor_only_moves_on_end_of_day_and_only_up():
    # Day 1 closes +$800 -> floor trails to 48,800 at the day roll.
    # Day 2 gives it all back -> the floor must NOT come back down.
    trades = [make_trade(pnl=800.0, day=1), make_trade(pnl=-800.0, day=2)]
    eq = metrics.equity_series(trades, PROP)
    assert eq["current_equity"] == 50_000.0
    assert eq["current_floor"] == 48_800.0
    assert eq["current_headroom"] == 1_200.0


def test_floor_locks_at_the_starting_balance():
    trades = [make_trade(pnl=2_500.0, day=1), make_trade(pnl=10.0, day=2)]
    eq = metrics.equity_series(trades, PROP)
    # Unlocked the floor would trail to 50,500; Topstep locks it at 50,000.
    assert eq["current_floor"] == 50_000.0


def test_intraday_profit_does_not_move_the_floor():
    # Both trades are the same session: the floor stays at its starting value.
    trades = [make_trade(pnl=900.0, day=1, hour=10), make_trade(pnl=-50.0, day=1, hour=14)]
    eq = metrics.equity_series(trades, PROP)
    assert eq["current_floor"] == 48_000.0


def test_mll_breach_is_detected():
    eq = metrics.equity_series([make_trade(pnl=-2_100.0, day=1)], PROP)
    assert eq["mll_breached"] is True and eq["mll_breach_time"] is not None


def test_daily_loss_limit_breach_is_flagged_per_day():
    trades = [make_trade(pnl=-600.0, day=1, hour=10), make_trade(pnl=-500.0, day=1, hour=12),
              make_trade(pnl=-100.0, day=2, hour=10)]
    eq = metrics.equity_series(trades, PROP)
    assert eq["daily_limit_breaches"] == ["2026-06-01"]
    assert eq["days"][0]["limit_used_pct"] == 110.0


def test_breakeven_win_rate_maths():
    # No friction: p = 1 / (1 + m). At a 0.75R target that is 57.14%.
    trades = [make_trade(pnl=0.0, r=0.0, contracts=1, stop_ticks=40)]
    f = metrics.friction_report(trades, {"commission_per_contract": 0.0, "slippage_ticks": 0}, 0.75)
    assert f["breakeven_win_rate"] == pytest.approx(57.14, abs=0.01)
    assert f["breakeven_win_rate_with_friction"] == pytest.approx(57.14, abs=0.01)


def test_friction_raises_the_bar_more_for_small_targets():
    # 1 MES contract, 40-tick stop = $50 risk; $4.98 round turn = 0.0996R friction.
    trades = [make_trade(contracts=1, stop_ticks=40)]
    cfg = {"commission_per_contract": 1.24, "slippage_ticks": 1}
    tight = metrics.friction_report(trades, cfg, 0.75)
    wide = metrics.friction_report(trades, cfg, 2.0)
    assert tight["avg_friction_r"] == pytest.approx(0.0996, abs=0.0005)
    # p = (1 + f) / (1 + m)
    assert tight["breakeven_win_rate_with_friction"] == pytest.approx(62.83, abs=0.05)
    assert wide["breakeven_win_rate_with_friction"] == pytest.approx(36.65, abs=0.05)
    # The same friction costs the 0.75R target ~5.7 points and the 2R target ~3.3.
    tight_penalty = tight["breakeven_win_rate_with_friction"] - tight["breakeven_win_rate"]
    wide_penalty = wide["breakeven_win_rate_with_friction"] - wide["breakeven_win_rate"]
    assert tight_penalty > wide_penalty


def test_halving_the_stop_doubles_friction_in_r():
    cfg = {"commission_per_contract": 1.24, "slippage_ticks": 1}
    wide = metrics.friction_report([make_trade(contracts=1, stop_ticks=40)], cfg, 1.0)
    tight = metrics.friction_report([make_trade(contracts=1, stop_ticks=20)], cfg, 1.0)
    assert tight["avg_friction_r"] == pytest.approx(2 * wide["avg_friction_r"], rel=1e-6)


def test_frequency_report_counts_losers_to_the_daily_limit():
    # $150 risk per trade against a $1,000 limit = 6.7 losers.
    trades = [make_trade(contracts=3, stop_ticks=40, day=d) for d in (1, 2, 3)]
    f = metrics.frequency_report(trades, 1_000.0)
    assert f["trading_days"] == 3
    assert f["trades_per_day_avg"] == 1.0
    assert f["losers_to_daily_limit"] == pytest.approx(6.7, abs=0.05)


def test_r_histogram_bins_include_empty_buckets():
    trades = [make_trade(r=-1.0, pnl=-50.0), make_trade(r=1.0, pnl=50.0, day=2)]
    bins = metrics.r_histogram(trades, bin_width=0.25)
    assert sum(b["count"] for b in bins) == 2
    assert any(b["count"] == 0 for b in bins)       # the gap between them is filled


def test_rolling_window_is_trailing_not_centred():
    trades = [make_trade(pnl=100.0, r=1.0, day=1), make_trade(pnl=-100.0, r=-1.0, day=2)]
    roll = metrics.rolling_metrics(trades, window=20)
    assert roll[0]["win_rate"] == 100.0
    assert roll[1]["win_rate"] == 50.0


# ---------------------------------------------------------------------- store
def test_context_applies_documented_defaults_to_an_empty_config():
    ctx = BotContext("none.yaml", {})
    assert ctx.prop["account_size"] == 50_000.0
    assert ctx.target_r == 0.75
    assert ctx.live_armed is False


def test_live_arming_needs_both_flags():
    both = {"execution": {"live": {"enabled": True, "i_understand_the_risk": True}}}
    one = {"execution": {"live": {"enabled": True, "i_understand_the_risk": False}}}
    assert BotContext("x", both).live_armed is True
    assert BotContext("x", one).live_armed is False


def test_store_reloads_when_the_blotter_changes(tmp_path):
    path = tmp_path / "trades.csv"
    header = ("entry_time,exit_time,direction,entry_price,exit_price,contracts,"
              "stop,target,pnl,r_multiple,exit_reason\n")
    row = ("2026-06-01 09:40:00,2026-06-01 10:05:00,long,5600,5610,2,5590,5615,"
           "95.02,0.95,target\n")
    path.write_text(header + row)
    store = TradeStore(blotter_path=path, config_path=tmp_path / "none.yaml")
    assert len(store.trades()) == 1

    import os, time
    path.write_text(header + row + row.replace("09:40", "10:40").replace("10:05", "11:05"))
    os.utime(path, (time.time() + 2, time.time() + 2))     # force a distinct mtime
    assert len(store.trades()) == 2


def test_demo_trades_are_deterministic():
    assert [t.pnl for t in generate_demo_trades()] == [t.pnl for t in generate_demo_trades()]


# ------------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(blotter=str(tmp_path / "none.csv"),
                                 config=str(tmp_path / "none.yaml"), demo=True))


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_overview_shape(client):
    body = client.get("/api/overview").json()
    for key in ("config", "source", "summary", "prop", "friction", "live"):
        assert key in body
    assert body["source"]["demo"] is True
    assert body["summary"]["trades"] > 0


def test_index_serves_the_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "TradeyMcGrady" in r.text


def test_symbol_filter_narrows_the_blotter(client):
    all_trades = client.get("/api/trades?limit=5000").json()
    mes = client.get("/api/trades?symbol=MES&limit=5000").json()
    assert 0 < mes["count"] < all_trades["count"]
    assert {t["symbol"] for t in mes["trades"]} == {"MES"}


def test_trades_are_returned_newest_first(client):
    trades = client.get("/api/trades?limit=50").json()["trades"]
    assert trades[0]["exit_time"] >= trades[-1]["exit_time"]


def test_since_filter_is_inclusive(client):
    body = client.get("/api/trades?since=2026-06-15&limit=5000").json()
    assert all(t["exit_time"][:10] >= "2026-06-15" for t in body["trades"])


def test_no_route_can_mutate_anything(client):
    """The dashboard is read-only. Every route is a GET."""
    for route in client.app.routes:
        methods = getattr(route, "methods", set()) or set()
        assert methods <= {"GET", "HEAD"}, f"{route.path} exposes {methods}"


def test_missing_blotter_serves_an_empty_dashboard_rather_than_500(tmp_path):
    c = TestClient(create_app(blotter=str(tmp_path / "nope.csv"),
                              config=str(tmp_path / "nope.yaml"), demo=False))
    body = c.get("/api/overview").json()
    assert body["summary"]["trades"] == 0
    assert body["source"]["blotter_exists"] is False
    assert body["live"]["available"] is False       # "not reporting", not "flat"


def test_live_state_from_a_file(tmp_path):
    state = tmp_path / "live_state.json"
    state.write_text(json.dumps({"halted": True, "reason": "daily loss limit",
                                 "open_positions": []}))
    c = TestClient(create_app(blotter=str(tmp_path / "n.csv"), config=str(tmp_path / "n.yaml"),
                              state=str(state), demo=False))
    body = c.get("/api/live").json()
    assert body["available"] is True and body["halted"] is True


def test_corrupt_live_state_does_not_crash(tmp_path):
    state = tmp_path / "live_state.json"
    state.write_text("{not json")
    c = TestClient(create_app(blotter=str(tmp_path / "n.csv"), config=str(tmp_path / "n.yaml"),
                              state=str(state), demo=False))
    assert c.get("/api/live").json()["available"] is False
