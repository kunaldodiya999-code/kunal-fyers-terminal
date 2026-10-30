"""The screens open without errors: FYERS login page, then the terminal (pretend FYERS, no network)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import terminal.engine as engine
import terminal.fyers as fyers
from tests.fakes import FakeFyers, master

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture(autouse=True)
def _no_risk_desk_server(monkeypatch):
    import terminal.risk.service as service
    monkeypatch.setattr(service.RiskService, "start", lambda self: None)     # no local server in screen tests


def test_login_page_asks_for_the_app_details_first():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("Log in with FYERS" in s.value for s in at.subheader)
    assert [t.label for t in at.text_input][:3] == ["App ID", "Secret ID", "Redirect URL"]


def test_terminal_page_with_a_token(monkeypatch, tmp_path):
    monkeypatch.setenv("FYERS_CLIENT_ID", "ABCD-100")
    monkeypatch.setenv("FYERS_SECRET_KEY", "s")
    fyers.save_token("tok")
    fake = FakeFyers()
    fake.set_price("NSE:NIFTY50-INDEX", 25010)
    monkeypatch.setattr(fyers, "Fyers", lambda cid, tok: fake)
    monkeypatch.setattr(engine, "load_master", lambda name, today, folder=None: master())
    monkeypatch.setattr(engine, "CHART_FILE", tmp_path / "chart.json")
    monkeypatch.setattr(engine.Terminal, "start", lambda self: self.tick())     # one pass, no thread
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, at.exception
    assert [c.label for c in at.checkbox] == ["Call", "Put"]
    assert any(m.label == "Funds available" and "50,000" in m.value for m in at.metric)
    assert any("25000" in m.value and "CALL" in m.value for m in at.markdown)  # the option chain table
    at.checkbox[1].uncheck().run()
    assert not at.exception
    from terminal.settings import SettingsStore
    assert SettingsStore().load().put_on is False
    at.radio(key="w_mode").set_value("PER_LOT").run()
    assert any(n.label == "Per lot amount (Rs)" for n in at.number_input)


def test_open_trade_and_todays_trades_show(monkeypatch, tmp_path):
    import json
    from datetime import datetime
    from terminal.candles import IST
    from terminal.engine import TRADE_FIELDS
    monkeypatch.setenv("FYERS_CLIENT_ID", "ABCD-100")
    monkeypatch.setenv("FYERS_SECRET_KEY", "s")
    fyers.save_token("tok2")
    folder = fyers.data_dir()
    today = datetime.now(IST).date().isoformat()
    pos = {"day": today, "index": "NIFTY", "side": "CALL", "symbol": "NSE:NIFTY26N0425000CE", "strike": 25000,
           "lot_size": 65, "lots": 2, "qty": 130, "entry_time": "10:03:05", "entry_price": 101.5, "stop": 96.0,
           "mode": "PAPER", "signal_bar": 0}
    (folder / "state.json").write_text(json.dumps({"position": pos}))
    (folder / "trades.csv").write_text(",".join(TRADE_FIELDS) + "\n" + ",".join(
        [today, "NIFTY", "PUT", "NSE:NIFTY26N0425000PE", "25000", "1", "65", "09:40:03", "120", "110", "09:52:03",
         "131", "715", "Direction crossed above 0", "PAPER"]) + "\n")
    fake = FakeFyers()
    fake.set_price("NSE:NIFTY50-INDEX", 25010)
    fake.set_price("NSE:NIFTY26N0425000CE", 104.0)
    monkeypatch.setattr(fyers, "Fyers", lambda cid, tok: fake)
    monkeypatch.setattr(engine, "load_master", lambda name, today, folder=None: master())
    monkeypatch.setattr(engine, "CHART_FILE", tmp_path / "chart.json")
    eleven = datetime.now(IST).replace(hour=11, minute=0, second=0)          # market hours: no 15:10 exit

    def one_pass(self):
        self.clock = lambda: eleven
        self.tick()
    monkeypatch.setattr(engine.Terminal, "start", one_pass)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("NSE:NIFTY26N0425000CE" in m.value and "P&L" in m.value for m in at.markdown)
    assert any(m.label == "Funds utilized" and m.value.startswith("13.2") for m in at.metric)   # 13,195 / 1,00,000 paper
    assert any(m.label == "Today's P&L" and "715" in m.value for m in at.metric)



def test_backtest_screen_runs_and_shows_the_report(monkeypatch, tmp_path):
    import terminal.backtest_view as bv
    from tests.test_backtest import DAYS, FakeData
    monkeypatch.setenv("FYERS_CLIENT_ID", "ABCD-100")
    monkeypatch.setenv("FYERS_SECRET_KEY", "s")
    fyers.save_token("tok3")
    fake = FakeFyers()
    fake.set_price("NSE:NIFTY50-INDEX", 25010)
    monkeypatch.setattr(fyers, "Fyers", lambda cid, tok: fake)
    monkeypatch.setattr(engine, "load_master", lambda name, today, folder=None: master())
    monkeypatch.setattr(engine, "CHART_FILE", tmp_path / "chart.json")
    monkeypatch.setattr(engine.Terminal, "start", lambda self: None)

    class Hist(FakeData):
        fetched = 0

        def __init__(self, client, db, log=print, **kw):
            super().__init__()
    monkeypatch.setattr(bv, "History", Hist)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="w_view").set_value("Backtest").run()
    assert not at.exception, at.exception
    at.multiselect[0].set_value(["NIFTY"])
    at.date_input[0].set_value(DAYS[2])
    at.date_input[1].set_value(DAYS[-1])
    at.number_input[0].set_value(90.0)
    at.number_input[1].set_value(140.0)
    next(b for b in at.button if b.label == "Run backtest").click().run()
    assert not at.exception, at.exception
    labels = [m.label for m in at.metric]
    assert {"Net P&L", "Trades", "Win rate", "Profit factor", "Max drawdown", "Sharpe"} <= set(labels)
    trades = next(m.value for m in at.metric if m.label == "Trades")
    assert int(trades) > 0
    assert any("KUNAL FYERS TERMINAL BACKTEST" in c.value for c in at.code)
    assert list((fyers.data_dir() / "backtests").glob("*_NIFTY/trades.csv"))



def test_kill_switch_on_every_page_blocks_orders(monkeypatch, tmp_path):
    from terminal.killswitch import KillSwitch
    monkeypatch.setenv("FYERS_CLIENT_ID", "ABCD-100")
    monkeypatch.setenv("FYERS_SECRET_KEY", "s")
    fyers.save_token("tok4")
    fake = FakeFyers()
    fake.set_price("NSE:NIFTY50-INDEX", 25010)
    monkeypatch.setattr(fyers, "Fyers", lambda cid, tok: fake)
    monkeypatch.setattr(engine, "load_master", lambda name, today, folder=None: master())
    monkeypatch.setattr(engine, "CHART_FILE", tmp_path / "chart.json")
    monkeypatch.setattr(engine.Terminal, "start", lambda self: None)
    at = AppTest.from_file(APP, default_timeout=60).run()
    for view in ("Live terminal", "Manual trade (R:R box)", "Backtest"):
        at.radio(key="w_view").set_value(view).run()
        assert not at.exception, at.exception
        assert any(b.label == "KILL SWITCH" for b in at.button), view
    at.button(key="kill_open").click().run()
    at.text_input(key="kill_text").set_value("KILL").run()
    at.button(key="kill_go").click().run()
    assert not at.exception, at.exception
    assert KillSwitch().active()
    assert any("KILL SWITCH ON" in e.value for e in at.error)
