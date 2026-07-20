#!/usr/bin/env python3
"""Phase 1 entry point: run a backtest of the market-structure strategy.

Usage:
    python run_backtest.py                     # uses config.yaml
    python run_backtest.py --config my.yaml
    python run_backtest.py --csv data/foo.csv  # override the data file
    python run_backtest.py --no-plot
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.backtest.engine import BacktestEngine
from src.backtest.plotting import plot_results
from src.config import load_config
from src.data.loader import load_csv, load_data
from src.logging_setup import get_logger, setup_logging

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the market-structure strategy")
    parser.add_argument("--config", default="config.yaml", help="path to config YAML")
    parser.add_argument("--strategy", default=None, choices=["market_structure", "orb"],
                        help="override strategy.name from config")
    parser.add_argument("--csv", default=None, help="override CSV data path")
    parser.add_argument("--no-plot", action="store_true", help="skip the equity-curve chart")
    parser.add_argument("--out", default="output/backtest.png", help="chart output path")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config(args.config)
    if args.strategy:
        cfg.raw.setdefault("strategy", {})["name"] = args.strategy

    tz = cfg.get("session", "timezone", default="America/New_York")
    df = load_csv(args.csv, tz=tz) if args.csv else load_data(cfg)

    engine = BacktestEngine(cfg, df)
    stats = engine.run()

    print("\n" + "=" * 52)
    print(f"  BACKTEST RESULTS — {engine.instrument.symbol}")
    print("=" * 52)
    print(stats.pretty())
    print("-" * 52)
    print(f"  PROP-FIRM EVALUATION — {cfg.get('prop_firm', 'name', default='prop')}")
    print("-" * 52)
    print(engine.prop_report().pretty())
    print("=" * 52 + "\n")

    # Persist the trade blotter for inspection.
    if engine.trades:
        import csv

        blotter = Path("output/trades.csv")
        blotter.parent.mkdir(exist_ok=True)
        with blotter.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["entry_time", "exit_time", "direction", "entry_price", "exit_price",
                 "contracts", "stop", "target", "pnl", "r_multiple", "exit_reason"]
            )
            for t in engine.trades:
                writer.writerow(
                    [t.entry_time, t.exit_time, "long" if t.direction > 0 else "short",
                     f"{t.entry_price:.4f}", f"{t.exit_price:.4f}",
                     t.contracts, f"{t.stop:.4f}", t.target, f"{t.pnl:.2f}",
                     f"{t.r_multiple:.3f}", t.exit_reason]
                )
        log.info("Wrote trade blotter -> %s", blotter)

    if not args.no_plot:
        path = plot_results(df, engine.equity_curve, engine.trades, args.out, cfg.symbol)
        log.info("Wrote chart -> %s", path)


if __name__ == "__main__":
    main()
