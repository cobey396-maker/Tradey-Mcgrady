"""Data access: config, blotter, live state.

The dashboard is a *reader*. It reloads the blotter when the file's mtime
changes so a running backtest or live session shows up without a restart, and it
never holds a lock on anything the bot writes.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

from dashboard.instruments import lookup
from dashboard.models import Trade, load_blotter

DEFAULT_CONFIG = "config.yaml"
DEFAULT_BLOTTER = "output/trades.csv"
DEFAULT_STATE = "output/live_state.json"


def load_config(path: str | Path) -> dict:
    """Load the bot's YAML config. A missing or unreadable file is not fatal —
    the dashboard falls back to the documented defaults so it still starts."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is in requirements.txt
        return {}
    try:
        with p.open() as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


@dataclass
class BotContext:
    """The slice of config.yaml the dashboard needs, with defaults applied."""
    config_path: str = DEFAULT_CONFIG
    raw: dict = field(default_factory=dict)

    @property
    def strategy(self) -> str:
        return str(self.raw.get("strategy", {}).get("name", "orb"))

    @property
    def symbol(self) -> str:
        return str(self.raw.get("instrument", {}).get("symbol", "MES")).upper()

    @property
    def timeframe(self) -> str:
        return str(self.raw.get("timeframe", "5m"))

    @property
    def prop(self) -> dict:
        p = dict(self.raw.get("prop_firm", {}) or {})
        p.setdefault("name", "Topstep")
        p.setdefault("account_size", 50_000.0)
        p.setdefault("daily_loss_limit", 1_000.0)
        p.setdefault("trailing_drawdown", 2_000.0)
        p.setdefault("lock_at_start_balance", True)
        return p

    @property
    def backtest(self) -> dict:
        b = dict(self.raw.get("backtest", {}) or {})
        b.setdefault("commission_per_contract", 1.24)
        b.setdefault("slippage_ticks", 1)
        return b

    @property
    def risk(self) -> dict:
        r = dict(self.raw.get("risk", {}) or {})
        r.setdefault("risk_per_trade_pct", 0.25)
        r.setdefault("max_concurrent_positions", 3)
        r.setdefault("max_trades_per_day", 0)
        r.setdefault("max_contracts", 10)
        return r

    @property
    def target_r(self) -> float:
        tp = self.raw.get("take_profit", {}) or {}
        return float(tp.get("r_multiple", 0.75) or 0.75)

    @property
    def execution_mode(self) -> str:
        return str((self.raw.get("execution", {}) or {}).get("mode", "demo"))

    @property
    def live_armed(self) -> bool:
        live = (self.raw.get("execution", {}) or {}).get("live", {}) or {}
        return bool(live.get("enabled")) and bool(live.get("i_understand_the_risk"))

    def describe(self) -> dict:
        orb = self.raw.get("orb", {}) or {}
        session = self.raw.get("session", {}) or {}
        return {
            "config_path": self.config_path,
            "config_found": bool(self.raw),
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "target_r": self.target_r,
            "risk_per_trade_pct": self.risk["risk_per_trade_pct"],
            "max_concurrent_positions": self.risk["max_concurrent_positions"],
            "max_trades_per_day": self.risk["max_trades_per_day"],
            "opening_minutes": orb.get("opening_minutes"),
            "one_trade_per_day": orb.get("one_trade_per_day"),
            "session_start": session.get("start"),
            "session_end": session.get("end"),
            "session_timezone": session.get("timezone", "America/New_York"),
            "execution_mode": self.execution_mode,
            "live_armed": self.live_armed,
            "prop_firm": self.prop.get("name"),
        }


class TradeStore:
    """Blotter + live-state reader with mtime-based caching."""

    def __init__(self, blotter_path: str | Path = DEFAULT_BLOTTER,
                 state_path: str | Path = DEFAULT_STATE,
                 config_path: str | Path = DEFAULT_CONFIG,
                 demo: bool = False):
        self.blotter_path = Path(blotter_path)
        self.state_path = Path(state_path)
        self.config_path = Path(config_path)
        self.demo = demo
        self._trades: list[Trade] = []
        self._blotter_mtime: float | None = None
        self._config_mtime: float | None = None
        self._ctx = BotContext(str(self.config_path), load_config(self.config_path))
        self._demo_trades: list[Trade] | None = None
        self.load_error: str | None = None

    # -- config ------------------------------------------------------------
    def context(self) -> BotContext:
        mtime = self.config_path.stat().st_mtime if self.config_path.exists() else None
        if mtime != self._config_mtime:
            self._config_mtime = mtime
            self._ctx = BotContext(str(self.config_path), load_config(self.config_path))
        return self._ctx

    # -- trades ------------------------------------------------------------
    def trades(self) -> list[Trade]:
        if self.demo:
            if self._demo_trades is None:
                self._demo_trades = generate_demo_trades()
            return self._demo_trades

        if not self.blotter_path.exists():
            self._trades = []
            self._blotter_mtime = None
            return self._trades

        mtime = self.blotter_path.stat().st_mtime
        if mtime != self._blotter_mtime:
            try:
                self._trades = load_blotter(self.blotter_path, self.context().symbol)
                self.load_error = None
            except ValueError as exc:
                self.load_error = str(exc)
                self._trades = []
            self._blotter_mtime = mtime
        return self._trades

    def source(self) -> dict:
        return {
            "demo": self.demo,
            "blotter_path": str(self.blotter_path),
            "blotter_exists": self.blotter_path.exists(),
            "blotter_mtime": (
                datetime.fromtimestamp(self.blotter_path.stat().st_mtime).isoformat()
                if self.blotter_path.exists() else None
            ),
            "state_path": str(self.state_path),
            "error": self.load_error,
        }

    # -- live state --------------------------------------------------------
    def live_state(self) -> dict:
        """Open positions and kill-switch flags, if the execution layer writes them.

        Absent file is the normal case for a backtest-only workflow; the UI shows
        'not reporting' rather than pretending there are zero open positions.
        """
        if self.demo:
            return demo_live_state()
        if not self.state_path.exists():
            return {"available": False, "open_positions": [], "halted": None, "reason": None}
        try:
            with self.state_path.open() as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {"available": False, "open_positions": [], "halted": None,
                    "reason": "live state file is unreadable"}
        data.setdefault("open_positions", [])
        data["available"] = True
        return data


# ---------------------------------------------------------------------------
# Demo data — lets the dashboard be opened and evaluated with no bot running.
# Deterministic (fixed seed) so screenshots and tests are reproducible.
# ---------------------------------------------------------------------------
DEMO_SYMBOLS = ["MES", "MNQ", "M2K", "MGC", "MCL", "MYM"]


def generate_demo_trades(days: int = 45, seed: int = 7, target_r: float = 1.0,
                         win_rate: float = 0.52, trades_per_day: int = 7) -> list[Trade]:
    """Synthesise a plausible higher-frequency blotter.

    NOT a backtest and not evidence of anything — it exists so the UI has shape
    to render. Every number the dashboard shows in demo mode is made up.
    """
    rng = random.Random(seed)
    base_price = {"MES": 5600.0, "MNQ": 20000.0, "M2K": 2300.0,
                  "MGC": 2400.0, "MCL": 78.0, "MYM": 41000.0}
    trades: list[Trade] = []
    day = datetime(2026, 6, 1)
    account = 50_000.0
    risk_pct = 0.15

    while sum(1 for _ in trades) < days * trades_per_day // 2 or (day - datetime(2026, 6, 1)).days < days:
        if (day - datetime(2026, 6, 1)).days >= days:
            break
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        n = rng.randint(max(1, trades_per_day - 3), trades_per_day + 2)
        clock = datetime.combine(day.date(), time(9, 40))
        for _ in range(n):
            sym = rng.choice(DEMO_SYMBOLS)
            inst = lookup(sym)
            direction = rng.choice([1, -1])
            entry = base_price[sym] * (1 + rng.gauss(0, 0.0015))
            stop_ticks = rng.randint(36, 72)
            stop_dist = stop_ticks * inst.tick_size
            risk_per_contract = stop_ticks * inst.tick_value
            contracts = max(1, min(10, int((account * risk_pct / 100.0) // risk_per_contract)))

            won = rng.random() < win_rate
            if won:
                r = target_r * rng.uniform(0.96, 1.02)
                reason = "target"
            elif rng.random() < 0.22:
                r = rng.uniform(-0.45, 0.35)
                reason = rng.choice(["breakeven_stop", "session_end"])
            else:
                r = -rng.uniform(0.98, 1.12)   # slippage past the stop
                reason = "stop"

            exit_price = entry + direction * r * stop_dist
            gross = r * risk_per_contract * contracts
            cost = (2 * 1.24 + 2 * 1 * inst.tick_value) * contracts
            pnl = gross - cost

            enter = clock + timedelta(minutes=rng.randint(0, 12))
            exit_t = enter + timedelta(minutes=rng.randint(6, 55))
            trades.append(Trade(
                symbol=sym, entry_time=enter, exit_time=exit_t, direction=direction,
                entry_price=round(entry, 2),
                exit_price=round(exit_price, 2),
                contracts=contracts,
                stop=round(entry - direction * stop_dist, 2),
                target=round(entry + direction * target_r * stop_dist, 2),
                pnl=round(pnl, 2),
                r_multiple=round(pnl / (risk_per_contract * contracts), 4),
                exit_reason=reason,
            ))
            account += pnl
            clock = exit_t + timedelta(minutes=rng.randint(2, 30))
            if clock.time() > time(15, 30):
                break
        day += timedelta(days=1)

    trades.sort(key=lambda t: t.exit_time)
    return trades


def demo_live_state() -> dict:
    return {
        "available": True,
        "as_of": datetime(2026, 7, 15, 11, 42).isoformat(),
        "halted": False,
        "reason": None,
        "open_positions": [
            {"symbol": "MNQ", "side": "long", "contracts": 2, "entry_price": 20115.25,
             "stop": 20081.75, "target": 20148.75, "unrealized": 41.0,
             "opened_at": datetime(2026, 7, 15, 11, 12).isoformat()},
            {"symbol": "MGC", "side": "short", "contracts": 3, "entry_price": 2411.4,
             "stop": 2416.2, "target": 2406.6, "unrealized": -27.0,
             "opened_at": datetime(2026, 7, 15, 11, 31).isoformat()},
        ],
    }
