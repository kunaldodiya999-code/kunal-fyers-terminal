"""Live prices and order updates from FYERS, as fast as FYERS sends them (owner: milliseconds, not seconds).

  * FYERS data websocket (SymbolUpdate): every trade tick of the subscribed symbols -> desk.on_quote at once;
  * FYERS order websocket (OnOrders): fills / cancels / rejects of REAL orders -> FyersGateway.handle at once;
  * safety net: a symbol with no tick for 3 seconds is read with a REST quote every second (and the screen says so).
"""

from __future__ import annotations

import threading
import time as _time
from typing import Any, Callable, Iterable, Optional


class Feed:
    def __init__(self, *, client_id: str, token: str, client, on_quote: Callable[[str, dict], Any],
                 on_order: Optional[Callable[[dict], Any]] = None, log: Callable[[str], Any] = print,
                 log_path: str = "") -> None:
        self.client_id, self.token, self.client = client_id, token, client
        self.on_quote_cb, self.on_order_cb, self.log, self.log_path = on_quote, on_order, log, log_path
        self.symbols: set[str] = set()
        self.last_tick: dict[str, float] = {}
        self.ws = None
        self.ows = None
        self.connected = False
        self.mode = "starting"
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ websockets
    def start(self) -> None:
        threading.Thread(target=self._start_data, name="fyers-data-ws", daemon=True).start()
        if self.on_order_cb is not None:
            threading.Thread(target=self._start_orders, name="fyers-order-ws", daemon=True).start()
        threading.Thread(target=self._safety_net, name="fyers-rest-net", daemon=True).start()

    def _start_data(self) -> None:
        try:
            from fyers_apiv3.FyersWebsocket import data_ws
            self.ws = data_ws.FyersDataSocket(access_token=f"{self.client_id}:{self.token}", log_path=self.log_path,
                                              litemode=False, write_to_file=False, reconnect=True,
                                              on_connect=self._on_connect, on_close=self._on_close,
                                              on_error=lambda m: self.log(f"price feed error: {m}"),
                                              on_message=self._on_message)
            self.ws.connect()
        except Exception as exc:
            self.mode = "REST (websocket failed)"
            self.log(f"FYERS price websocket did not start ({exc}); using REST quotes every second")

    def _on_connect(self) -> None:
        self.connected, self.mode = True, "websocket"
        self.log("FYERS live price websocket connected")
        with self._lock:
            syms = sorted(self.symbols)
        if syms:
            self.ws.subscribe(symbols=syms, data_type="SymbolUpdate")

    def _on_close(self, msg) -> None:
        self.connected, self.mode = False, "REST (websocket closed)"
        self.log(f"FYERS price websocket closed: {msg}")

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or "ltp" not in msg or not msg.get("symbol"):
            return
        sym = msg["symbol"]
        self.last_tick[sym] = _time.monotonic()
        self.on_quote_cb(sym, {"ltp": float(msg["ltp"]), "bid": float(msg.get("bid_price") or 0),
                               "ask": float(msg.get("ask_price") or 0), "t": msg.get("last_traded_time"),
                               "src": "ws", "rx": _time.time()})

    def _start_orders(self) -> None:
        try:
            from fyers_apiv3.FyersWebsocket import order_ws
            self.ows = order_ws.FyersOrderSocket(access_token=f"{self.client_id}:{self.token}",
                                                 log_path=self.log_path, write_to_file=False,
                                                 on_orders=self._on_orders,
                                                 on_error=lambda m: self.log(f"order feed error: {m}"),
                                                 on_connect=lambda: self.ows.subscribe(data_type="OnOrders"),
                                                 on_close=lambda m: self.log(f"order websocket closed: {m}"))
            self.ows.connect()
        except Exception as exc:
            self.log(f"FYERS order websocket did not start ({exc}); order book is read every second instead")

    def _on_orders(self, msg: Any) -> None:
        if isinstance(msg, dict) and isinstance(msg.get("orders"), dict):
            self.on_order_cb(msg["orders"])

    # ------------------------------------------------------------------ symbols
    def subscribe(self, symbols: Iterable[str]) -> None:
        new = []
        with self._lock:
            for s in symbols:
                if s and s not in self.symbols:
                    self.symbols.add(s)
                    new.append(s)
        if new and self.ws is not None and self.connected:
            try:
                self.ws.subscribe(symbols=new, data_type="SymbolUpdate")
            except Exception as exc:
                self.log(f"subscribe failed ({exc}); REST quotes cover it")

    def _safety_net(self) -> None:
        while not self._stop.is_set():
            _time.sleep(1.0)
            now = _time.monotonic()
            with self._lock:
                stale = [s for s in self.symbols if now - self.last_tick.get(s, 0) > 3.0]
            if not stale:
                continue
            try:
                for sym, q in self.client.quotes(stale).items():
                    self.on_quote_cb(sym, {**q, "src": "rest", "rx": _time.time()})
            except Exception as exc:
                self.log(f"REST quotes failed: {str(exc)[:120]}")

    def stop(self) -> None:
        self._stop.set()


__all__ = ["Feed"]
