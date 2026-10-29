"""Backtest of the owner's BBP_NM strategy on REAL past option prices (same rules as the live engine).

For every trading day:
  * index 1-minute candles -> 1 / 3 / 5-minute candles -> BBP_NM Direction (continuous across days, like the live
    chart that loads the earlier days); a cross is acted on when its candle CLOSES (time T);
  * exits / entries exactly as terminal/strategy.plan (Call / Put boxes, entry window 09:30-15:00);
  * the chain at T: nearest expiry, ATM +- 5 (NIFTY, BANK NIFTY) or +- 8 (SENSEX); each strike's price = close of
    its last finished minute; strike = pick_strike (price range: cheapest inside / per lot: nearest at or below);
  * lots = lots_for(funds per entry, capital);
  * stop = low of the option's own signal candle (same timeframe);
  * fills: ENTRY at the option's next minute OPEN + slippage (not filled if it opened more than buffer + Rs 1 above
    the signal price: the live limit order would not have caught it); EXIT at the next minute OPEN - slippage;
    STOP at the stop price (or the minute's open if it opened below it) - slippage; 15:10 at the 15:10 open;
  * charges (Indian F&O option buying, 2026): brokerage Rs 20 an order, STT 0.1% of the sell value, exchange
    charges (NSE 0.03503%, BSE 0.0325% of premium), SEBI Rs 10 a crore, stamp 0.003% of the buy value,
    GST 18% on brokerage + exchange + SEBI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Optional

from terminal.candles import IST, Candle, close_time, local, resample
from terminal.fyers import INDEXES, chain_strikes
from terminal.indicators import bbp_nm
from terminal.strategy import MODES, ChainRow, direction_events, lots_for, pick_strike, plan

TICK = 0.05


@dataclass(frozen=True)
class BacktestSettings:
    index: str = "NIFTY"
    start: date = date(2026, 9, 1)
    end: date = date(2026, 9, 30)
    timeframe: int = 3
    call_on: bool = True
    put_on: bool = True
    strike_mode: str = "RANGE"
    premium_low: float = 0.0
    premium_high: float = 0.0
    per_lot_amount: float = 0.0
    funds_per_entry: float = 20000.0
    capital: float = 100000.0
    compounding: bool = False             # True: profits / losses change the money available
    buffer: float = 2.0
    slippage: float = 0.5                 # Rs per order against us (spread + delay)
    charges: bool = True
    entry_start: str = "09:30"
    entry_end: str = "15:00"
    force_exit: str = "15:10"
    bbp_window: int = 10

    def __post_init__(self) -> None:
        if self.index not in INDEXES or self.timeframe not in (1, 3, 5) or self.strike_mode not in MODES:
            raise ValueError("index / timeframe / mode not valid")
        if self.start > self.end:
            raise ValueError("start date is after the end date")


def charges_for(buy_value: float, sell_value: float, exchange: str) -> float:
    brokerage = 40.0                                         # Rs 20 buy + Rs 20 sell
    stt = 0.001 * sell_value
    txn = (0.000325 if exchange == "BSE" else 0.0003503) * (buy_value + sell_value)
    sebi = 10 / 1e7 * (buy_value + sell_value)
    stamp = 0.00003 * buy_value
    gst = 0.18 * (brokerage + txn + sebi)
    return round(brokerage + stt + txn + sebi + stamp + gst, 2)


def _tick(x: float) -> float:
    return max(round(round(x / TICK) * TICK, 2), TICK)


@dataclass
class _Open:
    side: str
    symbol: str
    strike: float
    expiry: date
    lots: int
    lot_size: int
    entry_ts: int
    entry_price: float
    stop: float
    signal_ts: int
    picked: str
    minutes: list = field(repr=False, default_factory=list)
    i: int = 0                                               # next option minute to check
    low: float = 0.0
    high: float = 0.0


@dataclass
class Result:
    settings: dict
    trades: list[dict]
    signals: list[dict]
    days: list[dict]
    notes: list[str]
    index_bars: dict                                         # day -> [Candle] (timeframe), for trade charts
    lot_size: int
    option_minutes: dict = field(default_factory=dict)       # (symbol, day) -> [Candle] of every traded option


def run(data, cfg: BacktestSettings, *, progress: Callable[[float, str], Any] = lambda f, m: None,
        log: Callable[[str], Any] = print) -> Result:
    info = INDEXES[cfg.index]
    exchange = info.spot.split(":")[0]
    notes: list[str] = []
    progress(0.02, f"{info.label}: index candles")
    warm = cfg.start - timedelta(days=10)                     # earlier days only warm BBP_NM up
    days = data.index_days(cfg.index, warm, cfg.end)
    minutes = [c for d in sorted(days) for c in days[d]]
    bars = resample(minutes, cfg.timeframe)
    dirs = [x[2] for x in bbp_nm([b.close for b in bars], cfg.bbp_window)]
    events = {bars[i].ts: e for i, e in direction_events(dirs)}
    dir_at = {b.ts: d for b, d in zip(bars, dirs)}
    test_days = [d for d in sorted(days) if cfg.start <= d <= cfg.end]
    if not test_days:
        notes.append("No index candles in these dates.")
    lot = data.lot_size(cfg.index)
    notes.append(f"Lot size {lot} ({info.label}, today's FYERS contract list) used for every day.")
    t_start, t_end, t_force = (time.fromisoformat(x) for x in (cfg.entry_start, cfg.entry_end, cfg.force_exit))
    trades, signals, day_rows = [], [], []
    realised = 0.0
    index_bars: dict = {}
    option_minutes: dict = {}

    for n, day in enumerate(test_days):
        progress(0.05 + 0.9 * n / max(1, len(test_days)), f"{info.label} {day:%d %b %Y} ({n + 1}/{len(test_days)})")
        day_bars = [b for b in bars if local(b.ts).date() == day]
        index_bars[day] = day_bars
        later = getattr(data, "expiries_from", None)
        candidates = later(cfg.index, day, cfg.start)[:3] if later else [data.nearest_expiry(cfg.index, day, cfg.start)]
        expiry, contracts = None, None
        for e in (x for x in candidates if x):
            contracts = data.contracts(cfg.index, e, lot)
            if contracts.calls:
                expiry = e
                break
        if contracts is None or not contracts.calls:
            notes.append(f"{day}: no option contract list (expiry {expiry}) - day skipped")
            day_rows.append({"date": day, "trades": 0, "net": 0.0, "note": "no option contracts"})
            continue
        pos: Optional[_Open] = None
        day_trades: list[dict] = []

        def opt_minutes(symbol: str) -> list[Candle]:
            return data.option_minutes(cfg.index, symbol, expiry, day, lot)

        def close_pos(p: _Open, ts: int, price: float, reason: str) -> None:
            nonlocal realised
            exit_price = _tick(price - cfg.slippage)
            qty = p.lots * p.lot_size
            gross = round((exit_price - p.entry_price) * qty, 2)
            ch = charges_for(p.entry_price * qty, exit_price * qty, exchange) if cfg.charges else 0.0
            risk = p.entry_price - p.stop
            row = {
                "date": day, "side": p.side, "symbol": p.symbol, "strike": p.strike, "expiry": p.expiry,
                "lots": p.lots, "qty": qty, "signal_time": local(p.signal_ts).strftime("%H:%M"),
                "entry_time": local(p.entry_ts).strftime("%H:%M"), "entry_price": p.entry_price, "stop": p.stop,
                "exit_time": local(ts).strftime("%H:%M"), "exit_price": exit_price, "exit_reason": reason,
                "points": round(exit_price - p.entry_price, 2), "gross_pnl": gross, "charges": ch,
                "net_pnl": round(gross - ch, 2), "r_multiple": round((exit_price - p.entry_price) / risk, 2)
                if risk > 0 else None, "mae": round(min(p.low, exit_price) - p.entry_price, 2),
                "mfe": round(max(p.high, exit_price) - p.entry_price, 2),
                "minutes_held": max(1, (ts - p.entry_ts) // 60), "capital_used": round(p.entry_price * qty, 2),
                "picked": p.picked, "entry_ts": p.entry_ts, "exit_ts": ts, "signal_ts": p.signal_ts,
            }
            realised += row["net_pnl"]
            day_trades.append(row)
            option_minutes[(p.symbol, day)] = p.minutes

        def walk(p: _Open, until_ts: int) -> bool:
            """Check the stop and 15:10 minute by minute before ``until_ts``. True = still open."""
            ms = p.minutes
            while p.i < len(ms) and ms[p.i].ts < until_ts:
                m = ms[p.i]
                p.i += 1
                if local(m.ts).time() >= t_force:
                    close_pos(p, m.ts, m.open, f"time exit {cfg.force_exit}")
                    return False
                p.high, p.low = max(p.high, m.high), min(p.low, m.low)
                if m.low <= p.stop:
                    close_pos(p, m.ts, m.open if m.open <= p.stop else p.stop, "stop loss (signal candle low)")
                    return False
            return True

        def price_at(ms: list[Candle], ts: int) -> tuple[Optional[Candle], Optional[float]]:
            """(first minute at / after ts within 2 minutes, close of the last minute before ts)."""
            nxt = next((m for m in ms if ts <= m.ts <= ts + 120), None)
            last = next((m.close for m in reversed(ms) if m.ts < ts), None)
            return nxt, last

        def enter(side: str, bar: Candle, T: int, reason: str) -> Optional[_Open]:
            book = contracts.calls if side == "CALL" else contracts.puts
            strikes = chain_strikes(contracts, bar.close, info.chain_width)
            rows = []
            for k in strikes:
                _, last = price_at(opt_minutes(book[k]), T)
                rows.append(ChainRow(k, book[k], last))
            pick = pick_strike(rows, mode=cfg.strike_mode, low=cfg.premium_low, high=cfg.premium_high,
                               per_lot=cfg.per_lot_amount, lot_size=lot)
            sig = {"time": local(T).strftime("%Y-%m-%d %H:%M"), "side": side}
            if pick.row is None:
                signals.append({**sig, "result": f"no {side}: {pick.reason}"})
                return None
            money = cfg.capital + (realised if cfg.compounding else 0.0)
            lots = lots_for(cfg.funds_per_entry, money, pick.row.price, lot)
            if lots < 1:
                signals.append({**sig, "result": f"no {side}: Rs {min(cfg.funds_per_entry, money):,.0f} does not "
                                                 f"buy one lot at {pick.row.price:g} x {lot}"})
                return None
            ms = opt_minutes(pick.row.symbol)
            same = [b for b in resample(ms, cfg.timeframe) if b.ts == bar.ts]
            earlier = [b for b in resample(ms, cfg.timeframe) if b.ts < bar.ts]
            stop = same[0].low if same else (earlier[-1].low if earlier else None)
            nxt, _ = price_at(ms, T)
            if stop is None or nxt is None:
                signals.append({**sig, "result": f"no {side}: no option candles for {pick.row.symbol}"})
                return None
            if nxt.open > pick.row.price + cfg.buffer + 1.0:
                signals.append({**sig, "result": f"no {side}: {pick.row.symbol} opened {nxt.open:g}, above the "
                                                 f"limit {pick.row.price + cfg.buffer:g} (+1 re-price): not filled"})
                return None
            if nxt.open <= stop:
                signals.append({**sig, "result": f"no {side}: {pick.row.symbol} {nxt.open:g} already at or below "
                                                 f"the signal candle low {stop:g}"})
                return None
            entry = _tick(nxt.open + cfg.slippage)
            signals.append({**sig, "result": f"BUY {side} {pick.row.symbol} {lots} lot(s) at {entry:g}, stop "
                                             f"{stop:g} ({pick.reason})"})
            return _Open(side, pick.row.symbol, pick.row.strike, expiry, lots, lot, nxt.ts, entry, stop, bar.ts,
                         pick.reason, ms, ms.index(nxt), entry, entry)

        for bar in day_bars:
            T = close_time(bar.ts, cfg.timeframe)
            if pos and not walk(pos, T):
                pos = None
            if local(T).time() >= t_force:
                break
            event = events.get(bar.ts)
            if not event:
                continue
            allowed = t_start <= local(T).time() < t_end
            actions = plan(event, position=pos.side if pos else None, call_on=cfg.call_on, put_on=cfg.put_on,
                           entries_allowed=allowed)
            word = "ABOVE" if event == "UP" else "BELOW"
            base = {"time": local(T).strftime("%Y-%m-%d %H:%M"), "direction": round(dir_at[bar.ts] or 0, 2),
                    "cross": word}
            if not actions:
                why = ("outside entry time" if not allowed else
                       f"{'Call' if event == 'UP' else 'Put'} box is off")
                signals.append({**base, "side": "-", "result": f"cross {word} 0: nothing to do ({why})"})
            for a in actions:
                if a.kind == "EXIT" and pos:
                    nxt, last = price_at(pos.minutes, T)
                    close_pos(pos, nxt.ts if nxt else T, nxt.open if nxt else (last or pos.entry_price),
                              f"Direction crossed {word.lower()} 0")
                    signals.append({**base, "side": a.side, "result": f"EXIT {a.side} {pos.symbol}"})
                    pos = None
                elif a.kind == "ENTER":
                    got = enter(a.side, bar, T, a.reason)
                    if got:
                        pos = got
                    signals[-1].update({k: v for k, v in base.items() if k not in signals[-1]})
        if pos:
            if walk(pos, 10 ** 12):
                last = pos.minutes[-1] if pos.minutes else None
                close_pos(pos, last.ts if last else pos.entry_ts, last.close if last else pos.entry_price,
                          "end of data (no 15:10 candle)")
            pos = None
        trades.extend(day_trades)
        day_rows.append({"date": day, "trades": len(day_trades), "net": round(sum(t["net_pnl"] for t in day_trades), 2),
                         "note": ""})
    progress(1.0, "done")
    return Result({k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(cfg).items()}, trades,
                  signals, day_rows, notes, index_bars, lot, option_minutes)


__all__ = ["BacktestSettings", "Result", "charges_for", "run"]
