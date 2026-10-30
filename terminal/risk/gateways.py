"""Where the desk's orders go: PAPER (simulated on live FYERS prices) or REAL (FYERS API v3). Never market orders.

Both speak the same small language: place(LIMIT or SL-L), modify, cancel; fills come back as OrderEvent through
``listener``. Paper fills: a LIMIT BUY fills when the ask is at or below the limit (at the ask); a LIMIT SELL when the
bid is at or above it (at the bid); an SL-L SELL triggers when the price trades at or below its trigger and then
behaves like a limit sell (it can stay unfilled when the price jumps below the limit - the desk then chases).
"""

from __future__ import annotations

import itertools
import json
import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from terminal.candles import IST
from terminal.killswitch import KillSwitch

FYERS_STATUS = {1: "CANCELLED", 2: "FILLED", 4: "PENDING", 5: "REJECTED", 6: "PENDING", 7: "CANCELLED"}


class OrderRefused(RuntimeError):
    pass


@dataclass
class OrderEvent:
    order_id: str
    status: str                 # PENDING / FILLED / CANCELLED / REJECTED
    filled_qty: int
    avg_price: Optional[float]
    message: str = ""


def _journal(path: Path, **row) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": datetime.now(IST).isoformat(timespec="milliseconds"), **row}) + "\n")


class PaperGateway:
    mode = "PAPER"

    def __init__(self, journal: Path, kill: KillSwitch) -> None:
        self.journal, self.kill = journal, kill
        self.orders: dict[str, dict] = {}
        self.quotes: dict[str, dict] = {}
        self.listener: Callable[[OrderEvent], Any] = lambda e: None
        self._ids = itertools.count(1)

    def place(self, *, symbol: str, side: str, qty: int, kind: str, limit: float, stop: float = 0.0,
              tag: str = "KFTR") -> str:
        if side == "BUY" and self.kill.active():
            raise OrderRefused(self.kill.message())
        oid = f"P{next(self._ids):05d}"
        self.orders[oid] = {"symbol": symbol, "side": side, "qty": int(qty), "kind": kind, "limit": float(limit),
                            "stop": float(stop), "filled": 0, "avg": None, "status": "PENDING", "triggered": False}
        _journal(self.journal, mode="PAPER", action="PLACE", order_id=oid, **self.orders[oid])
        self._try(oid)
        return oid

    def modify(self, order_id: str, *, kind: Optional[str] = None, limit: Optional[float] = None,
               stop: Optional[float] = None, qty: Optional[int] = None) -> None:
        o = self.orders.get(order_id)
        if o is None or o["status"] != "PENDING":
            raise OrderRefused(f"order {order_id} is not pending")
        for k, v in (("kind", kind), ("limit", limit), ("stop", stop), ("qty", qty)):
            if v is not None:
                o[k] = v
        _journal(self.journal, mode="PAPER", action="MODIFY", order_id=order_id, kind=o["kind"], limit=o["limit"],
                 stop=o["stop"], qty=o["qty"])
        self._try(order_id)

    def cancel(self, order_id: str) -> None:
        o = self.orders.get(order_id)
        if o is None or o["status"] != "PENDING":
            return
        o["status"] = "CANCELLED"
        _journal(self.journal, mode="PAPER", action="CANCEL", order_id=order_id)
        self.listener(OrderEvent(order_id, "CANCELLED", o["filled"], o["avg"], "cancelled"))

    def on_quote(self, symbol: str, quote: dict) -> None:
        self.quotes[symbol] = quote
        for oid, o in list(self.orders.items()):
            if o["symbol"] == symbol and o["status"] == "PENDING":
                self._try(oid)

    def _try(self, oid: str) -> None:
        o = self.orders[oid]
        q = self.quotes.get(o["symbol"])
        if not q or o["status"] != "PENDING":
            return
        ltp = q["ltp"]
        bid, ask = q.get("bid") or ltp, q.get("ask") or ltp
        price = None
        if o["kind"] == "SL" and o["side"] == "SELL":
            if not o["triggered"] and ltp <= o["stop"]:
                o["triggered"] = True
            if o["triggered"] and bid >= o["limit"]:
                price = bid
        elif o["side"] == "BUY" and ask <= o["limit"]:
            price = ask
        elif o["side"] == "SELL" and o["kind"] == "LIMIT" and bid >= o["limit"]:
            price = bid
        if price is not None:
            o.update(filled=o["qty"], avg=round(price, 2), status="FILLED")
            _journal(self.journal, mode="PAPER", action="FILL", order_id=oid, price=price, qty=o["qty"])
            self.listener(OrderEvent(oid, "FILLED", o["qty"], o["avg"], "paper fill"))

    def poll(self) -> None:
        pass


class FyersGateway:
    """Real FYERS orders. Limit = type 1, SL-L = type 4. productType INTRADAY, validity DAY. Every call journaled."""
    mode = "REAL"

    def __init__(self, model: Any, journal: Path, kill: KillSwitch, *, pace: float = 0.12) -> None:
        self.model, self.journal, self.kill, self.pace = model, journal, kill, pace
        self.listener: Callable[[OrderEvent], Any] = lambda e: None
        self.open_ids: set[str] = set()
        self._last = 0.0
        self._lock = threading.Lock()
        self._seen: dict[str, tuple] = {}

    def _call(self, action: str, fn, data: dict) -> dict:
        with self._lock:
            wait = self.pace - (_time.monotonic() - self._last)
            if wait > 0:
                _time.sleep(wait)
            t0 = _time.monotonic()
            try:
                r = fn(data=data)
            finally:
                self._last = _time.monotonic()
        ok = isinstance(r, dict) and r.get("s") == "ok"
        _journal(self.journal, mode="REAL", action=action, request=data, ok=ok,
                 ms=round((_time.monotonic() - t0) * 1000), answer=r if isinstance(r, dict) else str(r))
        if not ok:
            raise OrderRefused(f"FYERS refused {action}: {r.get('message') if isinstance(r, dict) else r}")
        return r

    def place(self, *, symbol: str, side: str, qty: int, kind: str, limit: float, stop: float = 0.0,
              tag: str = "KFTR") -> str:
        if side == "BUY" and self.kill.active():
            raise OrderRefused(self.kill.message())
        data = {"symbol": symbol, "qty": int(qty), "type": 4 if kind == "SL" else 1,
                "side": 1 if side == "BUY" else -1, "productType": "INTRADAY", "limitPrice": float(limit),
                "stopPrice": float(stop) if kind == "SL" else 0, "validity": "DAY", "disclosedQty": 0,
                "offlineOrder": False, "orderTag": "".join(ch for ch in tag if ch.isalnum())[:20] or "KFTR"}
        r = self._call("PLACE", self.model.place_order, data)
        if not r.get("id"):
            raise OrderRefused("FYERS did not return an order id - check the FYERS order book (nothing re-sent)")
        self.open_ids.add(str(r["id"]))
        return str(r["id"])

    def modify(self, order_id: str, *, kind: Optional[str] = None, limit: Optional[float] = None,
               stop: Optional[float] = None, qty: Optional[int] = None) -> None:
        data: dict = {"id": order_id}
        if kind is not None:
            data["type"] = 4 if kind == "SL" else 1
        if limit is not None:
            data["limitPrice"] = float(limit)
        if stop is not None:
            data["stopPrice"] = float(stop)
        if qty is not None:
            data["qty"] = int(qty)
        self._call("MODIFY", self.model.modify_order, data)

    def cancel(self, order_id: str) -> None:
        self._call("CANCEL", self.model.cancel_order, {"id": order_id})

    def on_quote(self, symbol: str, quote: dict) -> None:
        pass

    def handle(self, row: dict) -> None:
        """One order row (FYERS order websocket 'orders' or an orderbook row) -> OrderEvent for the desk."""
        oid = str(row.get("id") or "")
        if oid not in self.open_ids:
            return
        status = FYERS_STATUS.get(int(row.get("status") or 0), "PENDING")
        filled = int(row.get("filledQty") or 0)
        price = row.get("tradedPrice")
        key = (status, filled)
        if self._seen.get(oid) == key:
            return
        self._seen[oid] = key
        if status in ("FILLED", "CANCELLED", "REJECTED"):
            self.open_ids.discard(oid)
        self.listener(OrderEvent(oid, status, filled, float(price) if price not in (None, "", 0) else None,
                                 str(row.get("message") or "")))

    def poll(self) -> None:
        """Back-up for the order websocket: read our open orders from the order book."""
        if not self.open_ids:
            return
        r = self.model.orderbook() or {}
        for row in r.get("orderBook") or []:
            self.handle(row)


__all__ = ["FyersGateway", "OrderEvent", "OrderRefused", "PaperGateway"]
