"""Chart risk desk (owner's answers 30 Oct 2026) on a pretend market: sizing, the one-exit-order OCO, drags,
gaps, timeouts, kill switch, daily loss, 15:10, restart, and the real FYERS order fields."""

import json
from datetime import datetime

import pytest

from terminal.candles import IST
from terminal.killswitch import KillSwitch
from terminal.risk.desk import Desk
from terminal.risk.gateways import FyersGateway, PaperGateway
from terminal.risk.sizing import position_size

SYM = "BSE:SENSEX26N0682000CE"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 30, 10, 0, tzinfo=IST)
        self.mono = 1000.0

    def __call__(self):
        return self.now


def make(tmp_path, **settings):
    c = Clock()
    kill = KillSwitch(tmp_path, clock=c)
    paper = PaperGateway(tmp_path / "j.jsonl", kill)
    d = Desk(paper=paper, folder=tmp_path, clock=c, mono=lambda: c.mono)
    d.store.update(paper_capital=50000, allocation_pct=50, max_risk=5000, **settings)
    d.set_instrument(SYM, 20, "BSE")
    return d, paper, c


def q(d, ltp, bid=None, ask=None):
    d.on_quote(SYM, {"ltp": ltp, "bid": bid if bid is not None else ltp - 0.5,
                     "ask": ask if ask is not None else ltp + 0.5})


def open_sells(paper):
    return [o for o in paper.orders.values() if o["side"] == "SELL" and o["status"] == "PENDING"]


def test_owner_sensex_example_plain_and_strict():
    plain = position_size(capital=50000, allocation_pct=50, max_risk=5000, entry=100, stop=75, lot_size=20)
    assert (plain.lots, plain.qty, plain.lots_capital, plain.lots_risk, plain.decided_by) == (10, 200, 12, 10, "risk")
    strict = position_size(capital=50000, allocation_pct=50, max_risk=5000, entry=100, stop=75, lot_size=20,
                           strict=True, exchange="BSE")
    assert (strict.lots, strict.qty) == (8, 160) and strict.risk_rs <= 5000


def test_entry_only_when_price_reaches_the_box_then_one_sl_l_at_the_exchange(tmp_path):
    d, paper, c = make(tmp_path)
    q(d, 95)
    ok, msg, t = d.arm(SYM, 100, 75, 150)
    assert ok and t.qty == 200 and t.state == "ARMED" and not paper.orders     # nothing sent yet
    q(d, 99.5)
    assert t.state == "ARMED"
    q(d, 100, ask=100.5)                                                       # reached
    entry = paper.orders[t.entry_order]
    assert entry["kind"] == "LIMIT" and entry["limit"] == 102.0                # CMP + 2
    assert t.state == "IN_TRADE" and t.entry_avg == 100.5
    sells = open_sells(paper)
    assert len(sells) == 1 and sells[0]["kind"] == "SL" and (sells[0]["stop"], sells[0]["limit"]) == (75, 73)


def test_drag_stop_modifies_the_order_and_a_riskier_drag_is_refused_without_cutting_qty(tmp_path):
    d, paper, c = make(tmp_path)
    q(d, 95)
    _, _, t = d.arm(SYM, 100, 75, 150)
    q(d, 100, ask=100.5)
    assert d.modify(t.id, stop=80)[0]
    sl = paper.orders[t.exit_order]
    assert (sl["stop"], sl["limit"]) == (80, 78)
    ok, msg = d.modify(t.id, stop=60)                                          # (100.5 - 60) x 200 = 8,100
    assert not ok and "never reduced" in msg and t.stop == 80 and t.qty == 200 and sl["stop"] == 80
    ok, msg = d.modify(t.id, stop=101)
    assert not ok and "Exit now" in msg                       # above the price (100)
    q(d, 120, bid=119.5)
    assert d.modify(t.id, stop=105)[0]                        # trailing above the entry is allowed


def test_target_turns_the_same_order_into_a_limit_sell(tmp_path):
    d, paper, c = make(tmp_path)
    q(d, 95)
    _, _, t = d.arm(SYM, 100, 75, 150)
    q(d, 100, ask=100.5)
    sl_id = t.exit_order
    q(d, 150, bid=149.5)
    assert t.exit_order == sl_id and t.state == "CLOSED" and t.exit_reason == "target"
    assert paper.orders[sl_id]["kind"] == "LIMIT" and t.exit_avg == 149.5
    assert t.pnl == pytest.approx((149.5 - 100.5) * 200)
    assert len([o for o in paper.orders.values() if o["side"] == "SELL"]) == 1     # never two sell orders


def test_stop_fill_and_a_gap_below_the_limit_is_chased_with_limits_only(tmp_path):
    d, paper, c = make(tmp_path)
    q(d, 95)
    _, _, t = d.arm(SYM, 100, 75, 150)
    q(d, 100, ask=100.5)
    q(d, 70, bid=69.5)                                       # jumped through 75 / 73
    assert t.state in ("STOP_CHASING", "CLOSED")
    if t.state != "CLOSED":
        c.mono += 3
        d.timer()
    assert t.state == "CLOSED" and t.exit_avg == 69.5 and "stop" in t.exit_reason
    kinds = [json.loads(x)["kind"] for x in (tmp_path / "j.jsonl").read_text().splitlines()
             if json.loads(x)["action"] == "PLACE"]
    assert set(kinds) <= {"LIMIT", "SL"}


def test_normal_stop_fill(tmp_path):
    d, paper, c = make(tmp_path)
    q(d, 95)
    _, _, t = d.arm(SYM, 100, 75, 150)
    q(d, 100, ask=100.5)
    q(d, 74.5, bid=74)
    assert t.state == "CLOSED" and t.exit_avg == 74 and t.pnl == pytest.approx((74 - 100.5) * 200)


def test_price_falling_to_the_entry_and_entry_timeout(tmp_path):
    d, paper, c = make(tmp_path)
    q(d, 110)
    _, _, t = d.arm(SYM, 100, 75, 150)
    assert t.trigger_dir == "down"
    q(d, 100, ask=110)                                       # reached but the ask is far: not filled
    assert t.state == "ENTRY_WORKING"
    q(d, 101.5, ask=110)
    c.mono += 11
    d.timer()
    assert paper.orders[t.entry_order]["limit"] == 103.0      # once, at most Rs 1 above the first limit (102)
    c.mono += 11
    d.timer()
    assert t.state == "CANCELLED" and not open_sells(paper)


def test_kill_switch_cancels_exits_and_blocks_new_orders_for_12_hours(tmp_path):
    d, paper, c = make(tmp_path)
    q(d, 95)
    _, _, a = d.arm(SYM, 100, 75, 150)
    _, _, b = d.arm(SYM, 98, 80, 130)
    q(d, 100, ask=100.5)                                     # both fill
    _, _, armed = d.arm(SYM, 120, 110, 140)
    d.kill.activate()
    d.kill_all()
    q(d, 100, bid=99.5)
    assert armed.state == "CANCELLED" and a.state == "CLOSED" and b.state == "CLOSED"
    ok, msg, _ = d.arm(SYM, 120, 110, 140)
    assert not ok and "KILL SWITCH" in msg


def test_max_loss_per_day_box_empty_means_no_limit(tmp_path):
    d, paper, c = make(tmp_path, max_day_loss=4000)
    q(d, 95)
    _, _, t = d.arm(SYM, 100, 75, 150)
    q(d, 100, ask=100.5)
    q(d, 74.5, bid=74)                                       # loses about Rs 5,300
    ok, msg, _ = d.arm(SYM, 100, 90, 150)
    assert not ok and "Max loss for the day" in msg
    d.store.update(max_day_loss=None)
    assert d.arm(SYM, 100, 90, 150)[0]


def test_1510_closes_everything_and_restart_keeps_the_trades(tmp_path):
    d, paper, c = make(tmp_path)
    q(d, 95)
    _, _, t = d.arm(SYM, 100, 75, 150)
    q(d, 100, ask=100.5)
    again = Desk(paper=PaperGateway(tmp_path / "j2.jsonl", KillSwitch(tmp_path, clock=c)), folder=tmp_path, clock=c)
    assert again.trades[t.id].state == "IN_TRADE"
    c.now = c.now.replace(hour=15, minute=10)
    d.timer()
    q(d, 120, bid=119.5)
    assert t.state == "CLOSED" and t.exit_reason == "time exit 15:10"


class Model:
    def __init__(self):
        self.calls = []

    def place_order(self, data):
        self.calls.append(("place", dict(data)))
        return {"s": "ok", "id": f"O{len(self.calls)}"}

    def modify_order(self, data):
        self.calls.append(("modify", dict(data)))
        return {"s": "ok"}

    def cancel_order(self, data):
        self.calls.append(("cancel", dict(data)))
        return {"s": "ok"}

    def orderbook(self, data=None):
        return {"s": "ok", "orderBook": []}


def test_real_gateway_sends_limit_and_sl_l_with_the_right_fields(tmp_path):
    m = Model()
    g = FyersGateway(m, tmp_path / "r.jsonl", KillSwitch(tmp_path), pace=0)
    events = []
    g.listener = events.append
    oid = g.place(symbol=SYM, side="SELL", qty=200, kind="SL", limit=73, stop=75, tag="KFTX-T1")
    data = m.calls[0][1]
    assert (data["type"], data["side"], data["stopPrice"], data["limitPrice"], data["productType"]) == \
        (4, -1, 75.0, 73.0, "INTRADAY") and data["orderTag"] == "KFTXT1"
    g.modify(oid, kind="LIMIT", limit=148)
    assert m.calls[1][1] == {"id": oid, "type": 1, "limitPrice": 148.0}
    g.handle({"id": oid, "status": 2, "filledQty": 200, "tradedPrice": 149.0})
    g.handle({"id": oid, "status": 2, "filledQty": 200, "tradedPrice": 149.0})      # duplicate ignored
    assert len(events) == 1 and events[0].status == "FILLED" and events[0].avg_price == 149.0
    KillSwitch(tmp_path).activate()
    with pytest.raises(Exception):
        g.place(symbol=SYM, side="BUY", qty=20, kind="LIMIT", limit=102)
    assert all(c[1].get("type") in (1, 4, None) for c in m.calls)                    # never 2 (market)
