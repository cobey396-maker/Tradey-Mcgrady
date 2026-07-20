#!/usr/bin/env python3
"""Parameter sweep with an in-sample / out-of-sample split (anti-overfitting).

Tunes on the TRAIN portion of the data, then re-runs the top configs on the
held-out TEST portion. A config only deserves attention if it holds up on data it
never saw. Strategy-aware: the grid depends on `strategy.name` in the base config.
Results are written to output/sweep_results.csv.

    python run_sweep.py --csv data/real_MES_5m.csv                 # market structure
    python run_sweep.py --csv data/real_MES_5m.csv --config orb.yaml   # ORB (strategy.name: orb)
"""
from __future__ import annotations

import argparse
import copy
import csv
import itertools
import logging
from pathlib import Path

from src.backtest.engine import BacktestEngine
from src.config import Config, load_config
from src.data.loader import load_csv
from src.logging_setup import setup_logging


def _clone(base: Config, mutate) -> Config:
    raw = copy.deepcopy(base.raw)
    mutate(raw)
    return Config(raw=raw, path=base.path)


def market_structure_variants(base: Config) -> list[dict]:
    grid = {
        "left": [2, 3],
        "tp": [("fixed_r", 1.5), ("fixed_r", 2.0), ("trailing", None)],
        "mom": [False, True],                 # the momentum-gate experiment
        "sides": ["long", "both"],
    }
    out = []
    for left, tp, mom, sides in itertools.product(*grid.values()):
        def mut(raw, left=left, tp=tp, mom=mom, sides=sides):
            raw["pivots"]["left_bars"] = raw["pivots"]["right_bars"] = left
            raw["take_profit"]["mode"] = tp[0]
            if tp[1] is not None:
                raw["take_profit"]["r_multiple"] = tp[1]
            raw.setdefault("trend_filter", {})["enabled"] = False
            raw.setdefault("momentum_filter", {})["enabled"] = mom
            raw["direction"] = {"long": True, "short": sides == "both"}
        label = (f"piv={left}/{left} tp={tp[0]}{'' if tp[1] is None else f'@{tp[1]}'} "
                 f"mom={'ON ' if mom else 'off'} {sides}")
        out.append({"label": label, "cfg": _clone(base, mut),
                    "left": left, "tp_mode": tp[0], "momentum": mom, "sides": sides})
    return out


def orb_variants(base: Config) -> list[dict]:
    grid = {
        "opening": [15, 30, 60],
        "stop_mode": ["range", "fraction"],
        "confirm": ["close", "wick"],
        "sides": ["long", "both"],
    }
    out = []
    for opening, stop_mode, confirm, sides in itertools.product(*grid.values()):
        def mut(raw, opening=opening, stop_mode=stop_mode, confirm=confirm, sides=sides):
            o = raw.setdefault("orb", {})
            o["opening_minutes"] = opening
            o["stop_mode"] = stop_mode
            o["breakout_confirm"] = confirm
            raw["direction"] = {"long": True, "short": sides == "both"}
        label = f"OR={opening}m stop={stop_mode} {confirm} {sides}"
        out.append({"label": label, "cfg": _clone(base, mut),
                    "opening": opening, "stop_mode": stop_mode,
                    "confirm": confirm, "sides": sides})
    return out


def make_variants(base: Config) -> list[dict]:
    name = base.get("strategy", "name", default="market_structure")
    return orb_variants(base) if name == "orb" else market_structure_variants(base)


def run_one(cfg: Config, df) -> dict:
    engine = BacktestEngine(cfg, df)
    stats = engine.run()
    report = engine.prop_report()
    return {
        "trades": stats.num_trades,
        "win_rate": round(stats.win_rate, 1),
        "profit_factor": round(stats.profit_factor, 2),
        "avg_r": round(stats.avg_r, 3),
        "net_pnl": round(report.net_pnl, 2),
        "failed_eval": report.failed,
        "daily_lockouts": report.daily_lockouts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--train-frac", type=float, default=0.65)
    parser.add_argument("--top", type=int, default=6, help="validate this many top configs")
    parser.add_argument("--min-trades", type=int, default=8,
                        help="ignore configs with fewer train trades (lucky-small-sample guard)")
    args = parser.parse_args()

    setup_logging(level=logging.ERROR)   # keep the sweep output readable
    base = load_config(args.config)
    tz = base.get("session", "timezone", default="America/New_York")
    df = load_csv(args.csv, tz=tz)
    strat = base.get("strategy", "name", default="market_structure")

    split = int(len(df) * args.train_frac)
    train, test = df.iloc[:split], df.iloc[split:]
    print(f"Strategy: {strat} | {len(df)} bars | "
          f"TRAIN {train.index[0].date()}->{train.index[-1].date()} ({len(train)}) | "
          f"TEST {test.index[0].date()}->{test.index[-1].date()} ({len(test)})\n")

    rows = make_variants(base)
    for n, r in enumerate(rows, 1):
        r.update(run_one(r["cfg"], train))
        print(f"[{n:2d}/{len(rows)}] {r['label']:34s} trades={r['trades']:3d} "
              f"pf={r['profit_factor']:5.2f} netP&L=${r['net_pnl']:9.2f} "
              f"{'FAILED-EVAL' if r['failed_eval'] else ''}")

    viable = [r for r in rows if not r["failed_eval"] and r["trades"] >= args.min_trades]
    viable.sort(key=lambda r: r["net_pnl"], reverse=True)

    print("\n=== OUT-OF-SAMPLE VALIDATION (held-out data) ===")
    if not viable:
        print("(no config cleared the failed-eval + min-trades bar in-sample)")
    for r in viable[: args.top]:
        oos = run_one(r["cfg"], test)
        r.update({f"oos_{k}": v for k, v in oos.items()})
        print(f"{r['label']:34s} TRAIN ${r['net_pnl']:9.2f} ({r['trades']:3d} tr) | "
              f"TEST ${oos['net_pnl']:9.2f} ({oos['trades']:3d} tr, pf={oos['profit_factor']:.2f})"
              f" {'FAILED-EVAL' if oos['failed_eval'] else ''}")

    out = Path("output/sweep_results.csv")
    out.parent.mkdir(exist_ok=True)
    for r in rows:
        r.pop("cfg", None)                       # not serializable / not needed in CSV
    fieldnames = sorted({k for r in rows for k in r})
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nFull results -> {out}")


if __name__ == "__main__":
    main()
