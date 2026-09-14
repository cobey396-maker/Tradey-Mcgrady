"""Futures contract specs — the numbers that turn ticks into dollars.

Mirrors the registry the engine uses (src/instrument.py). Kept standalone so the
dashboard runs against a blotter CSV without importing the trading engine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    tick_size: float
    tick_value: float

    @property
    def point_value(self) -> float:
        """Dollars per 1.0 move in price."""
        return self.tick_value / self.tick_size

    def ticks(self, price_distance: float) -> float:
        return abs(price_distance) / self.tick_size

    def dollars(self, price_distance: float, contracts: int = 1) -> float:
        return abs(price_distance) * self.point_value * contracts


REGISTRY: dict[str, Instrument] = {
    "MES": Instrument("MES", "Micro E-mini S&P 500", 0.25, 1.25),
    "MNQ": Instrument("MNQ", "Micro E-mini Nasdaq-100", 0.25, 0.50),
    "M2K": Instrument("M2K", "Micro E-mini Russell 2000", 0.10, 0.50),
    "MGC": Instrument("MGC", "Micro Gold", 0.10, 1.00),
    "MCL": Instrument("MCL", "Micro WTI Crude", 0.01, 1.00),
    "MYM": Instrument("MYM", "Micro E-mini Dow", 1.00, 0.50),
    "ES": Instrument("ES", "E-mini S&P 500", 0.25, 12.50),
    "NQ": Instrument("NQ", "E-mini Nasdaq-100", 0.25, 5.00),
}

# Used when a blotter references a symbol we have no spec for. Deliberately the
# MES spec rather than a guess of 1.0/1.0, so dollar figures stay in a sane range,
# but callers can detect it via `is_fallback`.
FALLBACK = Instrument("?", "unknown contract", 0.25, 1.25)


def lookup(symbol: str) -> Instrument:
    return REGISTRY.get((symbol or "").upper(), FALLBACK)


def is_fallback(symbol: str) -> bool:
    return (symbol or "").upper() not in REGISTRY


def round_turn_cost(symbol: str, commission_per_side: float, slippage_ticks: float) -> float:
    """Total friction in dollars for one contract, entry + exit.

    This is the number that decides whether a higher-frequency variant of a
    strategy can work at all: it is charged per trade regardless of outcome, so
    halving the average stop distance doubles friction as a fraction of risk.
    """
    inst = lookup(symbol)
    return 2.0 * commission_per_side + 2.0 * slippage_ticks * inst.tick_value
