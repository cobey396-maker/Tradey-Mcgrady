"""Web dashboard for the futures prop-firm day-trading bot.

Read-only by design: the dashboard observes the bot, it never places, cancels or
sizes an order. Everything it shows is derived from the trade blotter the
backtest/execution layer writes plus the prop-firm limits in config.yaml.
"""
from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):  # pragma: no cover - thin re-export
    from dashboard.app import create_app as _create_app

    return _create_app(*args, **kwargs)
