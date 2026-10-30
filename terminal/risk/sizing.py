"""Dual-constraint position size (owner's formulas, 30 Oct 2026): the SMALLER of the capital limit and the risk limit,
in whole lots.

    lots_capital = floor(capital x allocation% / (entry x lot))
    lots_risk    = floor(max_risk / ((entry - stop) x lot))
    lots         = min(lots_capital, lots_risk)

PLAIN (default, owner's choice): exactly those formulas with the box prices.
STRICT (switch): the worst normal fills - entry + buffer (the limit sent) and stop - buffer (the SL-L limit) - and
the estimated charges are counted in the risk, so the real loss stays under the cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Optional


def charges_estimate(entry: float, stop: float, qty: int, exchange: str = "NSE") -> float:
    """Charges of a buy at ``entry`` and a sell at ``stop`` (brokerage, STT, exchange, SEBI, stamp, GST)."""
    from terminal.backtest import charges_for
    return charges_for(entry * qty, stop * qty, exchange)


@dataclass(frozen=True)
class Size:
    lots: int
    qty: int
    lot_size: int
    lots_capital: int
    lots_risk: int
    decided_by: str            # "capital", "risk" or "both"
    entry_used: float
    stop_used: float
    risk_per_unit: float
    money: float               # entry_used x qty
    risk_rs: float             # worst loss in rupees (strict: with charges)
    reason: str                # why 0 lots, else ""


def _floor_div(a: float, b: float) -> int:
    if b <= 0:
        return 0
    return int((Decimal(str(a)) / Decimal(str(b))).to_integral_value(rounding=ROUND_DOWN))


def position_size(*, capital: float, allocation_pct: float, max_risk: float, entry: float, stop: float,
                  lot_size: int, buffer: float = 2.0, strict: bool = False, exchange: str = "NSE",
                  max_lots: Optional[int] = None) -> Size:
    entry_used = entry + buffer if strict else entry
    stop_used = max(stop - buffer, 0.05) if strict else stop
    rpu = round(entry_used - stop_used, 4)
    budget = max(0.0, capital) * max(0.0, allocation_pct) / 100.0
    lots_capital = _floor_div(budget, entry_used * lot_size) if entry_used > 0 else 0
    lots_risk = _floor_div(max_risk, rpu * lot_size) if rpu > 0 else 0
    if strict:                                         # charges count against the risk cap too
        while lots_risk > 0 and lots_risk * lot_size * rpu + charges_estimate(
                entry_used, stop_used, lots_risk * lot_size, exchange) > max_risk:
            lots_risk -= 1
    lots = min(lots_capital, lots_risk)
    if max_lots is not None:
        lots = min(lots, max_lots)
    decided = "both" if lots_capital == lots_risk else ("capital" if lots_capital < lots_risk else "risk")
    if max_lots is not None and lots == max_lots and max_lots < min(lots_capital, lots_risk):
        decided = "safety cap"
    qty = lots * lot_size
    reason = ""
    if rpu <= 0:
        reason = "stop must be below the entry"
    elif lot_size < 1:
        reason = "lot size unknown"
    elif lots_capital < 1:
        reason = f"Rs {budget:,.0f} ({allocation_pct:g}% of Rs {capital:,.0f}) does not buy one lot " \
                 f"({entry_used:g} x {lot_size} = Rs {entry_used * lot_size:,.0f})"
    elif lots_risk < 1:
        reason = f"one lot risks Rs {rpu * lot_size:,.0f}, more than the max risk Rs {max_risk:,.0f}"
    risk_rs = rpu * qty + (charges_estimate(entry_used, stop_used, qty, exchange) if strict and qty else 0.0)
    return Size(lots, qty, lot_size, lots_capital, lots_risk, decided, round(entry_used, 2), round(stop_used, 2),
                rpu, round(entry_used * qty, 2), round(risk_rs, 2), reason)


__all__ = ["Size", "charges_estimate", "position_size"]
