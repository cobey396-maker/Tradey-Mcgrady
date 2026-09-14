"""Trade record + blotter parsing.

The canonical on-disk format is the CSV `run_backtest.py` writes to
output/trades.csv. The portfolio engine writes the same columns plus `symbol`.
Both are accepted; a missing `symbol` falls back to the config's instrument.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from dashboard.instruments import lookup

# Columns run_backtest.py emits, in order. `symbol` is optional (single-instrument
# backtests omit it) and anything else in the file is ignored.
REQUIRED_COLUMNS = {"entry_time", "exit_time", "direction", "entry_price",
                    "exit_price", "contracts", "pnl"}


def _parse_time(value: str) -> datetime:
    """Parse the timestamps the engine writes (pandas Timestamp str()).

    Accepts ISO-8601 with or without an offset, with or without a 'T'. Naive
    timestamps are left naive: the blotter is written in session-local time
    (America/New_York) and re-interpreting it as UTC would shift every trade
    into the wrong session bucket.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp: {value!r}")


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Trade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    direction: int                 # +1 long, -1 short
    entry_price: float
    exit_price: float
    contracts: int
    stop: float
    target: float | None
    pnl: float                     # NET dollars, after commission + slippage
    r_multiple: float
    exit_reason: str

    # ---- derived -----------------------------------------------------------
    @property
    def side(self) -> str:
        return "long" if self.direction > 0 else "short"

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def duration_min(self) -> float:
        return max(0.0, (self.exit_time - self.entry_time).total_seconds() / 60.0)

    @property
    def risk_per_contract(self) -> float:
        """Dollar distance from entry to the initial stop, one contract."""
        return lookup(self.symbol).dollars(self.entry_price - self.stop, 1)

    @property
    def risk_dollars(self) -> float:
        """Planned risk for the whole position — the denominator of R."""
        return self.risk_per_contract * max(1, self.contracts)

    @property
    def gross_pnl(self) -> float:
        """P&L implied by the fills alone, before costs.

        `pnl` on the blotter is already net of commission and slippage, so the
        difference between the two is what the trade paid to trade.
        """
        inst = lookup(self.symbol)
        move = (self.exit_price - self.entry_price) * self.direction
        return move * inst.point_value * max(1, self.contracts)

    @property
    def session_date(self):
        return self.entry_time.date()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = self.entry_time.isoformat()
        d["exit_time"] = self.exit_time.isoformat()
        d["side"] = self.side
        d["duration_min"] = round(self.duration_min, 1)
        d["risk_dollars"] = round(self.risk_dollars, 2)
        d["gross_pnl"] = round(self.gross_pnl, 2)
        return d


def parse_row(row: dict, default_symbol: str = "MES") -> Trade:
    direction_raw = str(row.get("direction", "")).strip().lower()
    if direction_raw in ("long", "buy", "1", "+1"):
        direction = 1
    elif direction_raw in ("short", "sell", "-1"):
        direction = -1
    else:
        direction = 1 if _to_float(direction_raw, 1.0) >= 0 else -1

    target_raw = str(row.get("target", "")).strip()
    target = None if target_raw in ("", "None", "nan") else _to_float(target_raw, 0.0)

    return Trade(
        symbol=(row.get("symbol") or default_symbol).strip().upper(),
        entry_time=_parse_time(row["entry_time"]),
        exit_time=_parse_time(row["exit_time"]),
        direction=direction,
        entry_price=_to_float(row.get("entry_price")),
        exit_price=_to_float(row.get("exit_price")),
        contracts=int(_to_float(row.get("contracts"), 1.0)),
        stop=_to_float(row.get("stop")),
        target=target,
        pnl=_to_float(row.get("pnl")),
        r_multiple=_to_float(row.get("r_multiple")),
        exit_reason=(row.get("exit_reason") or "unknown").strip(),
    )


def load_blotter(path: str | Path, default_symbol: str = "MES") -> list[Trade]:
    """Read a trade blotter CSV. Rows that fail to parse are skipped, not fatal —
    a half-written row from a running bot must not take the dashboard down."""
    p = Path(path)
    if not p.exists():
        return []
    trades: list[Trade] = []
    with p.open(newline="") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{p}: blotter is missing columns {sorted(missing)}")
        for row in reader:
            try:
                trades.append(parse_row(row, default_symbol))
            except (ValueError, KeyError, TypeError):
                continue
    trades.sort(key=lambda t: t.exit_time)
    return trades


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
