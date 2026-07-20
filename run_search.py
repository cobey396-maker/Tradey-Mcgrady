#!/usr/bin/env python3
"""Brute-force search for a config that is profitable on ALL 6 instruments.

...and then an immediate honest check of whether that "winner" survives on data
it never saw. This script exists to answer "just keep tweaking until it's 6/6"
truthfully: with 6 instruments, a zero-edge strategy hits 6/6 by chance roughly
1 time in 64, so searching hundreds of configs will FIND one regardless of edge.
The only question that matters is whether it holds out-of-sample.

    python run_search.py --configs 150
"""
from __future__ import annotations

import argparse
import copy
import logging
import random

from src.backtest.engine import BacktestEngine
from src.config import Config, load_config
from src.data.loader import load_csv
from src.data.providers import resample_ohlcv
from src.logging_setup import setup_logging

SYMS = ["MES", "MNQ", "M2K", "MGC", "MCL", "MYM"]


def sample_config(rng) -> dict:
    """Draw one random point from the strategy/settings search space."""
    strat = rng.choice(["market_structure", "orb"])
    c = {
        "strategy": strat,
        "tf": rng.choice([None, "15min", "30min"]),
        "r_multiple": rng.choice([1.0, 1.5, 2.0, 3.0]),
        "short": rng.choice([True, False]),
        "cap": rng.choice([0, 2, 4]),
        "min_stop_ticks": rng.choice([4, 8, 16]),
        "steps": rng.choice(["off", "be1r", "be05r", "dollar"]),
        "risk_pct": rng.choice([0.5, 1.0]),
    }
    if strat == "market_structure":
        c |= {"piv": rng.choice([2, 3, 4]),
              "minsw": rng.choice([1, 2]),
              "momentum": rng.choice([True, False]),
              "confirm": rng.choice(["close", "wick"]),
              "buf": rng.choice([1, 2, 6])}
    else:
        c |= {"opening": rng.choice([15, 30, 60]),
              "stop_mode": rng.choice(["range", "fraction"]),
              "confirm": rng.choice(["close", "wick"])}
    return c


def build(base: Config, sym: str, c: dict) -> Config:
    raw = copy.deepcopy(base.raw)
    raw["strategy"] = {"name": c["strategy"]}
    raw["direction"] = {"long": True, "short": c["short"]}
    raw["take_profit"] = {"mode": "fixed_r", "r_multiple": c["r_multiple"]}
    raw["instrument"] = {"symbol": sym}
    raw["risk"] = {"risk_per_trade_pct": c["risk_pct"], "max_contracts": 10,
                   "max_open_positions": 1, "max_trades_per_day": c["cap"]}
    raw["session"] = {"enabled": True, "timezone": "America/New_York",
                      "start": "09:35", "end": "15:55", "flatten_at_end": True}
    raw["backtest"] = {**raw["backtest"], "commission_per_contract": 1.24, "slippage_ticks": 1}
    steps = {"off":    {"enabled": False, "steps": []},
             "be1r":   {"enabled": True, "mode": "r", "steps": [{"trigger": 1.0, "lock": 0.0}]},
             "be05r":  {"enabled": True, "mode": "r",
                        "steps": [{"trigger": 0.5, "lock": 0.0}, {"trigger": 1.5, "lock": 0.5}]},
             "dollar": {"enabled": True, "mode": "dollar",
                        "steps": [{"trigger": 50, "lock": 0}, {"trigger": 100, "lock": 50}]}}[c["steps"]]
    raw["stop_steps"] = steps
    if c["strategy"] == "market_structure":
        raw["pivots"] = {"left_bars": c["piv"], "right_bars": c["piv"]}
        raw["structure"] = {"min_higher_highs": c["minsw"], "min_higher_lows": c["minsw"]}
        raw["entry"] = {"require_pullback": True, "breakout_confirm": c["confirm"]}
        raw["stop"] = {"buffer_ticks": c["buf"], "min_stop_ticks": c["min_stop_ticks"]}
        raw["momentum_filter"] = {"enabled": c["momentum"], "lookbacks": [50, 100, 200], "mode": "all"}
        raw["trend_filter"] = {"enabled": False}
    else:
        raw["orb"] = {"session_open": "09:30", "opening_minutes": c["opening"],
                      "stop_mode": c["stop_mode"], "stop_fraction": 0.5, "buffer_ticks": 1,
                      "one_trade_per_day": c["cap"] in (0, 2), "breakout_confirm": c["confirm"]}
        raw["stop"] = {"buffer_ticks": 2, "min_stop_ticks": c["min_stop_ticks"]}
    return Config(raw=raw, path=base.path)


def evaluate(base, data, c, label) -> tuple[int, float]:
    """Return (#instruments profitable, total net P&L) over the given data split."""
    npos, total = 0, 0.0
    for sym in SYMS:
        df = data[sym]
        if c["tf"]:
            df = resample_ohlcv(df, c["tf"])
        eng = BacktestEngine(build(base, sym, c), df)
        eng.run()
        pnl = eng.prop_report().net_pnl
        npos += pnl > 0
        total += pnl
    return npos, total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--train-frac", type=float, default=0.65)
    args = ap.parse_args()

    setup_logging(level=logging.CRITICAL)   # silence per-trade/breach spam; it dominates runtime
    logging.disable(logging.ERROR)
    base = load_config("config.yaml")
    tz = "America/New_York"
    full = {s: load_csv(f"data/real_{s}_5m.csv", tz=tz) for s in SYMS}
    train, test = {}, {}
    for s, df in full.items():
        k = int(len(df) * args.train_frac)
        train[s], test[s] = df.iloc[:k], df.iloc[k:]
    print(f"TRAIN {train['MES'].index[0].date()} -> {train['MES'].index[-1].date()} | "
          f"TEST {test['MES'].index[0].date()} -> {test['MES'].index[-1].date()}")
    print(f"Searching {args.configs} random configs for one that is 6/6 on TRAIN...\n")

    rng = random.Random(args.seed)
    winners = []
    for i in range(1, args.configs + 1):
        c = sample_config(rng)
        try:
            npos, total = evaluate(base, train, c, "train")
        except Exception:
            continue
        if npos >= 6:
            winners.append((c, npos, total))
            print(f"  [{i:3d}] *** 6/6 ON TRAIN *** total ${total:,.0f}  {c}")
        elif i % 25 == 0:
            print(f"  [{i:3d}] searching... (best so far: {max([w[1] for w in winners], default=0)}/6)")

    print(f"\n{'='*74}\nFound {len(winners)} config(s) that were 6/6 profitable on TRAIN.")
    if not winners:
        print("None hit 6/6 even in-sample.")
        return

    print(f"\nNow the only question that matters — do they hold on data they never saw?\n")
    print(f"{'#':>2}  {'TRAIN':>18}   {'TEST (held-out)':>20}")
    held = 0
    for n, (c, npos, total) in enumerate(winners, 1):
        t_npos, t_total = evaluate(base, test, c, "test")
        held += t_npos >= 6
        flag = "  <-- HELD UP" if t_npos >= 6 else ""
        print(f"{n:2d}  6/6  ${total:>9,.0f}   {t_npos}/6  ${t_total:>9,.0f}{flag}")
    print(f"\n{held} of {len(winners)} 6/6 configs stayed 6/6 out-of-sample.")


if __name__ == "__main__":
    main()
