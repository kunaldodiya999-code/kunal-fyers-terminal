"""The Backtest screen: settings, run, and a full report (like professional backtest reports)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from terminal.backtest import BacktestSettings, run
from terminal.candles import IST, resample
from terminal.chart import replay_html
from terminal.fyers import INDEXES, data_dir
from terminal.history import History, PriceDB, day_bounds, default_db
from terminal.metrics import equity_curve, group_stats, monthly, summary
from terminal.settings import SettingsStore

GREEN, RED, BLUE = "#26a69a", "#ef5350", "#2962ff"
SHIFT = 19800


def last_trading_day(today: date) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def back_n_weekdays(end: date, n: int) -> date:
    d, left = end, n - 1
    while left > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def save_run(index: str, result, stats: dict) -> Path:
    folder = data_dir() / "backtests" / f"{datetime.now(IST):%Y%m%d_%H%M%S}_{index}"
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(result.trades).to_csv(folder / "trades.csv", index=False)
    pd.DataFrame(result.signals).to_csv(folder / "signals.csv", index=False)
    pd.DataFrame(result.days).to_csv(folder / "days.csv", index=False)
    (folder / "summary.json").write_text(json.dumps({"settings": result.settings, "summary": stats,
                                                     "notes": result.notes}, indent=2, default=str),
                                         encoding="utf-8")
    return folder


def text_summary(index: str, result, stats: dict) -> str:
    s = result.settings
    head = (f"KUNAL FYERS TERMINAL BACKTEST - {INDEXES[index].label} {s['start']} to {s['end']}, {s['timeframe']} min, "
            f"Call {'ON' if s['call_on'] else 'OFF'} / Put {'ON' if s['put_on'] else 'OFF'}, mode {s['strike_mode']} "
            f"({s['premium_low']:g}-{s['premium_high']:g} | per lot {s['per_lot_amount']:g}), funds/entry "
            f"{s['funds_per_entry']:g}, capital {s['capital']:g}, slippage {s['slippage']:g}, charges "
            f"{'ON' if s['charges'] else 'OFF'}")
    return head + "\n" + "\n".join(f"{k}: {v}" for k, v in stats.items())


def render(client) -> None:
    live = SettingsStore().load()
    st.subheader("Backtest - BBP_NM strategy on real past option prices")
    st.caption("Same rules as the live terminal: a Direction cross on a FINISHED candle, strike from the real "
               "option chain at that moment, stop = the option's signal-candle low, exit on the opposite cross or "
               "15:10. Data: the shared KunalQuant database first; missing days / options are fetched from FYERS "
               "and added to it (add-only, one backup before the first write).")
    end_default = last_trading_day(datetime.now(IST).date())
    with st.form("bt"):
        a, b, c, d = st.columns(4)
        indexes = a.multiselect("Index", list(INDEXES), default=["NIFTY", "SENSEX"],
                                format_func=lambda k: INDEXES[k].label)
        timeframe = b.selectbox("Timeframe (min)", (1, 3, 5), index=(1, 3, 5).index(live.timeframe))
        start = c.date_input("From", back_n_weekdays(end_default, 30))
        end = d.date_input("To", end_default)
        a, b, c, d = st.columns(4)
        call_on = a.checkbox("Call", value=live.call_on)
        put_on = a.checkbox("Put", value=live.put_on)
        mode = b.radio("Premium select", ("RANGE", "PER_LOT"), index=("RANGE", "PER_LOT").index(live.strike_mode),
                       format_func={"RANGE": "Price range", "PER_LOT": "Per lot amount"}.get)
        lo = c.number_input("Price range from", min_value=0.0, value=float(live.premium_low or 0.0), step=5.0)
        hi = c.number_input("Price range to", min_value=0.0, value=float(live.premium_high or 0.0), step=5.0)
        per_lot = d.number_input("Per lot amount (Rs)", min_value=0.0, value=float(live.per_lot_amount or 0.0),
                                 step=500.0)
        a, b, c, d = st.columns(4)
        funds = a.number_input("Funds per new entry (Rs)", min_value=0.0, step=1000.0,
                               value=float(live.funds_per_entry or 20000.0))
        capital = b.number_input("Starting capital (Rs)", min_value=1000.0, step=10000.0,
                                 value=float(live.paper_capital or 100000.0))
        slippage = c.number_input("Slippage per order (Rs)", min_value=0.0, max_value=10.0, step=0.25, value=0.5,
                                  help="Price lost on each fill (spread / delay). 0.5 is a fair NIFTY/SENSEX figure.")
        charges = d.checkbox("Include charges (brokerage, STT, exchange, GST, stamp)", value=True)
        compounding = d.checkbox("Compounding (profits change the money available)", value=False)
        with st.expander("More settings"):
            e1, e2, e3, e4 = st.columns(4)
            entry_start = e1.text_input("New entries from", live.entry_start)
            entry_end = e2.text_input("No new entries from", live.entry_end)
            force_exit = e3.text_input("Close everything at", live.force_exit)
            buffer = e4.number_input("Limit buffer (Rs)", min_value=0.0, max_value=10.0, value=float(live.buffer))
            db_path = st.text_input("Price database", str(default_db()))
        go = st.form_submit_button("Run backtest", type="primary")

    if go:
        if not indexes:
            st.error("Choose at least one index.")
            return
        results = {}
        for index in indexes:
            try:
                cfg = BacktestSettings(index=index, start=start, end=end, timeframe=int(timeframe), call_on=call_on,
                                       put_on=put_on, strike_mode=mode, premium_low=lo, premium_high=hi,
                                       per_lot_amount=per_lot, funds_per_entry=funds, capital=capital,
                                       compounding=compounding, buffer=buffer, slippage=slippage, charges=charges,
                                       entry_start=entry_start, entry_end=entry_end, force_exit=force_exit,
                                       bbp_window=live.bbp_window)
            except ValueError as exc:
                st.error(str(exc))
                return
            bar = st.progress(0.0, text=f"{INDEXES[index].label}: starting")
            messages: list[str] = []
            hist = History(client, PriceDB(Path(db_path), log=messages.append), log=messages.append)
            try:
                res = run(hist, cfg, progress=lambda f, m: bar.progress(min(1.0, f), text=m), log=messages.append)
            except Exception as exc:
                st.error(f"{INDEXES[index].label}: backtest stopped: {exc}")
                with st.expander("Data log"):
                    st.code("\n".join(messages) or "-")
                continue
            res.notes.append(f"FYERS calls this run: {hist.fetched} (everything else came from the database).")
            res.notes.extend(messages[-40:])
            stats = summary(res.trades, res.days, capital)
            folder = save_run(index, res, stats)
            results[index] = {"result": res, "stats": stats, "folder": str(folder), "db": db_path}
            bar.empty()
        st.session_state["bt_results"] = results

    results = st.session_state.get("bt_results")
    if not results:
        st.info("Choose the settings and press **Run backtest**. The first run fetches missing option candles "
                "from FYERS (a few minutes for 30 days); later runs read them from the database.")
        return
    if len(results) > 1:
        st.markdown("#### Side by side")
        keys = ["Net P&L (Rs)", "Trades", "Win rate %", "Profit factor", "Expectancy per trade (Rs)",
                "Max drawdown (Rs)", "Sharpe ratio (daily, yearly scale)", "Return on capital %"]
        st.dataframe(pd.DataFrame({INDEXES[k].label: {x: v["stats"][x] for x in keys} for k, v in results.items()}),
                     width="stretch")
    tabs = st.tabs([INDEXES[k].label for k in results])
    for tab, (index, pack) in zip(tabs, results.items()):
        with tab:
            report(index, pack)


def _kpis(stats: dict) -> None:
    cols = st.columns(7)
    tiles = [("Net P&L", f"Rs {stats['Net P&L (Rs)']:,.0f}"), ("Trades", stats["Trades"]),
             ("Win rate", f"{stats['Win rate %'] or 0:.1f} %"), ("Profit factor", stats["Profit factor"] or "-"),
             ("Expectancy / trade", f"Rs {stats['Expectancy per trade (Rs)'] or 0:,.0f}"),
             ("Max drawdown", f"Rs {stats['Max drawdown (Rs)']:,.0f}"),
             ("Sharpe", stats["Sharpe ratio (daily, yearly scale)"] or "-")]
    for c, (label, value) in zip(cols, tiles):
        c.metric(label, value)


def report(index: str, pack: dict) -> None:
    res, stats = pack["result"], pack["stats"]
    s = res.settings
    st.caption(f"{s['start']} to {s['end']} · {s['timeframe']}-minute · lot {res.lot_size} · results saved in "
               f"`{pack['folder']}`")
    if not res.trades:
        st.warning("No trades in this period with these settings. See 'Signals' below for the reason of each cross.")
    _kpis(stats)
    trades = pd.DataFrame(res.trades)
    days = pd.DataFrame(res.days)

    t1, t2, t3, t4, t5, t6, t7 = st.tabs(["Equity & drawdown", "Daily P&L", "Statistics", "Breakdowns",
                                          "Trades", "Trade replay", "Signals & notes"])
    with t1:
        if res.trades:
            eq = pd.DataFrame(equity_curve(res.trades, s["capital"]))
            line = alt.Chart(eq).mark_line(color=BLUE, strokeWidth=2).encode(
                x=alt.X("trade:Q", title="Trade number"), y=alt.Y("equity:Q", title="Equity (Rs)",
                                                                   scale=alt.Scale(zero=False)),
                tooltip=["trade", "date", "equity"])
            st.altair_chart(line.properties(height=300, title="Equity curve (after charges)"), width="stretch")
            dd = alt.Chart(eq).mark_area(color=RED, opacity=0.5).encode(
                x=alt.X("trade:Q", title="Trade number"), y=alt.Y("drawdown:Q", title="Drawdown (Rs)"),
                tooltip=["trade", "date", "drawdown"])
            st.altair_chart(dd.properties(height=180, title="Drawdown from the highest equity"), width="stretch")
    with t2:
        if not days.empty:
            days["date"] = days["date"].astype(str)
            bars = alt.Chart(days).mark_bar().encode(
                x=alt.X("date:N", title=None, sort=None), y=alt.Y("net:Q", title="Net P&L (Rs)"),
                color=alt.condition("datum.net >= 0", alt.value(GREEN), alt.value(RED)),
                tooltip=["date", "net", "trades"])
            st.altair_chart(bars.properties(height=300, title="P&L per day"), width="stretch")
            st.dataframe(pd.DataFrame(monthly(res.days)), hide_index=True, width="stretch")
        if res.trades:
            hist = alt.Chart(trades).mark_bar(color=BLUE).encode(
                x=alt.X("net_pnl:Q", bin=alt.Bin(maxbins=30), title="Net P&L per trade (Rs)"),
                y=alt.Y("count():Q", title="Trades"))
            st.altair_chart(hist.properties(height=220, title="How trade results are spread"), width="stretch")
    with t3:
        st.dataframe(pd.DataFrame({"Value": {k: ("-" if v is None else v) for k, v in stats.items()}}),
                     width="stretch", height=900)
    with t4:
        if res.trades:
            trades["hour"] = trades["entry_time"].str[:2] + ":00"
            trades["weekday"] = pd.to_datetime(trades["date"]).dt.day_name()
            trades["expiry day"] = trades.apply(lambda t: "expiry day" if t["date"] == t["expiry"] else "other days",
                                                axis=1)
            for title, key in (("By side", "side"), ("By exit reason", "exit_reason"), ("By entry hour", "hour"),
                               ("By weekday", "weekday"), ("Expiry day or not", "expiry day")):
                st.markdown(f"**{title}**")
                st.dataframe(pd.DataFrame(group_stats(trades.to_dict("records"), lambda t, k=key: t[k])),
                             hide_index=True, width="stretch")
    with t5:
        if res.trades:
            show = trades.drop(columns=[c for c in ("entry_ts", "exit_ts", "signal_ts", "hour", "weekday",
                                                    "expiry day") if c in trades.columns])
            st.dataframe(show, hide_index=True, width="stretch", height=500)
            st.download_button("Download trades (CSV)", show.to_csv(index=False), f"backtest_{index}_trades.csv",
                               "text/csv", key=f"dl_{index}")
    with t6:
        if res.trades:
            labels = [f"{i + 1}. {t['date']} {t['entry_time']} {t['side']} {t['symbol']} -> {t['exit_reason']} "
                      f"(Rs {t['net_pnl']:,.0f})" for i, t in enumerate(res.trades)]
            pick = st.selectbox("Trade", range(len(labels)), format_func=labels.__getitem__, key=f"rp_{index}")
            replay(index, res, res.trades[pick], pack)
    with t7:
        st.markdown("**Every Direction cross and what happened**")
        st.dataframe(pd.DataFrame(res.signals), hide_index=True, width="stretch", height=400)
        st.markdown("**Notes and data log**")
        st.code("\n".join(res.notes) or "-", language=None)
        st.markdown("**Summary to paste to Claude**")
        st.code(text_summary(index, res, stats), language=None)


def show_html(html: str, height: int) -> None:
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:                                    # older Streamlit
        import streamlit.components.v1 as components
        components.html(html, height=height)


def replay(index: str, res, t: dict, pack: dict) -> None:
    """The trade on two charts: index (signal) and option (entry, stop, exit). Option candles: from the database."""
    day = t["date"] if isinstance(t["date"], date) else date.fromisoformat(str(t["date"]))
    bars = res.index_bars.get(day, [])
    idx = [{"time": b.ts + SHIFT, "open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in bars]
    tf = res.settings["timeframe"]
    mins = res.option_minutes.get((t["symbol"], day))
    if not mins:
        lo, hi = day_bounds(day)
        mins = PriceDB(Path(pack["db"])).minutes(t["symbol"], lo, hi)
    opt = resample(mins, tf)
    o = [{"time": b.ts + SHIFT, "open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in opt]

    def at(ts):
        return max((b.ts for b in bars if b.ts <= ts), default=ts) + SHIFT

    def at_opt(ts):
        return max((b.ts for b in opt if b.ts <= ts), default=ts) + SHIFT
    side_up = t["side"] == "CALL"
    data = {
        "index_title": f"{INDEXES[index].label} {tf}m - signal, entry, exit",
        "index": idx,
        "index_markers": [
            {"time": at(t["signal_ts"]), "position": "belowBar" if side_up else "aboveBar",
             "color": GREEN if side_up else RED, "shape": "arrowUp" if side_up else "arrowDown",
             "text": f"signal {t['side']}"},
            {"time": at(t["exit_ts"]), "position": "aboveBar", "color": "#ff9800", "shape": "circle",
             "text": "exit"}],
        "option_title": f"{t['symbol']} {tf}m - entry {t['entry_price']:g}, stop {t['stop']:g}, "
                        f"exit {t['exit_price']:g}",
        "option": o,
        "option_markers": [
            {"time": at_opt(t["entry_ts"]), "position": "belowBar", "color": BLUE, "shape": "arrowUp",
             "text": f"BUY {t['entry_price']:g}"},
            {"time": at_opt(t["exit_ts"]), "position": "aboveBar", "color": "#ff9800", "shape": "arrowDown",
             "text": f"SELL {t['exit_price']:g}"}],
        "lines": [{"price": t["entry_price"], "color": BLUE, "title": "entry"},
                  {"price": t["stop"], "color": RED, "title": "stop"},
                  {"price": t["exit_price"], "color": "#ff9800", "title": "exit"}],
    }
    if not o:
        st.caption("No option candles saved for this trade.")
    show_html(replay_html(data), 460)
    c = st.columns(6)
    for col, (k, v) in zip(c, (("Points", t["points"]), ("Net P&L", f"Rs {t['net_pnl']:,.0f}"),
                               ("R multiple", t["r_multiple"]), ("MAE", t["mae"]), ("MFE", t["mfe"]),
                               ("Minutes", t["minutes_held"]))):
        col.metric(k, v)
    st.caption(f"Why this strike: {t['picked']}.")


__all__ = ["render"]
