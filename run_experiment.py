#!/usr/bin/env python3
"""Cross-instrument robustness harness — the anti-overfitting guardrail.

Instead of tuning one config until it looks good on one instrument's held-out
slice (which just curve-fits that slice), this evaluates each FIXED config across
MANY instruments over the same period. A rule with real edge should make money on
several markets; a curve-fit won't. A config is only "interesting" if it's
profitable on a majority of instruments AND never blows the prop evaluation.

    python run_experiment.py
"""
from __future__ import annotations

import logging

from src.backtest.engine import BacktestEngine
from src.config import load_config
from src.data.loader import load_csv
from src.data.providers import resample_ohlcv
from src.logging_setup import setup_logging

INSTRUMENTS = {
    "MES": "data/real_MES_5m.csv", "MNQ": "data/real_MNQ_5m.csv",
    "M2K": "data/real_M2K_5m.csv", "MGC": "data/real_MGC_5m.csv",
    "MCL": "data/real_MCL_5m.csv", "MYM": "data/real_MYM_5m.csv",
}


def cfg_for(base, symbol, overrides, resample):
    import copy
    from src.config import Config
    raw = copy.deepcopy(base.raw)
    raw["instrument"] = {"symbol": symbol}
    for path, val in overrides.items():
        node = raw
        keys = path.split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = val
    return Config(raw=raw, path=base.path), resample


# Each config: (label, overrides, resample-rule-or-None). These are the levers the
# user asked to vary: strategy, timeframe (via resample), risk/exit params.
CONFIGS = [
    ("MS piv3 2R 5m",            {"strategy.name": "market_structure", "pivots.left_bars": 3,
                                  "pivots.right_bars": 3, "take_profit.mode": "fixed_r",
                                  "take_profit.r_multiple": 2.0, "momentum_filter.enabled": False,
                                  "direction.short": False}, None),
    ("MS piz3 2R +mom 5m",       {"strategy.name": "market_structure", "pivots.left_bars": 3,
                                  "pivots.right_bars": 3, "momentum_filter.enabled": True,
                                  "direction.short": False}, None),
    ("MS piv3 2R 15m",           {"strategy.name": "market_structure", "pivots.left_bars": 3,
                                  "pivots.right_bars": 3, "momentum_filter.enabled": False,
                                  "direction.short": False}, "15min"),
    ("MS piv3 2R 30m",           {"strategy.name": "market_structure", "pivots.left_bars": 3,
                                  "pivots.right_bars": 3, "momentum_filter.enabled": False,
                                  "direction.short": False}, "30min"),
    ("MS piv3 3R 5m long",       {"strategy.name": "market_structure", "pivots.left_bars": 3,
                                  "pivots.right_bars": 3, "take_profit.r_multiple": 3.0,
                                  "momentum_filter.enabled": False, "direction.short": False}, None),
    ("ORB 30m range long",       {"strategy.name": "orb", "orb.opening_minutes": 30,
                                  "orb.stop_mode": "range", "direction.short": False}, None),
    ("ORB 60m fraction long",    {"strategy.name": "orb", "orb.opening_minutes": 60,
                                  "orb.stop_mode": "fraction", "direction.short": False}, None),
    ("ORB 30m fraction both",    {"strategy.name": "orb", "orb.opening_minutes": 30,
                                  "orb.stop_mode": "fraction", "direction.short": True}, None),
]


def main() -> None:
    setup_logging(level=logging.ERROR)
    base = load_config("config.yaml")
    tz = base.get("session", "timezone", default="America/New_York")

    # preload data once
    data = {}
    for sym, path in INSTRUMENTS.items():
        try:
            data[sym] = load_csv(path, tz=tz)
        except FileNotFoundError:
            print(f"(missing {sym} data, skipping)")

    syms = list(data)
    print(f"Cross-instrument test over {len(syms)} instruments: {', '.join(syms)}\n")
    header = f"{'config':22s} " + " ".join(f"{s:>7s}" for s in syms) + "   #pos  #fail"
    print(header)
    print("-" * len(header))

    summary = []
    for label, overrides, resample in CONFIGS:
        cells, npos, nfail = [], 0, 0
        for sym in syms:
            df = data[sym]
            if resample:
                df = resample_ohlcv(df, resample)
            cfg, _ = cfg_for(base, sym, overrides, resample)
            eng = BacktestEngine(cfg, df)
            eng.run()
            rep = eng.prop_report()
            pnl = rep.net_pnl
            cells.append(f"{pnl:7.0f}")
            npos += pnl > 0
            nfail += rep.failed
        print(f"{label:22s} " + " ".join(cells) + f"   {npos}/{len(syms)}   {nfail}")
        summary.append((label, npos, nfail))

    print("\nBest by cross-instrument OOS breadth (most profitable instruments, fewest eval failures):")
    for label, npos, nfail in sorted(summary, key=lambda x: (x[1], -x[2]), reverse=True)[:4]:
        print(f"  {label:22s}  positive on {npos}/{len(syms)}  |  failed eval on {nfail}")


if __name__ == "__main__":
    main()
