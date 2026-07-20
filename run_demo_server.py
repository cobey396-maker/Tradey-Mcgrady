#!/usr/bin/env python3
"""Phase 2 entry point: run the TradingView webhook -> Tradovate DEMO server.

    # 1) put your Tradovate DEMO creds + WEBHOOK_SECRET in .env  (see .env.example)
    # 2) start the server
    python run_demo_server.py                    # binds 0.0.0.0:8000
    python run_demo_server.py --port 8080
    python run_demo_server.py --dry-run          # MockBroker, no Tradovate calls

Then expose it to TradingView (e.g. `ngrok http 8000`) and point your alert's
webhook URL at https://<host>/webhook. See README for the alert JSON.

This runner will ONLY ever construct a demo or mock broker. Live is not reachable
here — see src/execution/live.py.
"""
from __future__ import annotations

import argparse

from dotenv import load_dotenv

from src.config import load_config
from src.execution.base import MockBroker
from src.execution.tradovate import TradovateClient
from src.logging_setup import get_logger, setup_logging
from src.server.app import create_app

log = get_logger(__name__)


def build_broker(cfg, dry_run: bool):
    if dry_run:
        log.warning("DRY RUN: using in-memory MockBroker — no Tradovate calls will be made")
        return MockBroker(equity=float(cfg.get("prop_firm", "account_size", default=50_000.0)))

    mode = cfg.execution_mode
    if mode == "live":
        # Hard stop: this runner never arms live. Use is gated in execution/live.py.
        raise SystemExit(
            "execution.mode is 'live' — this demo runner refuses to start. "
            "Live trading is a disabled scaffold (see src/execution/live.py)."
        )
    log.info("Connecting Tradovate DEMO broker for %s", cfg.symbol)
    return TradovateClient.from_env(
        symbol=cfg.symbol,
        is_automated=bool(cfg.get("execution", "is_automated", default=True)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the demo webhook server")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--dry-run", action="store_true", help="use a MockBroker, no broker calls")
    args = parser.parse_args()

    setup_logging()
    load_dotenv()                       # load secrets from .env into the environment
    cfg = load_config(args.config)

    if not cfg.webhook_secret:
        raise SystemExit("WEBHOOK_SECRET is not set in the environment (.env). Refusing to start.")

    broker = build_broker(cfg, args.dry_run)
    app = create_app(cfg, broker)

    import uvicorn
    log.info("Starting demo server on %s:%d (mode=%s)", args.host, args.port, cfg.execution_mode)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
