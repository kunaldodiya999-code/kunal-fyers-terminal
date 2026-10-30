"""The risk desk: every box trade from Arm to Closed (owner's answers, 30 Oct 2026).

States: ARMED -> ENTRY_WORKING -> IN_TRADE -> (TARGET_EXITING | STOP_CHASING | EXITING) -> CLOSED, or CANCELLED.
  ARMED          nothing at the exchange; waits for the price to REACH the box entry (owner Q4: pending only).
  ENTRY_WORKING  BUY LIMIT at price + buffer; re-priced once (at most Rs 1 higher), then the rest is cancelled.
  IN_TRADE       ONE SELL SL-L at the exchange: trigger = stop, limit = stop - buffer (owner Q5: the safer default).
  TARGET_EXITING price reached the target: the SAME order is modified into a LIMIT SELL at price - buffer.
  STOP_CHASING   the SL-L triggered but the price jumped below its limit: the limit follows the price down
                 (price - buffer) every few seconds. Never a market order.
  EXITING        Exit button, 15:10 or the kill switch: the same order becomes a LIMIT SELL at price - buffer.
Quantity is fixed when you press Arm. A drag that would push the rupee risk above Max risk is REFUSED (owner Q6:
never reduce the quantity). Max loss for the day (empty = no limit): when today's closed losses reach it, no new
Arm and armed boxes are cancelled. No cap on the number of trades (owner Q7).
"""

from __future__ import annotations

import itertools
import json
import threading
from collections import deque
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, time
from pathlib import Path
from typing import Any, Callable, Optional

from live_trading.orders import round_to_tick
from terminal.candles import IST
from terminal.fyers import data_dir
from terminal.killswitch import KillSwitch
from terminal.risk.gateways import OrderEvent, OrderRefused
from terminal.risk.sizing import charges_estimate, position_size

TICK = 0.05
OPEN_STATES = {"ENTRY_WORKING", "IN_TRADE", "TARGET_EXITING", "STOP_CHASING", "EXITING"}


def up(x: float) -> float:
    return max(round_to_tick(x, TICK, "BUY"), TICK)


def down(x: float) -> float:
    return max(round_to_tick(x, TICK, "SELL"), TICK)


def near(x: float) -> float:
    return round(round(x / TICK) * TICK, 2)


@dataclass(frozen=True)
class DeskSettings:
    allocation_pct: float = 50.0
    max_risk: float = 5000.0
    max_day_loss: Optional[float] = None        # empty box = no daily limit
    buffer: float = 2.0
    strict: bool = False                        # owner Q3: plain formula by default
    paper_capital: float = 100000.0
    entry_from: str = "09:15"
    entry_until: str = "15:00"
    force_exit: str = "15:10"
    entry_timeout: float = 10.0                 # seconds before the one re-price / cancel of an entry
    chase_every: float = 2.0                    # seconds between exit re-prices
    real_orders: bool = False                   # + today's arm (arm_real_orders.py)
    real_max_lots: int = 1                      # phase 3: real orders with 1 lot only until you raise it
    min_stop_ticks: int = 3

    def __post_init__(self) -> None:
        if not (0 < self.allocation_pct <= 100):
            raise ValueError("capital allocation must be 1-100 %")
        if self.max_risk <= 0:
            raise ValueError("max risk per trade must be above 0")
        if self.max_day_loss is not None and self.max_day_loss <= 0:
            raise ValueError("max loss per day must be above 0 (or empty)")
        if not (0 <= self.buffer <= 10) or self.real_max_lots < 1:
            raise ValueError("buffer 0-10, real max lots at least 1")
        if not (time.fromisoformat(self.entry_from) < time.fromisoformat(self.entry_until)
                <= time.fromisoformat(self.force_exit)):
            raise ValueError("times: entries from < entries until <= close everything")


class DeskSettingsStore:
    def __init__(self, folder: Optional[Path] = None) -> None:
        self.path = Path(folder or data_dir()) / "risk_settings.json"

    def load(self) -> DeskSettings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return DeskSettings(**{k: v for k, v in data.items() if k in {f.name for f in fields(DeskSettings)}})
        except (OSError, ValueError, TypeError):
            return DeskSettings()

    def save(self, s: DeskSettings) -> DeskSettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
        return s

    def update(self, **changes) -> DeskSettings:
        return self.save(replace(self.load(), **changes))


@dataclass
class Trade:
    id: str
    day: str
    symbol: str
    lot_size: int
    exchange: str
    entry: float
    stop: float
    target: float
    lots: int
    qty: int
    mode: str                                   # PAPER / REAL
    state: str = "ARMED"
    trigger_dir: str = "up"                     # up: price rises to the entry; down: price falls to it
    sizing: dict = field(default_factory=dict)
    entry_order: Optional[str] = None
    entry_limit: float = 0.0
    entry_first: float = 0.0
    entry_sent: float = 0.0
    entry_repriced: bool = False
    filled_qty: int = 0
    entry_avg: Optional[float] = None
    exit_order: Optional[str] = None
    exit_kind: str = ""                         # SL / LIMIT
    exit_limit: float = 0.0
    exit_sent: float = 0.0
    exit_filled: int = 0
    exit_avg: Optional[float] = None
    exit_reason: str = ""
    chase_steps: int = 0
    pnl: Optional[float] = None
    charges: Optional[float] = None
    opened_at: str = ""
    closed_at: str = ""
    t0: Optional[int] = None                    # where the box starts on the chart (chart time)
    notes: list = field(default_factory=list)


class Desk:
    def __init__(self, *, paper, real=None, folder: Optional[Path] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(IST),
                 mono: Optional[Callable[[], float]] = None, funds: Callable[[], float] = lambda: 0.0,
                 real_allowed: Callable[[], bool] = lambda: False) -> None:
        import time as _t
        self.folder = Path(folder or data_dir())
        self.store = DeskSettingsStore(self.folder)
        self.kill = KillSwitch(self.folder, clock)
        self.clock, self.mono = clock, mono or _t.monotonic
        self.funds, self.real_allowed = funds, real_allowed
        self.gw = {"PAPER": paper}
        if real is not None:
            self.gw["REAL"] = real
        for g in self.gw.values():
            g.listener = self._queue_event
        self.lock = threading.RLock()
        self.trades: dict[str, Trade] = {}
        self.quotes: dict[str, dict] = {}
        self.instruments: dict[str, dict] = {}       # symbol -> {"lot_size", "exchange"}
        self.events: deque = deque()
        self.log: deque = deque(maxlen=300)
        self.listeners: list[Callable[[], Any]] = []
        self._ids = itertools.count(1)
        self._load()

    # ------------------------------------------------------------------ records
    @property
    def file(self) -> Path:
        return self.folder / "risk_trades.json"

    def _load(self) -> None:
        try:
            rows = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        today = self.clock().date().isoformat()
        for r in rows:
            t = Trade(**{k: v for k, v in r.items() if k in {f.name for f in fields(Trade)}})
            if t.day == today or t.state in OPEN_STATES:
                self.trades[t.id] = t
                if t.mode == "REAL" and t.state in OPEN_STATES and "REAL" in self.gw:
                    for oid in (t.entry_order, t.exit_order):
                        if oid:
                            self.gw["REAL"].open_ids.add(oid)
        self._ids = itertools.count(len(self.trades) + 1)

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(t) for t in self.trades.values()], indent=1), encoding="utf-8")
        tmp.replace(self.file)

    def note(self, t: Optional[Trade], text: str) -> None:
        stamp = self.clock().strftime("%H:%M:%S")
        line = f"{stamp} {t.id + ' ' if t else ''}{text}"
        self.log.appendleft(line)
        if t is not None:
            t.notes.append(line)

    def _changed(self) -> None:
        self._save()
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass

    # ------------------------------------------------------------------ inputs from the feed
    def set_instrument(self, symbol: str, lot_size: int, exchange: str) -> None:
        self.instruments[symbol] = {"lot_size": int(lot_size), "exchange": exchange}

    def on_quote(self, symbol: str, quote: dict) -> None:
        with self.lock:
            self.quotes[symbol] = quote
            for g in self.gw.values():
                g.on_quote(symbol, quote)
            self._flush()
            touched = False
            for t in list(self.trades.values()):
                if t.symbol == symbol and (t.state == "ARMED" or t.state in OPEN_STATES):
                    touched |= self._on_price(t, quote["ltp"])
            self._flush()
            if touched:
                self._changed()

    def _queue_event(self, e: OrderEvent) -> None:
        self.events.append(e)

    # ------------------------------------------------------------------ the ticket
    def capital(self, mode: str) -> float:
        if mode == "REAL":
            return float(self.funds() or 0.0)
        s = self.store.load()
        used = sum((t.entry_avg or t.entry) * t.filled_qty for t in self.trades.values()
                   if t.mode == "PAPER" and t.state in OPEN_STATES)
        return max(0.0, s.paper_capital - used)

    def mode(self) -> str:
        s = self.store.load()
        return "REAL" if s.real_orders and "REAL" in self.gw and self.real_allowed() else "PAPER"

    def day_pnl(self) -> float:
        today = self.clock().date().isoformat()
        return round(sum((t.pnl or 0) - (t.charges or 0) for t in self.trades.values()
                         if t.day == today and t.state == "CLOSED"), 2)

    def blocked(self) -> str:
        s = self.store.load()
        if self.kill.active():
            return self.kill.message()
        if s.max_day_loss and self.day_pnl() <= -s.max_day_loss:
            return f"Max loss for the day reached (Rs {self.day_pnl():,.0f} of -{s.max_day_loss:,.0f}): no new trades."
        now = self.clock().time()
        if now >= time.fromisoformat(s.entry_until):
            return f"No new trades after {s.entry_until}."
        return ""

    def ticket(self, symbol: str, entry: float, stop: float, target: float) -> dict:
        """What Arm would do: size, risk, reward, warnings. Sends nothing."""
        s = self.store.load()
        ins = self.instruments.get(symbol)
        mode = self.mode()
        out: dict = {"symbol": symbol, "mode": mode, "ok": False, "warnings": []}
        if not ins:
            out["warnings"].append("unknown instrument (no lot size yet)")
            return out
        entry, stop, target = near(entry), near(stop), near(target)
        size = position_size(capital=self.capital(mode), allocation_pct=s.allocation_pct, max_risk=s.max_risk,
                             entry=entry, stop=stop, lot_size=ins["lot_size"], buffer=s.buffer, strict=s.strict,
                             exchange=ins["exchange"], max_lots=s.real_max_lots if mode == "REAL" else None)
        q = self.quotes.get(symbol)
        out.update(entry=entry, stop=stop, target=target, size=asdict(size), lots=size.lots, qty=size.qty,
                   risk_rs=size.risk_rs, reward_rs=round((target - size.entry_used) * size.qty, 2),
                   rr=round((target - entry) / (entry - stop), 2) if entry > stop else None,
                   ltp=q["ltp"] if q else None, capital=self.capital(mode))
        w = out["warnings"]
        if not (stop < entry < target):
            w.append("the box must be: stop < entry < target")
        if entry - stop < s.min_stop_ticks * TICK:
            w.append(f"stop closer than {s.min_stop_ticks} ticks")
        if size.reason:
            w.append(size.reason)
        if (b := self.blocked()):
            w.append(b)
        out["ok"] = not w and size.lots > 0
        return out

    # ------------------------------------------------------------------ commands from the chart
    def arm(self, symbol: str, entry: float, stop: float, target: float,
            t0: Optional[int] = None) -> tuple[bool, str, Optional[Trade]]:
        with self.lock:
            tk = self.ticket(symbol, entry, stop, target)
            if not tk["ok"]:
                return False, "; ".join(tk["warnings"]) or "not allowed", None
            q = self.quotes.get(symbol)
            ins = self.instruments[symbol]
            tid = f"T{self.clock():%H%M%S}{next(self._ids):02d}"
            t = Trade(tid, self.clock().date().isoformat(), symbol, ins["lot_size"], ins["exchange"], tk["entry"],
                      tk["stop"], tk["target"], tk["lots"], tk["qty"], tk["mode"], sizing=tk["size"],
                      trigger_dir="down" if q and q["ltp"] > tk["entry"] else "up", t0=t0)
            self.trades[tid] = t
            self.note(t, f"ARMED {t.mode}: {t.lots} lot(s) = {t.qty} {symbol}, entry {t.entry:g} ({'price falls to' if t.trigger_dir == 'down' else 'price rises to'} it), "
                         f"stop {t.stop:g}, target {t.target:g}, risk Rs {tk['risk_rs']:,.0f} ({tk['size']['decided_by']})")
            if q and abs(q["ltp"] - t.entry) < TICK / 2:
                self._on_price(t, q["ltp"])
            self._flush()
            self._changed()
            return True, "armed", t

    def modify(self, trade_id: str, *, entry: Optional[float] = None, stop: Optional[float] = None,
               target: Optional[float] = None) -> tuple[bool, str]:
        """A drag released on the chart. Refused (the line goes back) when it breaks a rule."""
        with self.lock:
            t = self.trades.get(trade_id)
            if t is None or t.state in ("CLOSED", "CANCELLED"):
                return False, "trade is closed"
            s = self.store.load()
            ltp = (self.quotes.get(t.symbol) or {}).get("ltp")
            in_trade = t.state in OPEN_STATES
            if entry is not None and in_trade:
                return False, "entry cannot move after the entry order is sent"
            if t.state in ("TARGET_EXITING", "STOP_CHASING", "EXITING"):
                return False, "the exit is already working"
            e = near(entry) if entry is not None else t.entry
            st = near(stop) if stop is not None else t.stop
            tg = near(target) if target is not None else t.target
            if not in_trade and not (st < e < tg):
                return False, "the box must stay: stop < entry < target"
            if in_trade and not st < tg:
                return False, "the stop must stay below the target"
            if in_trade and ltp is not None:
                if stop is not None and st >= ltp:
                    return False, f"stop {st:g} is at or above the price {ltp:g}: use Exit now instead"
                if target is not None and tg <= ltp:
                    return False, f"target {tg:g} is at or below the price {ltp:g}: use Exit now instead"
            qty = t.filled_qty if t.filled_qty else t.qty
            ref_entry = t.entry_avg if t.entry_avg else (e + s.buffer if s.strict else e)
            risk = (ref_entry - (st - s.buffer if s.strict else st)) * qty
            if stop is not None and risk > s.max_risk + 0.01:
                return False, (f"new stop risks Rs {risk:,.0f}, above the max risk Rs {s.max_risk:,.0f} "
                               f"(quantity is never reduced)")
            old = (t.entry, t.stop, t.target)
            if stop is not None and t.exit_order and t.exit_kind == "SL" and t.state in ("ENTRY_WORKING", "IN_TRADE"):
                try:
                    self.gw[t.mode].modify(t.exit_order, stop=st, limit=down(st - s.buffer))
                except OrderRefused as exc:
                    return False, f"FYERS refused the stop change: {exc}"
                t.exit_limit = down(st - s.buffer)
            t.entry, t.stop, t.target = e, st, tg
            if t.state == "ARMED" and entry is not None and ltp is not None:
                t.trigger_dir = "down" if ltp > e else "up"
            self.note(t, f"moved: entry {old[0]:g}->{e:g}, stop {old[1]:g}->{st:g}, target {old[2]:g}->{tg:g}")
            self._flush()
            self._changed()
            return True, "ok"

    def cancel(self, trade_id: str) -> tuple[bool, str]:
        with self.lock:
            t = self.trades.get(trade_id)
            if t is None or t.state != "ARMED":
                return False, "only an armed box (no order yet) can be cancelled - use Exit now"
            t.state = "CANCELLED"
            self.note(t, "cancelled by you")
            self._changed()
            return True, "cancelled"

    def exit_now(self, trade_id: str, reason: str = "exit button") -> tuple[bool, str]:
        with self.lock:
            t = self.trades.get(trade_id)
            if t is None:
                return False, "no such trade"
            if t.state == "ARMED":
                t.state = "CANCELLED"
                self.note(t, f"cancelled ({reason})")
                self._changed()
                return True, "cancelled"
            if t.state not in OPEN_STATES:
                return False, "trade is closed"
            self._exit(t, reason)
            self._flush()
            self._changed()
            return True, "exiting"

    def kill_all(self, *, real_flatten: Optional[Callable[[], Any]] = None) -> list[str]:
        """Kill switch: cancel armed boxes, close paper trades here; real trades are closed by the account
        close-out (killswitch.flatten_account) - their records are closed with the last price."""
        with self.lock:
            done = []
            for t in list(self.trades.values()):
                if t.state == "ARMED":
                    t.state = "CANCELLED"
                    self.note(t, "cancelled by the kill switch")
                elif t.state in OPEN_STATES and t.mode == "PAPER":
                    self._exit(t, "kill switch")
                done.append(t.id)
            self._flush()
            real_open = [t for t in self.trades.values() if t.mode == "REAL" and t.state in OPEN_STATES]
        if real_flatten is not None:              # account level: also covers trades the desk did not open
            real_flatten()
        with self.lock:
            for t in real_open:
                ltp = (self.quotes.get(t.symbol) or {}).get("ltp") or t.entry_avg or t.entry
                self._close(t, ltp, "kill switch (closed by the account close-out - check FYERS for the price)")
            self._changed()
            return done

    # ------------------------------------------------------------------ the engine
    def timer(self) -> None:
        """Every half second: entry timeouts, exit re-pricing, 15:10, daily loss, real order polling."""
        with self.lock:
            s = self.store.load()
            now, mono = self.clock(), self.mono()
            for g in self.gw.values():
                try:
                    g.poll()
                except Exception as exc:
                    self.note(None, f"order status read failed: {str(exc)[:120]}")
            self._flush()
            block = self.blocked()
            for t in list(self.trades.values()):
                ltp = (self.quotes.get(t.symbol) or {}).get("ltp")
                if now.time() >= time.fromisoformat(s.force_exit):
                    if t.state == "ARMED":
                        t.state = "CANCELLED"
                        self.note(t, f"cancelled at {s.force_exit}")
                    elif t.state in ("ENTRY_WORKING", "IN_TRADE"):
                        self._exit(t, f"time exit {s.force_exit}")
                    continue
                if t.state == "ARMED" and block:
                    t.state = "CANCELLED"
                    self.note(t, f"cancelled: {block}")
                elif t.state == "ENTRY_WORKING" and mono - t.entry_sent >= s.entry_timeout:
                    self._entry_timeout(t, ltp, s)
                elif t.state in ("TARGET_EXITING", "STOP_CHASING", "EXITING") and ltp is not None and \
                        mono - t.exit_sent >= s.chase_every:
                    new = down(ltp - s.buffer)
                    if new < t.exit_limit:
                        self._modify_exit(t, "LIMIT", new)
                        t.chase_steps += 1
                        self.note(t, f"exit not filled: limit lowered to {new:g} (step {t.chase_steps})")
                        if t.chase_steps in (10, 20, 40):
                            self.note(t, "ALERT: exit still not filled - check FYERS now")
                    t.exit_sent = mono
            self._flush()
            self._changed()

    def _on_price(self, t: Trade, ltp: float) -> bool:
        s = self.store.load()
        if t.state == "ARMED":
            if self.blocked() or self.clock().time() < time.fromisoformat(s.entry_from):
                return False
            reached = ltp >= t.entry if t.trigger_dir == "up" else ltp <= t.entry
            if not reached:
                return False
            limit = up(ltp + s.buffer)
            try:
                oid = self.gw[t.mode].place(symbol=t.symbol, side="BUY", qty=t.qty, kind="LIMIT", limit=limit,
                                            tag=f"KFTR{t.id}")
            except OrderRefused as exc:
                t.state = "CANCELLED"
                self.note(t, f"entry refused: {exc}")
                return True
            t.state, t.entry_order, t.entry_limit, t.entry_first = "ENTRY_WORKING", oid, limit, limit
            t.entry_sent, t.opened_at = self.mono(), self.clock().strftime("%H:%M:%S")
            self.note(t, f"price {ltp:g} reached the entry: BUY LIMIT {t.qty} at {limit:g} sent ({oid})")
            return True
        if t.state == "IN_TRADE":
            if ltp >= t.target:
                self._modify_exit(t, "LIMIT", down(min(t.target, ltp) - s.buffer))
                t.state, t.exit_reason = "TARGET_EXITING", "target"
                self.note(t, f"target {t.target:g} reached ({ltp:g}): exit order changed to LIMIT SELL "
                             f"{t.exit_limit:g}")
                return True
            if t.exit_kind == "SL" and ltp < t.exit_limit:
                # triggered and jumped below the SL-L limit: follow the price down (never market)
                self._modify_exit(t, "LIMIT", down(ltp - s.buffer))
                t.state, t.exit_reason = "STOP_CHASING", "stop loss (price jumped below the limit)"
                self.note(t, f"price {ltp:g} below the stop limit: chasing with LIMIT SELL {t.exit_limit:g}")
                return True
        return False

    def _entry_timeout(self, t: Trade, ltp: Optional[float], s: DeskSettings) -> None:
        g = self.gw[t.mode]
        if not t.entry_repriced and ltp is not None:
            new = min(up(ltp + s.buffer), up(t.entry_first + 1.0))
            t.entry_repriced, t.entry_sent = True, self.mono()
            if new > t.entry_limit:
                try:
                    g.modify(t.entry_order, limit=new)
                    t.entry_limit = new
                    self.note(t, f"entry not filled: limit raised to {new:g} (once, at most Rs 1 above the first)")
                except OrderRefused as exc:
                    self.note(t, f"entry re-price refused: {exc}")
            return
        try:
            g.cancel(t.entry_order)
        except OrderRefused as exc:
            self.note(t, f"cancel of the entry refused: {exc} - CHECK FYERS")
            return
        self._flush()
        if t.state == "ENTRY_WORKING":
            if t.filled_qty:
                t.state, t.qty = "IN_TRADE", t.filled_qty
                self.note(t, f"entry partly filled ({t.filled_qty}); rest cancelled")
            else:
                t.state = "CANCELLED"
                self.note(t, "entry not filled in time: cancelled (no trade)")

    def _exit(self, t: Trade, reason: str) -> None:
        s = self.store.load()
        ltp = (self.quotes.get(t.symbol) or {}).get("ltp") or t.entry_avg or t.entry
        if t.state == "ENTRY_WORKING" and t.entry_order:
            try:
                self.gw[t.mode].cancel(t.entry_order)
            except OrderRefused:
                pass
            self._flush()
            if not t.filled_qty:
                t.state = "CANCELLED"
                self.note(t, f"entry cancelled ({reason})")
                return
        t.exit_reason = reason
        self._modify_exit(t, "LIMIT", down(ltp - s.buffer))
        t.state = "EXITING"
        self.note(t, f"{reason}: LIMIT SELL {t.exit_limit:g}")

    def _modify_exit(self, t: Trade, kind: str, limit: float, stop: Optional[float] = None) -> None:
        g = self.gw[t.mode]
        qty = t.filled_qty - t.exit_filled
        if qty <= 0:
            return
        try:
            if t.exit_order:
                g.modify(t.exit_order, kind=kind, limit=limit, stop=stop, qty=qty if kind == "LIMIT" else None)
            else:
                t.exit_order = g.place(symbol=t.symbol, side="SELL", qty=qty, kind=kind, limit=limit,
                                       stop=stop or 0.0, tag=f"KFTX{t.id}")
            t.exit_kind, t.exit_limit, t.exit_sent = kind, limit, self.mono()
        except OrderRefused as exc:
            self.note(t, f"ALERT: exit order change refused: {exc} - CHECK FYERS")

    def _close(self, t: Trade, price: float, reason: str) -> None:
        t.state = "CLOSED"
        t.exit_avg = t.exit_avg or round(price, 2)
        t.exit_reason = t.exit_reason or reason
        t.closed_at = self.clock().strftime("%H:%M:%S")
        qty = t.filled_qty
        t.pnl = round((t.exit_avg - (t.entry_avg or 0)) * qty, 2)
        t.charges = charges_estimate(t.entry_avg or 0, t.exit_avg, qty, t.exchange) if qty else 0.0
        self.note(t, f"CLOSED ({t.exit_reason}): sold {qty} at {t.exit_avg:g}, P&L Rs {t.pnl:,.2f} "
                     f"(charges about Rs {t.charges:,.0f})")

    def _flush(self) -> None:
        """Apply the order events (fills, cancels, rejects) that came back from the gateways."""
        s = self.store.load()
        while self.events:
            e = self.events.popleft()
            for t in self.trades.values():
                if e.order_id == t.entry_order:
                    self._entry_event(t, e, s)
                    break
                if e.order_id == t.exit_order:
                    self._exit_event(t, e)
                    break

    def _entry_event(self, t: Trade, e: OrderEvent, s: DeskSettings) -> None:
        if e.filled_qty > t.filled_qty:
            t.entry_avg = e.avg_price or t.entry_limit
            t.filled_qty = e.filled_qty
            ltp = (self.quotes.get(t.symbol) or {}).get("ltp") or t.entry_avg
            self.note(t, f"entry FILLED {t.filled_qty} at {t.entry_avg:g}")
            if t.exit_order is None and ltp <= t.stop:
                t.exit_reason = "price at or below the stop when the entry filled"
                self._modify_exit(t, "LIMIT", down(ltp - s.buffer))
                t.state = "EXITING"
            elif t.exit_order is None:
                self._modify_exit(t, "SL", down(t.stop - s.buffer), stop=t.stop)
                self.note(t, f"protection placed: SELL SL-L trigger {t.stop:g} limit {t.exit_limit:g}")
            elif t.exit_kind == "SL":
                self.gw[t.mode].modify(t.exit_order, qty=t.filled_qty - t.exit_filled)
            if e.status == "FILLED" and t.state == "ENTRY_WORKING":
                t.state = "IN_TRADE"
        elif e.status in ("CANCELLED", "REJECTED") and t.state == "ENTRY_WORKING" and not t.filled_qty:
            t.state = "CANCELLED"
            self.note(t, f"entry {e.status.lower()}: {e.message}")

    def _exit_event(self, t: Trade, e: OrderEvent) -> None:
        if e.filled_qty > t.exit_filled:
            new = e.filled_qty - t.exit_filled
            prev = (t.exit_avg or 0) * t.exit_filled
            t.exit_filled = e.filled_qty
            t.exit_avg = round((prev + (e.avg_price or t.exit_limit) * new) / t.exit_filled, 2)
            if t.exit_filled >= t.filled_qty:
                if not t.exit_reason:
                    t.exit_reason = "stop loss (SL-L at the exchange)"
                self._close(t, t.exit_avg, t.exit_reason)
        elif e.status in ("CANCELLED", "REJECTED") and t.state != "CLOSED":
            t.exit_order = None
            self.note(t, f"ALERT: exit order {e.status.lower()} ({e.message}) - position NOT protected, "
                         f"CHECK FYERS")

    # ------------------------------------------------------------------ for the screen
    def snapshot(self) -> dict:
        with self.lock:
            s = self.store.load()
            today = self.clock().date().isoformat()
            rows = []
            for t in self.trades.values():
                if t.day != today and t.state not in OPEN_STATES:
                    continue
                ltp = (self.quotes.get(t.symbol) or {}).get("ltp")
                d = asdict(t)
                d["ltp"] = ltp
                d["open_pnl"] = round((ltp - t.entry_avg) * (t.filled_qty - t.exit_filled), 2) \
                    if ltp and t.entry_avg and t.state in OPEN_STATES else None
                d["notes"] = t.notes[-8:]
                rows.append(d)
            return {"trades": rows, "settings": asdict(s), "mode": self.mode(), "day_pnl": self.day_pnl(),
                    "blocked": self.blocked(), "kill": self.kill.message(), "log": list(self.log)[:40],
                    "capital": {m: self.capital(m) for m in self.gw}}


__all__ = ["Desk", "DeskSettings", "DeskSettingsStore", "OPEN_STATES", "Trade"]
