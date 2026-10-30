"""The risk desk running for real: desk + FYERS live feed + local fast link to the chart page + kill switch.

The chart page talks to this program over a websocket on THIS PC only (127.0.0.1), with a secret key in the
address and an origin check, so no other web page can send orders through it. Ticks go FYERS -> here -> chart in
milliseconds; an order change is one FYERS API call (FYERS answers in about 100-300 ms).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import threading
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from terminal.broker import real_orders_allowed
from terminal.candles import IST
from terminal.fyers import INDEXES, chain_strikes, data_dir, load_master, nearest_contracts
from terminal.killswitch import FYERS_KILL_HELP, KillSwitch, flatten_account
from terminal.risk.desk import Desk
from terminal.risk.feed import Feed
from terminal.risk.gateways import FyersGateway, PaperGateway

WEB = Path(__file__).resolve().parent / "web"
LIB = Path(__file__).resolve().parents[2] / "static" / "lightweight-charts" / "lightweight-charts.standalone.production.js"


def free_port(start: int = 8765) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port for the risk desk")


class RiskService:
    def __init__(self, *, client_id: str, token: str, client, terminal=None, folder: Optional[Path] = None,
                 port: Optional[int] = None, start_feed: bool = True, master_loader=None) -> None:
        self.folder = Path(folder or data_dir())
        self.client, self.terminal = client, terminal
        self.key = secrets.token_urlsafe(18)
        self.port = port or free_port()
        self.master_loader = master_loader or (lambda name, d: load_master(name, d))
        self.kill_switch = KillSwitch(self.folder)
        paper = PaperGateway(self.folder / "risk_orders_paper.jsonl", self.kill_switch)
        model = getattr(client, "model", None)
        self.real = FyersGateway(model, self.folder / "risk_orders_real.jsonl", self.kill_switch) if model else None
        self._funds = (0.0, 0.0)
        self.desk = Desk(paper=paper, real=self.real, folder=self.folder, funds=self.funds,
                         real_allowed=lambda: real_orders_allowed(True, self.folder, datetime.now(IST).date()))
        self.desk.listeners.append(self.push_state)
        self.feed = Feed(client_id=client_id, token=token, client=client, on_quote=self._on_quote,
                         on_order=self.real.handle if self.real else None, log=lambda m: self.desk.note(None, m),
                         log_path=str(self.folder / "logs"))
        self.start_feed = start_feed
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.queues: set = set()
        self._chains: dict[str, dict] = {}
        self._threads_started = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?k={self.key}"

    # ------------------------------------------------------------------ data
    def funds(self) -> float:
        value, at = self._funds
        if _time.monotonic() - at > 15:
            try:
                value = float(self.client.available_funds())
            except Exception:
                pass
            self._funds = (value, _time.monotonic())
        return value

    def _on_quote(self, symbol: str, quote: dict) -> None:
        self.desk.on_quote(symbol, quote)
        self.broadcast({"type": "tick", "symbol": symbol, "ltp": quote["ltp"], "bid": quote.get("bid"),
                        "ask": quote.get("ask"), "rx": quote.get("rx"), "src": quote.get("src")})

    def chain(self, index: str) -> dict:
        info = INDEXES[index]
        today = datetime.now(IST).date()
        c = nearest_contracts(self.master_loader(info.master, today), info.underlying, today)
        spot = self.client.quotes([info.spot]).get(info.spot, {}).get("ltp")
        strikes = chain_strikes(c, spot or 0, info.chain_width) if spot else []
        syms = [c.calls[k] for k in strikes] + [c.puts[k] for k in strikes]
        quotes = self.client.quotes(syms) if syms else {}
        exch = info.spot.split(":")[0]
        for sym in syms:
            self.desk.set_instrument(sym, c.lot_size, exch)
        rows = [{"strike": k, "ce": c.calls[k], "pe": c.puts[k],
                 "ce_ltp": (quotes.get(c.calls[k]) or {}).get("ltp"),
                 "pe_ltp": (quotes.get(c.puts[k]) or {}).get("ltp")} for k in strikes]
        out = {"index": index, "label": info.label, "spot": spot, "expiry": c.expiry.isoformat(),
               "lot_size": c.lot_size, "rows": rows}
        self._chains[index] = out
        return out

    def history(self, symbol: str) -> list[dict]:
        today = datetime.now(IST).date()
        bars = self.client.minutes(symbol, today - timedelta(days=4), today)
        self.feed.subscribe([symbol])
        return [{"t": b.ts, "o": b.open, "h": b.high, "l": b.low, "c": b.close} for b in bars[-1500:]]

    # ------------------------------------------------------------------ kill switch
    def kill(self) -> dict:
        until = self.kill_switch.activate("kill switch button")
        real_on = self.desk.store.load().real_orders
        if self.terminal is not None:
            try:
                from terminal.settings import SettingsStore
                real_on = real_on or SettingsStore(self.terminal.folder).load().real_orders
                self.terminal.kill()
            except Exception as exc:
                self.desk.note(None, f"kill: BBP engine: {exc}")
        report: dict = {}
        model = getattr(self.client, "model", None)
        flatten = None
        if real_on and model is not None:
            def flatten():
                report.update(flatten_account(model, buffer=self.desk.store.load().buffer,
                                              log=lambda m: self.desk.note(None, m)))
        self.desk.kill_all(real_flatten=flatten)
        msg = (f"KILL SWITCH ON until {until:%d %b %H:%M}. "
               + ("FYERS account: every pending order cancelled and every position closed with limit orders. "
                  if flatten else "Paper mode: paper trades closed, the FYERS account was not touched. ")
               + FYERS_KILL_HELP)
        self.desk.note(None, msg)
        self.push_state()
        return {"message": msg, "report": report}

    # ------------------------------------------------------------------ fast link
    def broadcast(self, msg: dict) -> None:
        loop = self.loop
        if loop is None or not self.queues:
            return
        text = json.dumps(msg, default=str)
        for q in list(self.queues):
            try:
                loop.call_soon_threadsafe(q.put_nowait, text)
            except Exception:
                pass

    def push_state(self) -> None:
        if self.queues:
            self.broadcast({"type": "state", **self.desk.snapshot(), "feed": self.feed.mode})

    def handle(self, msg: dict) -> dict:
        """One command from the chart page. Runs in a worker thread (FYERS calls never block the price link)."""
        op = msg.get("op")
        d = self.desk
        f = lambda v: None if v in (None, "") else float(v)
        if op == "chain":
            return {"type": "chain", **self.chain(msg["index"])}
        if op == "history":
            return {"type": "history", "symbol": msg["symbol"], "bars": self.history(msg["symbol"])}
        if op == "ticket":
            return {"type": "ticket", "ref": msg.get("ref"),
                    **d.ticket(msg["symbol"], float(msg["entry"]), float(msg["stop"]), float(msg["target"]))}
        if op == "arm":
            ok, text, t = d.arm(msg["symbol"], float(msg["entry"]), float(msg["stop"]), float(msg["target"]),
                                t0=int(msg["t0"]) if msg.get("t0") else None)
            return {"type": "result", "op": op, "ok": ok, "msg": text, "id": t.id if t else None}
        if op == "modify":
            ok, text = d.modify(msg["id"], entry=f(msg.get("entry")), stop=f(msg.get("stop")),
                                target=f(msg.get("target")))
            if not ok:
                self.push_state()                           # the line jumps back to what is really held
            return {"type": "result", "op": op, "ok": ok, "msg": text, "id": msg["id"]}
        if op == "cancel":
            ok, text = d.cancel(msg["id"])
            return {"type": "result", "op": op, "ok": ok, "msg": text}
        if op == "exit":
            ok, text = d.exit_now(msg["id"])
            return {"type": "result", "op": op, "ok": ok, "msg": text}
        if op == "kill":
            if msg.get("confirm") != "KILL":
                return {"type": "result", "op": op, "ok": False, "msg": "type KILL to confirm"}
            return {"type": "result", "op": op, "ok": True, "msg": self.kill()["message"]}
        if op == "settings":
            values = msg.get("values") or {}
            if "max_day_loss" in values and values["max_day_loss"] in ("", None, 0, "0"):
                values["max_day_loss"] = None
            try:
                d.store.update(**values)
            except (ValueError, TypeError) as exc:
                return {"type": "result", "op": op, "ok": False, "msg": str(exc)}
            self.push_state()
            return {"type": "result", "op": op, "ok": True, "msg": "settings saved"}
        if op == "state":
            return {"type": "state", **d.snapshot(), "feed": self.feed.mode}
        return {"type": "result", "op": op, "ok": False, "msg": "unknown command"}

    def app(self):
        from starlette.applications import Starlette
        from starlette.concurrency import run_in_threadpool
        from starlette.responses import HTMLResponse, PlainTextResponse, Response
        from starlette.routing import Route, WebSocketRoute
        from starlette.websockets import WebSocket, WebSocketDisconnect

        allowed = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

        async def page(request):
            if request.query_params.get("k") != self.key:
                return PlainTextResponse("Open the risk desk from the terminal (key missing).", status_code=403)
            html = (WEB / "desk.html").read_text(encoding="utf-8")
            return HTMLResponse(html.replace("__KEY__", self.key))

        async def asset(request):
            name = request.path_params["name"]
            if name == "lwc.js":
                return Response(LIB.read_bytes(), media_type="application/javascript")
            if name == "desk.js":
                return Response((WEB / "desk.js").read_bytes(), media_type="application/javascript")
            return PlainTextResponse("not found", status_code=404)

        async def ws_endpoint(ws: WebSocket):
            origin = ws.headers.get("origin")
            if ws.query_params.get("k") != self.key or (origin and origin not in allowed):
                await ws.close(code=4403)
                return
            await ws.accept()
            self.loop = asyncio.get_running_loop()
            q: asyncio.Queue = asyncio.Queue(maxsize=5000)
            self.queues.add(q)

            async def sender():
                while True:
                    await ws.send_text(await q.get())

            task = asyncio.create_task(sender())
            try:
                await ws.send_text(json.dumps({"type": "state", **self.desk.snapshot(), "feed": self.feed.mode},
                                              default=str))
                while True:
                    msg = json.loads(await ws.receive_text())
                    try:
                        reply = await run_in_threadpool(self.handle, msg)
                    except Exception as exc:
                        reply = {"type": "result", "op": msg.get("op"), "ok": False, "msg": f"error: {exc}"}
                    await ws.send_text(json.dumps(reply, default=str))
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                task.cancel()
                self.queues.discard(q)

        return Starlette(routes=[Route("/", page), Route("/a/{name}", asset), WebSocketRoute("/ws", ws_endpoint)])

    def start(self) -> None:
        if self._threads_started:
            return
        self._threads_started = True
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(self.app(), host="127.0.0.1", port=self.port, log_level="warning",
                                               ws="auto"))
        threading.Thread(target=server.run, name="risk-desk-server", daemon=True).start()
        threading.Thread(target=self._timer, name="risk-desk-timer", daemon=True).start()
        if self.start_feed:
            self.feed.start()
            self.feed.subscribe({t.symbol for t in self.desk.trades.values()})

    def _timer(self) -> None:
        while True:
            _time.sleep(0.5)
            try:
                self.desk.timer()                       # saves and pushes the state to the chart
            except Exception as exc:
                self.desk.note(None, f"desk timer problem: {str(exc)[:150]}")


__all__ = ["RiskService"]
