"""KILL SWITCH (owner 30 Oct 2026): one red button on every screen. Pressing it

  1. stops the terminal: no NEW order of any kind for the next 12 hours (the BBP_NM engine, the chart risk desk,
     every buy) - saved in data/kill_switch.json, so a restart does not undo it;
  2. cancels EVERY pending order in the FYERS account (entries, stop-losses, targets);
  3. closes every open position with a LIMIT order at price - buffer (buy-backs at price + buffer), re-priced
     every 2 seconds until flat - never a market order (owner rule);
  4. shows the link to FYERS' own kill switch ("Take a Break"): FYERS has NO API for it (checked 30 Oct 2026,
     API changelog up to 16 Oct 2026), so that last click is yours, in the FYERS app or website.

Order matters: cancel everything FIRST, then one exit per position - so there are never two sell orders for the
same position (two fills would leave you SHORT an option).
Ending it early is possible only from PowerShell: ``.venv\\Scripts\\python.exe kill_switch.py --off`` (phrase).
"""

from __future__ import annotations

import json
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from terminal.candles import IST
from terminal.fyers import data_dir

KILL_FILE = "kill_switch.json"
KILL_HOURS = 12
RELEASE_PHRASE = "I WANT TO TRADE AGAIN"
FYERS_KILL_HELP = ("FYERS' own kill switch: FYERS app or trade.fyers.in -> Profile -> 'Take a Break' / Kill switch "
                   "(FYERS gives no API for it).")
PENDING = {4, 6}                                 # FYERS order status: 4 transit, 6 pending


class KillSwitch:
    def __init__(self, folder: Optional[Path] = None, clock: Callable[[], datetime] = lambda: datetime.now(IST)):
        self.path = Path(folder or data_dir()) / KILL_FILE
        self.clock = clock

    def until(self) -> Optional[datetime]:
        """End of the active kill switch, or None when trading is allowed."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            end = datetime.fromisoformat(data["until"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return end if end > self.clock() else None

    def active(self) -> bool:
        return self.until() is not None

    def activate(self, reason: str = "kill switch button", hours: float = KILL_HOURS) -> datetime:
        now = self.clock()
        end = max(now + timedelta(hours=hours), self.until() or now)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"since": now.isoformat(timespec="seconds"),
                                         "until": end.isoformat(timespec="seconds"), "reason": reason}, indent=2),
                             encoding="utf-8")
        return end

    def release(self, phrase: str) -> None:
        if phrase.strip() != RELEASE_PHRASE:
            raise ValueError(f"Not released. Type exactly: {RELEASE_PHRASE}")
        self.path.unlink(missing_ok=True)

    def message(self) -> str:
        end = self.until()
        return f"KILL SWITCH ON until {end:%d %b %H:%M} - no new orders." if end else ""


def _tick(price: float, up: bool) -> float:
    from live_trading.orders import round_to_tick
    return max(round_to_tick(price, 0.05, "BUY" if up else "SELL"), 0.05)


def flatten_account(model: Any, *, buffer: float = 2.0, log: Callable[[str], Any] = print,
                    sleep: Callable[[float], Any] = _time.sleep, tries: int = 15) -> dict:
    """Real FYERS account: cancel every pending order, then close every open position with limit orders."""
    report = {"cancelled": [], "exits": [], "left_open": [], "errors": []}
    book = model.orderbook() or {}
    for row in book.get("orderBook") or []:
        if int(row.get("status") or 0) in PENDING:
            r = model.cancel_order(data={"id": row["id"]})
            (report["cancelled"] if isinstance(r, dict) and r.get("s") == "ok" else report["errors"]).append(
                f"cancel {row.get('symbol')} {row['id']}: {r.get('message') if isinstance(r, dict) else r}")
    if report["cancelled"]:
        sleep(1.0)                                   # let the exchange confirm the cancels before any exit
    working: dict[str, dict] = {}
    for p in (model.positions() or {}).get("netPositions") or []:
        qty = int(p.get("netQty") or 0)
        if qty == 0:
            continue
        sym, side = p["symbol"], (-1 if qty > 0 else 1)
        ltp = float(p.get("ltp") or 0) or _quote(model, sym)
        price = _tick(ltp - buffer, False) if side == -1 else _tick(ltp + buffer, True)
        r = model.place_order(data={"symbol": sym, "qty": abs(qty), "type": 1, "side": side,
                                    "productType": p.get("productType") or "INTRADAY", "limitPrice": price,
                                    "stopPrice": 0, "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
                                    "orderTag": "KFTKILL"})
        if isinstance(r, dict) and r.get("s") == "ok" and r.get("id"):
            working[str(r["id"])] = {"symbol": sym, "side": side, "qty": abs(qty)}
            report["exits"].append(f"{'SELL' if side == -1 else 'BUY'} {abs(qty)} {sym} limit {price}")
        else:
            report["errors"].append(f"exit {sym}: {r.get('message') if isinstance(r, dict) else r}")
    for _ in range(tries):
        if not working:
            break
        sleep(2.0)
        for oid, w in list(working.items()):
            rows = (model.orderbook(data={"id": oid}) or {}).get("orderBook") or [{}]
            status = int(rows[0].get("status") or 0)
            if status == 2:
                working.pop(oid)
            elif status in (1, 5, 7):
                report["errors"].append(f"exit {w['symbol']} order {oid} ended with status {status}")
                working.pop(oid)
            else:
                ltp = _quote(model, w["symbol"])
                price = _tick(ltp - buffer, False) if w["side"] == -1 else _tick(ltp + buffer, True)
                model.modify_order(data={"id": oid, "type": 1, "limitPrice": price})
    report["left_open"] = [f"{w['symbol']} order {oid} still working" for oid, w in working.items()]
    for k, v in report.items():
        for line in v:
            log(f"KILL {k}: {line}")
    return report


def _quote(model: Any, symbol: str) -> float:
    r = model.quotes(data={"symbols": symbol}) or {}
    for row in r.get("d") or []:
        v = row.get("v") or {}
        if v.get("lp") is not None:
            return float(v["lp"])
    raise ValueError(f"no price for {symbol}")


__all__ = ["FYERS_KILL_HELP", "KILL_HOURS", "KillSwitch", "RELEASE_PHRASE", "flatten_account"]
