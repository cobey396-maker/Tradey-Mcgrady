#!/usr/bin/env python3
"""Entry point: run the read-only web dashboard.

    python run_dashboard.py                  # reads output/trades.csv + config.yaml
    python run_dashboard.py --demo           # synthetic data, nothing else needed
    python run_dashboard.py --config config_v8_scalp.yaml --blotter output/v8.csv
    python run_dashboard.py --port 8081

Then open http://127.0.0.1:8080.

The dashboard is an OBSERVER. It has no route that places, sizes, modifies or
cancels an order, and it never writes to config.yaml or the blotter. It binds to
localhost by default — the trade blotter and the prop-firm state are not things
to expose on 0.0.0.0 without thinking about it first.
"""
from __future__ import annotations

import argparse

from dashboard.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the trading bot dashboard")
    parser.add_argument("--config", default="config.yaml", help="path to the bot config YAML")
    parser.add_argument("--blotter", default="output/trades.csv", help="trade blotter CSV")
    parser.add_argument("--state", default="output/live_state.json",
                        help="optional JSON written by the execution layer (open positions)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default localhost — see the module docstring)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--demo", action="store_true",
                        help="serve synthetic data so the UI can be evaluated with no bot running")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (dev)")
    args = parser.parse_args()

    app = create_app(blotter=args.blotter, config=args.config, state=args.state, demo=args.demo)

    import uvicorn

    if args.demo:
        print("\n  DEMO MODE — every number shown is synthetic, not a backtest result.\n")
    print(f"  Dashboard -> http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
