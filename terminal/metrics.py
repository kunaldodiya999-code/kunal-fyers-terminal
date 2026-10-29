"""Backtest statistics, the way professional reports show them (all from the closed trades, after charges)."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Iterable, Optional


def _pf(wins: float, losses: float) -> Optional[float]:
    return round(wins / -losses, 2) if losses < 0 else None


def _streak(values: Iterable[float], positive: bool) -> int:
    best = cur = 0
    for v in values:
        cur = cur + 1 if (v > 0 if positive else v <= 0) else 0
        best = max(best, cur)
    return best


def group_stats(trades: list[dict], key) -> list[dict]:
    groups: dict = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    out = []
    for k, ts in sorted(groups.items(), key=lambda kv: str(kv[0])):
        net = [t["net_pnl"] for t in ts]
        wins = sum(x for x in net if x > 0)
        losses = sum(x for x in net if x <= 0)
        out.append({"group": k, "trades": len(ts), "win_rate_%": round(100 * sum(x > 0 for x in net) / len(ts), 1),
                    "net_pnl": round(sum(net), 2), "avg_pnl": round(sum(net) / len(ts), 2),
                    "profit_factor": _pf(wins, losses)})
    return out


def summary(trades: list[dict], days: list[dict], capital: float) -> dict:
    net = [t["net_pnl"] for t in trades]
    n = len(net)
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    equity, peak, max_dd, max_dd_pct = capital, capital, 0.0, 0.0
    for x in net:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        max_dd_pct = max(max_dd_pct, (peak - equity) / peak * 100 if peak else 0.0)
    day_net = [d["net"] for d in days]
    traded_days = len(day_net)
    mean = sum(day_net) / traded_days if traded_days else 0.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in day_net) / (traded_days - 1)) if traded_days > 1 else 0.0
    down = [min(0.0, x) for x in day_net]
    dsd = math.sqrt(sum(x * x for x in down) / traded_days) if traded_days else 0.0
    total = sum(net)
    out = {
        "Net P&L (Rs)": round(total, 2),
        "Gross P&L (Rs)": round(sum(t["gross_pnl"] for t in trades), 2),
        "Charges (Rs)": round(sum(t["charges"] for t in trades), 2),
        "Return on capital %": round(total / capital * 100, 2) if capital else None,
        "Trades": n,
        "Winning trades": len(wins),
        "Losing trades": len(losses),
        "Win rate %": round(100 * len(wins) / n, 1) if n else None,
        "Average win (Rs)": round(sum(wins) / len(wins), 2) if wins else None,
        "Average loss (Rs)": round(sum(losses) / len(losses), 2) if losses else None,
        "Payoff ratio (avg win / avg loss)": round((sum(wins) / len(wins)) / -(sum(losses) / len(losses)), 2)
        if wins and losses and sum(losses) < 0 else None,
        "Profit factor": _pf(sum(wins), sum(losses)),
        "Expectancy per trade (Rs)": round(total / n, 2) if n else None,
        "Average R multiple": round(sum(t["r_multiple"] for t in trades if t["r_multiple"] is not None) /
                                    max(1, sum(t["r_multiple"] is not None for t in trades)), 2) if n else None,
        "Largest win (Rs)": round(max(net), 2) if n else None,
        "Largest loss (Rs)": round(min(net), 2) if n else None,
        "Max consecutive wins": _streak(net, True),
        "Max consecutive losses": _streak(net, False),
        "Max drawdown (Rs)": round(max_dd, 2),
        "Max drawdown %": round(max_dd_pct, 2),
        "Recovery factor (net / max DD)": round(total / max_dd, 2) if max_dd else None,
        "Trading days": traded_days,
        "Profitable days %": round(100 * sum(x > 0 for x in day_net) / traded_days, 1) if traded_days else None,
        "Best day (Rs)": round(max(day_net), 2) if day_net else None,
        "Worst day (Rs)": round(min(day_net), 2) if day_net else None,
        "Average trades per day": round(n / traded_days, 2) if traded_days else None,
        "Sharpe ratio (daily, yearly scale)": round(mean / sd * math.sqrt(252), 2) if sd else None,
        "Sortino ratio (daily, yearly scale)": round(mean / dsd * math.sqrt(252), 2) if dsd else None,
        "Average minutes in a trade": round(sum(t["minutes_held"] for t in trades) / n, 1) if n else None,
        "Average MAE (points against)": round(sum(t["mae"] for t in trades) / n, 2) if n else None,
        "Average MFE (points in favour)": round(sum(t["mfe"] for t in trades) / n, 2) if n else None,
        "Average capital per trade (Rs)": round(sum(t["capital_used"] for t in trades) / n, 2) if n else None,
    }
    return out


def equity_curve(trades: list[dict], capital: float) -> list[dict]:
    rows, equity, peak = [], capital, capital
    for i, t in enumerate(trades, 1):
        equity += t["net_pnl"]
        peak = max(peak, equity)
        rows.append({"trade": i, "date": t["date"], "equity": round(equity, 2),
                     "drawdown": round(equity - peak, 2)})
    return rows


def monthly(days: list[dict]) -> list[dict]:
    out: dict = defaultdict(lambda: {"trades": 0, "net": 0.0, "days": 0})
    for d in days:
        k = d["date"].strftime("%Y-%m") if isinstance(d["date"], date) else str(d["date"])[:7]
        out[k]["trades"] += d["trades"]
        out[k]["net"] = round(out[k]["net"] + d["net"], 2)
        out[k]["days"] += 1
    return [{"month": k, **v} for k, v in sorted(out.items())]


__all__ = ["equity_curve", "group_stats", "monthly", "summary"]
