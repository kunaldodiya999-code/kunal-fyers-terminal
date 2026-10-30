"""The fast link chart <-> desk: secret key + origin check, ticket/arm/modify/kill over the websocket, and how many
milliseconds a FYERS tick takes to reach the chart."""

import asyncio
import json
import time
from datetime import date, datetime, timedelta

import pytest
import websockets

from terminal.candles import IST, Candle
from terminal.risk.service import RiskService


def master():
    exp = int(datetime.combine(date.today() + timedelta(days=5), datetime.min.time(), IST).timestamp()) + 55800
    out = {}
    for k in range(24500, 25550, 50):
        for t in ("CE", "PE"):
            out[f"NSE:NIFTY26X{k}{t}"] = {"underSym": "NIFTY", "optType": t, "strikePrice": k, "expiryDate": exp,
                                          "symTicker": f"NSE:NIFTY26X{k}{t}", "minLotSize": 65}
    return out


class Client:
    model = None

    def quotes(self, symbols):
        return {s: {"ltp": 25000.0 if "INDEX" in s else 100.0, "bid": 0, "ask": 0} for s in symbols}

    def minutes(self, symbol, start, end):
        t0 = int(time.time()) // 60 * 60 - 3000
        return [Candle(t0 + 60 * i, 100, 101, 99, 100) for i in range(50)]

    def available_funds(self):
        return 0.0


@pytest.fixture
def svc(tmp_path):
    s = RiskService(client_id="X-100", token="t", client=Client(), folder=tmp_path, start_feed=False,
                    master_loader=lambda n, d: master())
    s.desk.store.update(paper_capital=100000, allocation_pct=50, max_risk=5000, entry_from="00:00",
                        entry_until="23:58", force_exit="23:59")
    s.start()
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{s.port}/?k={s.key}", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    return s


async def recv_until(ws, kind, timeout=3.0, cond=lambda m: True):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=end - time.monotonic()))
        if m.get("type") == kind and cond(m):
            return m
    raise TimeoutError(kind)


def test_key_and_origin_are_required(svc):
    async def go():
        with pytest.raises(Exception):
            async with websockets.connect(f"ws://127.0.0.1:{svc.port}/ws?k=wrong") as ws:
                await ws.recv()
        with pytest.raises(Exception):
            async with websockets.connect(f"ws://127.0.0.1:{svc.port}/ws?k={svc.key}",
                                          origin="http://evil.example") as ws:
                await ws.recv()
    asyncio.run(go())


def test_box_over_the_link_and_tick_latency(svc):
    async def go():
        url = f"ws://127.0.0.1:{svc.port}/ws?k={svc.key}"
        async with websockets.connect(url, origin=f"http://127.0.0.1:{svc.port}") as ws:
            await recv_until(ws, "state")
            await ws.send(json.dumps({"op": "chain", "index": "NIFTY"}))
            chain = await recv_until(ws, "chain")
            assert len(chain["rows"]) == 11 and chain["lot_size"] == 65
            sym = chain["rows"][5]["ce"]
            await ws.send(json.dumps({"op": "history", "symbol": sym}))
            assert len((await recv_until(ws, "history"))["bars"]) == 50
            svc._on_quote(sym, {"ltp": 95.0, "bid": 94.5, "ask": 95.5, "rx": time.time()})
            t0 = time.perf_counter()
            svc._on_quote(sym, {"ltp": 96.0, "bid": 95.5, "ask": 96.5, "rx": time.time()})
            await recv_until(ws, "tick", cond=lambda m: m["ltp"] == 96.0)
            ms = (time.perf_counter() - t0) * 1000
            print(f"tick -> chart: {ms:.1f} ms")
            assert ms < 100                                         # milliseconds, not seconds
            await ws.send(json.dumps({"op": "ticket", "ref": 1, "symbol": sym, "entry": 100, "stop": 80,
                                      "target": 140}))
            tk = await recv_until(ws, "ticket")
            assert tk["ok"] and tk["lots"] == 3                     # 5,000 / (20 x 65) = 3.8 -> 3
            await ws.send(json.dumps({"op": "arm", "symbol": sym, "entry": 100, "stop": 80, "target": 140}))
            res = await recv_until(ws, "result", cond=lambda m: m["op"] == "arm")
            assert res["ok"]
            svc._on_quote(sym, {"ltp": 100.0, "bid": 99.5, "ask": 100.5, "rx": time.time()})
            st = await recv_until(ws, "state", cond=lambda m: any(t["state"] == "IN_TRADE" for t in m["trades"]))
            tid = st["trades"][0]["id"]
            await ws.send(json.dumps({"op": "modify", "id": tid, "stop": 40}))           # too much risk
            res = await recv_until(ws, "result", cond=lambda m: m["op"] == "modify")
            assert not res["ok"] and "never reduced" in res["msg"]
            await ws.send(json.dumps({"op": "modify", "id": tid, "stop": 85}))
            assert (await recv_until(ws, "result", cond=lambda m: m["op"] == "modify"))["ok"]
            await ws.send(json.dumps({"op": "kill", "confirm": "no"}))
            assert not (await recv_until(ws, "result", cond=lambda m: m["op"] == "kill"))["ok"]
            await ws.send(json.dumps({"op": "kill", "confirm": "KILL"}))
            res = await recv_until(ws, "result", cond=lambda m: m["op"] == "kill")
            assert res["ok"] and "KILL SWITCH ON" in res["msg"] and svc.kill_switch.active()
            await ws.send(json.dumps({"op": "arm", "symbol": sym, "entry": 100, "stop": 80, "target": 140}))
            res = await recv_until(ws, "result", cond=lambda m: m["op"] == "arm")
            assert not res["ok"]
    asyncio.run(go())
