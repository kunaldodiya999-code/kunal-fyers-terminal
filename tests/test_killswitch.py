"""Kill switch: 12 hours with no new orders, survives a restart, cancels first then ONE limit exit per position."""

from datetime import datetime, timedelta

import pytest

from terminal.candles import IST
from terminal.killswitch import RELEASE_PHRASE, KillSwitch, flatten_account


def test_twelve_hours_saved_and_only_released_with_the_phrase(tmp_path):
    now = datetime(2026, 10, 30, 11, 0, tzinfo=IST)
    k = KillSwitch(tmp_path, clock=lambda: now)
    assert not k.active()
    end = k.activate()
    assert end == now + timedelta(hours=12) and KillSwitch(tmp_path, clock=lambda: now).active()   # restart
    assert not KillSwitch(tmp_path, clock=lambda: now + timedelta(hours=12, seconds=1)).active()
    with pytest.raises(ValueError):
        k.release("yes")
    k.release(RELEASE_PHRASE)
    assert not k.active()


class Model:
    def __init__(self):
        self.calls = []
        self.status = {}

    def orderbook(self, data=None):
        if data:
            return {"s": "ok", "orderBook": [{"id": data["id"], "status": self.status.get(data["id"], 6)}]}
        return {"s": "ok", "orderBook": [{"id": "A", "status": 6, "symbol": "NSE:X1CE"},
                                         {"id": "B", "status": 2, "symbol": "NSE:X2CE"}]}

    def cancel_order(self, data):
        self.calls.append(("cancel", data["id"]))
        return {"s": "ok"}

    def positions(self):
        return {"s": "ok", "netPositions": [{"symbol": "NSE:X1CE", "netQty": 130, "ltp": 101.0,
                                             "productType": "INTRADAY"},
                                            {"symbol": "NSE:X3PE", "netQty": 0, "ltp": 50}]}

    def place_order(self, data):
        self.calls.append(("place", data["symbol"], data["side"], data["qty"], data["type"], data["limitPrice"]))
        return {"s": "ok", "id": "E1"}

    def modify_order(self, data):
        self.calls.append(("modify", data["id"], data["limitPrice"]))
        self.status[data["id"]] = 2
        return {"s": "ok"}

    def quotes(self, data):
        return {"s": "ok", "d": [{"n": data["symbols"], "v": {"lp": 99.0}}]}


def test_flatten_cancels_first_then_one_limit_exit_and_chases_without_market_orders():
    m = Model()
    r = flatten_account(m, buffer=2, log=lambda x: None, sleep=lambda s: None)
    assert m.calls[0] == ("cancel", "A")                                   # cancel BEFORE any exit
    assert m.calls[1] == ("place", "NSE:X1CE", -1, 130, 1, 99.0)            # LIMIT sell at ltp - 2
    assert ("modify", "E1", 97.0) in m.calls                                # re-priced, still a limit
    assert all(c[0] != "place" or c[4] == 1 for c in m.calls)               # type 1 = limit, never 2
    assert not r["left_open"] and not r["errors"]
