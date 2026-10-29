"""Backtest: same rules as live on a pretend market (option prices follow the index), statistics, data layer."""

import math
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from terminal.backtest import BacktestSettings, charges_for, run
from terminal.candles import IST, Candle
from terminal.fyers import Contracts
from terminal.history import History, PriceDB, strike_from_symbol
from terminal.metrics import equity_curve, group_stats, monthly, summary

DAYS = [date(2026, 10, 5) + timedelta(days=i) for i in range(12) if (date(2026, 10, 5) + timedelta(days=i)).weekday() < 5]
STRIKES = range(24000, 26050, 50)


def spot_path(day_no, i):
    return 25000 + 60 * math.sin((day_no * 375 + i) / 17.0) + 15 * math.sin((day_no * 375 + i) / 5.0)


class FakeData:
    def __init__(self):
        self.spot = {}
        for n, d in enumerate(DAYS):
            t0 = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
            self.spot[d] = [Candle(t0 + 60 * i, spot_path(n, i), spot_path(n, i) + 3, spot_path(n, i) - 3,
                                   spot_path(n, i + 0.5)) for i in range(375)]

    def index_days(self, index, start, end):
        return {d: v for d, v in self.spot.items() if start <= d <= end}

    def nearest_expiry(self, index, day, start):
        return day + timedelta(days=(1 - day.weekday()) % 7)        # Tuesdays

    def lot_size(self, index):
        return 65

    def contracts(self, index, expiry, lot):
        return Contracts(expiry, lot, 50.0, {k: f"NSE:NIFTY{k}CE" for k in STRIKES},
                         {k: f"NSE:NIFTY{k}PE" for k in STRIKES}, None)

    def option_minutes(self, index, symbol, expiry, day, lot):
        k, ce = float(symbol[9:14]), symbol.endswith("CE")
        out = []
        for c in self.spot[day]:
            price = lambda s: max(1.0, 120 + ((s - k) if ce else (k - s)) * 0.5)
            o, cl = price(c.open), price(c.close)
            out.append(Candle(c.ts, o, max(o, cl) + 1, max(0.5, min(o, cl) - 1), cl))
        return out


def cfg(**kw):
    base = dict(index="NIFTY", start=DAYS[2], end=DAYS[-1], timeframe=3, strike_mode="RANGE", premium_low=90,
                premium_high=140, funds_per_entry=20000, capital=100000)
    base.update(kw)
    return BacktestSettings(**base)


def test_backtest_follows_the_live_rules():
    r = run(FakeData(), cfg(), log=lambda m: None)
    assert r.trades, r.signals[:5]
    for t in r.trades:
        assert t["entry_ts"] > t["signal_ts"]                          # after the signal candle closed
        assert "09:30" <= t["entry_time"] <= "15:02" and t["exit_time"] <= "15:10"
        assert 90 <= t["entry_price"] - 0.5 <= 142 + 1                # bought inside the range (+ slippage)
        assert t["lots"] == math.floor(20000 / ((t["entry_price"] - 0.5 + 0.0001) * 65)) or t["lots"] >= 1
        assert t["stop"] < t["entry_price"]
        if t["exit_reason"].startswith("stop"):
            assert t["exit_price"] <= t["stop"]
        assert t["net_pnl"] == pytest.approx(t["gross_pnl"] - t["charges"])
    # never two trades open at once
    by_day = {}
    for t in r.trades:
        by_day.setdefault(t["date"], []).append(t)
    for ts in by_day.values():
        for a, b in zip(ts, ts[1:]):
            assert a["exit_ts"] <= b["entry_ts"]
    assert {t["side"] for t in r.trades} == {"CALL", "PUT"}


def test_call_box_only_never_buys_puts_and_stats_add_up():
    r = run(FakeData(), cfg(put_on=False), log=lambda m: None)
    assert r.trades and {t["side"] for t in r.trades} == {"CALL"}
    s = summary(r.trades, r.days, 100000)
    assert s["Trades"] == len(r.trades) == s["Winning trades"] + s["Losing trades"]
    assert s["Net P&L (Rs)"] == pytest.approx(sum(t["net_pnl"] for t in r.trades))
    assert s["Net P&L (Rs)"] == pytest.approx(sum(d["net"] for d in r.days), abs=0.05)
    eq = equity_curve(r.trades, 100000)
    assert eq[-1]["equity"] == pytest.approx(100000 + s["Net P&L (Rs)"])
    assert -min(e["drawdown"] for e in eq) == pytest.approx(s["Max drawdown (Rs)"])
    assert sum(g["trades"] for g in group_stats(r.trades, lambda t: t["exit_reason"])) == len(r.trades)
    assert sum(m["trades"] for m in monthly(r.days)) == len(r.trades)


def test_per_lot_mode_and_not_enough_money():
    r = run(FakeData(), cfg(strike_mode="PER_LOT", per_lot_amount=6500), log=lambda m: None)
    assert r.trades and all(t["entry_price"] - 0.5 <= 100 + 0.01 for t in r.trades)
    poor = run(FakeData(), cfg(funds_per_entry=1000), log=lambda m: None)
    assert not poor.trades and any("does not buy one lot" in s["result"] for s in poor.signals)


def test_charges_match_the_published_rates():
    # buy 65 x 100, sell 65 x 110 on NSE
    c = charges_for(6500, 7150, "NSE")
    expected = 40 + 7.15 + 0.0003503 * 13650 + 13650 / 1e6 + 0.195 + 0.18 * (40 + 0.0003503 * 13650 + 13650 / 1e6)
    assert c == pytest.approx(expected, abs=0.01)


# ------------------------------------------------------------------ data layer
class Model:
    def __init__(self):
        self.calls = []

    def history(self, data):
        self.calls.append(("history", data["symbol"]))
        d, rows = date.fromisoformat(data["range_from"]), []
        while d <= date.fromisoformat(data["range_to"]):
            t0 = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
            rows += [[t0 + 60 * i, 100, 101, 99, 100.5, 0] for i in range(375)]
            d += timedelta(days=1)
        return {"s": "ok", "candles": rows}

    def fno_historical_data(self, data):
        self.calls.append(("fno", data["symbol"]))
        d = date.fromisoformat(data["range_from"])
        t0 = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
        return {"s": "ok", "candles": [[t0 + 60 * i, 50, 51, 49, 50.5, 10, 1000] for i in range(375)]}

    def expiry_dates(self, data):
        return {"s": "ok", "data": {"expiries": [{"expiry_date": "2026-10-06"}, {"expiry_date": "2026-10-13"}]}}

    def history_underlying_symbols(self, data):
        return {"s": "ok", "data": {"contracts": {"options": ["NSE:NIFTY2610625000CE", "NSE:NIFTY2610625000PE",
                                                             "NSE:NIFTY26OCT25050CE"]}}}


def test_history_reads_the_database_first_and_adds_only_what_was_missing(tmp_path):
    path = tmp_path / "kq.db"
    db = PriceDB(path, log=lambda m: None)
    h = History(Model(), db, today=date(2026, 10, 20), log=lambda m: None, pause=0,
                master_loader=lambda n, d: {})
    got = h.index_days("NIFTY", date(2026, 10, 7), date(2026, 10, 8))
    assert [len(v) for v in got.values()] == [375, 375] and len(h.client.calls) == 1
    again = History(Model(), PriceDB(path, log=lambda m: None), today=date(2026, 10, 20), log=lambda m: None,
                    pause=0, master_loader=lambda n, d: {})
    again.index_days("NIFTY", date(2026, 10, 7), date(2026, 10, 8))
    assert again.client.calls == []                                       # now in the database
    assert h.expiries("NIFTY", date(2026, 10, 1)) == [date(2026, 10, 6), date(2026, 10, 13)]
    ch = h.chain("NIFTY", date(2026, 10, 6))
    assert ch[(25000.0, "CE")] == "NSE:NIFTY2610625000CE"
    m = h.option_minutes("NIFTY", "NSE:NIFTY2610625000CE", date(2026, 10, 6), date(2026, 10, 6), 65)
    assert len(m) == 375 and h.client.calls[-1] == ("fno", "NSE:NIFTY2610625000CE")   # expired -> fno history
    with sqlite3.connect(path) as con:
        row = con.execute("SELECT instrument_type, strike, option_type, expiry_date FROM instruments WHERE "
                          "symbol = 'NSE:NIFTY2610625000CE'").fetchone()
        src = {r[0] for r in con.execute("SELECT DISTINCT source FROM candles")}
    assert row == ("OPTION", 25000.0, "CE", "2026-10-06") and src == {"FYERS_HISTORY", "FYERS_FNO_HISTORY"}


def test_backup_before_the_first_write_to_an_existing_database(tmp_path):
    path = tmp_path / "kq.db"
    PriceDB(path, log=lambda m: None).add("NSE:X-INDEX", [Candle(1, 1, 1, 1, 1)], source="T")
    PriceDB(path, log=lambda m: None).add("NSE:X-INDEX", [Candle(2, 1, 1, 1, 1)], source="T")
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_strike_from_fyers_symbols():
    assert strike_from_symbol("NSE:NIFTY2692223250CE") == 23250
    assert strike_from_symbol("NSE:NIFTY26SEP23250PE") == 23250
    assert strike_from_symbol("BSE:SENSEX26O2982000CE") == 82000
    assert strike_from_symbol("NSE:BANKNIFTY26NOV56000CE") == 56000
