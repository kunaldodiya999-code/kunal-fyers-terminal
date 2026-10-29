"""Backtest from PowerShell (same engine as the Backtest screen). Needs today's FYERS login (the terminal's login page).

    .venv\\Scripts\\python.exe backtest.py --index NIFTY SENSEX --days 30 --timeframe 3 --low 180 --high 200 --funds 20000

Per lot amount instead of a price range:  --per-lot 6500
Prints a summary to paste to Claude; full results in data\\backtests\\<time>_<index>\\.
"""

import argparse
from datetime import datetime
from pathlib import Path

from terminal.backtest import BacktestSettings, run
from terminal.backtest_view import back_n_weekdays, last_trading_day, save_run, text_summary
from terminal.candles import IST
from terminal.fyers import INDEXES, Fyers, credentials, saved_token
from terminal.history import History, PriceDB, default_db
from terminal.metrics import group_stats, summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", nargs="+", default=["NIFTY", "SENSEX"], choices=list(INDEXES))
    ap.add_argument("--days", type=int, default=30, help="trading days back from the last finished day")
    ap.add_argument("--timeframe", type=int, default=3, choices=(1, 3, 5))
    ap.add_argument("--low", type=float, default=0.0)
    ap.add_argument("--high", type=float, default=0.0)
    ap.add_argument("--per-lot", type=float, default=0.0)
    ap.add_argument("--funds", type=float, default=20000.0, help="funds per new entry")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--slippage", type=float, default=0.5)
    ap.add_argument("--no-charges", action="store_true")
    ap.add_argument("--no-call", action="store_true")
    ap.add_argument("--no-put", action="store_true")
    ap.add_argument("--db", default=str(default_db()))
    a = ap.parse_args()
    token, creds = saved_token(), credentials()
    if not token or not creds["client_id"]:
        raise SystemExit("Log in first: start the terminal (start_terminal.bat) and finish the FYERS login page.")
    client = Fyers(creds["client_id"], token)
    end = last_trading_day(datetime.now(IST).date())
    start = back_n_weekdays(end, a.days)
    for index in a.index:
        cfg = BacktestSettings(index=index, start=start, end=end, timeframe=a.timeframe, call_on=not a.no_call,
                               put_on=not a.no_put, strike_mode="PER_LOT" if a.per_lot else "RANGE",
                               premium_low=a.low, premium_high=a.high, per_lot_amount=a.per_lot,
                               funds_per_entry=a.funds, capital=a.capital, slippage=a.slippage,
                               charges=not a.no_charges)
        hist = History(client, PriceDB(Path(a.db)))
        res = run(hist, cfg, progress=lambda f, m: print(f"  {m}", flush=True) if m.endswith(")") else None)
        stats = summary(res.trades, res.days, a.capital)
        folder = save_run(index, res, stats)
        print("\n" + "=" * 90 + "\n" + text_summary(index, res, stats))
        for title, key in (("By side", "side"), ("By exit reason", "exit_reason")):
            print(f"-- {title}")
            for g in group_stats(res.trades, lambda t, k=key: t[k]):
                print(f"   {g['group']}: {g['trades']} trades, win {g['win_rate_%']}%, net Rs {g['net_pnl']:,.0f}, "
                      f"PF {g['profit_factor']}")
        print(f"Saved: {folder}  (FYERS calls: {hist.fetched})")


if __name__ == "__main__":
    main()
