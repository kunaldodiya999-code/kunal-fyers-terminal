# Kunal FYERS Terminal

One simple screen connected to FYERS:

* **Login page first** (FYERS token, once per trading day).
* **Chart** (TradingView look): candles + **VWAP**, **BBP_NM** (bull / bear columns, Direction line) and
  **KTMI Uni Lite**. Timeframes **1 min, 3 min, 5 min**. Index: **NIFTY 50, BANK NIFTY, SENSEX**.
* **Option chain** on the right for the index on the chart, nearest expiry, prices only:
  NIFTY and BANK NIFTY: 5 ITM + ATM + 5 OTM. SENSEX: 8 ITM + ATM + 8 OTM. ATM row is highlighted.
* **Funds available** (live from FYERS), **Funds to utilize per new entry**, **Funds utilized %**.
* **Trade option**: Call box, Put box. **Premium select**: price range `[from] - [to]` or per lot amount.

All prices come live from the FYERS API. **Paper trades by default** (nothing is sent to FYERS).

## The strategy (owner's rules, 28 Oct 2026)

| When | What happens |
|---|---|
| BBP_NM **Direction crosses above 0** (on a FINISHED candle) | exit an open PUT; buy a CALL if the Call box is ticked |
| BBP_NM **Direction crosses below 0** (on a FINISHED candle) | exit an open CALL; buy a PUT if the Put box is ticked |
| Only Call ticked | calls are bought and exited; on a cross below 0 the call is exited and NO put is bought |
| Stop loss | the **low of the option's own candle** (same timeframe) that closed with the signal. Option price at or below it: exit |
| Time | new entries 09:30 to 15:00, everything closed at 15:10 (Settings tab) |
| Orders | **LIMIT only, never market**: buy at price + 2, sell at price - 2 (buffer in Settings) |

**Which strike** (choose one mode):

* **Price range** e.g. `180 - 200`: of the strikes priced inside the range, the **lower price** is bought
  (73000 CE at 195 and 72900 CE at 182: buys 72900 CE).
* **Per lot amount** e.g. `6500`: NIFTY lot 65, so it buys the price nearest to Rs 100 without going above.

**How many lots**: whole lots that "Funds to utilize per new entry" buys (Rs 20,000 at Rs 6,500 a lot = 3 lots),
never more than the funds available. KTMI Uni Lite is shown on the chart only; it does not trade.

## Install (one time) — on your PC, PowerShell

1. Open PowerShell and go to your Kunal folder, then download this project:
   ```
   cd C:\Users\USER\Desktop\Kunal
   git clone https://github.com/kunaldodiya999-code/kunal-fyers-terminal.git
   cd kunal-fyers-terminal
   ```
   You are now in `C:\Users\USER\Desktop\Kunal\kunal-fyers-terminal`.
2. One command does the whole setup (its own Python box `.venv`, all parts, FYERS library, tests):
   ```
   python setup_pc.py
   ```
   It takes a few minutes and must end with `35 passed` and `ALL DONE`.
   (No `Activate.ps1` needed, so the Windows "running scripts is disabled" message does not matter.)

## Your FYERS app (one time)

On **myapi.fyers.in** > Dashboard you need an app (you may already have one for KunalQuant: the same one works).
Note its **App ID** (like `ABCD1234-100`), **Secret ID** and **Redirect URL**. The Redirect URL you type in the
terminal must be exactly the same as in the FYERS app (e.g. `http://127.0.0.1:8000`).

## Every trading day

1. Double-click **`start_terminal.bat`** in the folder `C:\Users\USER\Desktop\Kunal\kunal-fyers-terminal`
   (or in PowerShell in that folder: `.\.venv\Scripts\python.exe -m streamlit run app.py`).
   The browser opens the terminal (if not, open http://localhost:8501). Keep this PowerShell window open.
2. **Login page**:
   * Step 1 (first time only): App ID, Secret ID, Redirect URL, then **Save app details**
     (saved in `.env` in this folder; never uploaded to GitHub).
   * Step 2: **Open FYERS login**, log in (mobile, PIN, OTP), allow the app.
   * Step 3: the browser opens your Redirect URL; the page may say "This site can't be reached" — that is fine.
     Copy the **whole address** from the address bar (it has `auth_code=` in it), paste it, click **Authorize**.
3. The terminal opens. The first start of the day downloads FYERS' contract lists (a minute or so).
4. Fill **Funds to utilize per new entry**, tick **Call** / **Put**, choose the **premium** mode and fill it.
   Everything you type is saved and used straight away.
5. Tabs under the chart: **Today's trades**, **What happened** (every signal and why a trade was or was not taken),
   **Settings** (times, buffer, BBP lookback, Real orders).

To stop: close the browser tab and close the black window (or press `Ctrl + C` in it).

## Manual trade (R:R box) - your own levels, the program does the size and the orders

At the top choose **Manual trade (R:R box)** (or open it full screen with the link above the chart).

1. Pick the index and the option (e.g. `25000 CE`) and the timeframe.
2. Press **+ Long box**, then click your ENTRY price on the chart. A box appears: blue ENTRY, red STOP, green TARGET.
   For a bearish view draw the Long box on a PUT chart (bearish = buy put, never sell options).
3. Drag the three lines. The ticket shows lots, quantity, money, rupee risk and reward, R:R and which limit decided
   the size: `lots = min(capital x allocation% / (entry x lot), max risk / ((entry - stop) x lot))`.
   Lot size comes from FYERS every morning. "Strict sizing" (switch) counts the buffers and charges too.
4. Press **Arm**. Nothing is sent until the price REACHES the entry; then a BUY LIMIT at price + Rs 2.
5. When it fills, ONE protective SELL SL-L sits at FYERS (trigger = stop, limit = stop - Rs 2). It works even if your
   PC or internet stops.
6. Drag the STOP: after you let go, the SL-L order is modified at FYERS. A drag that would risk more than Max risk is
   refused (the quantity is never reduced). Trailing the stop into profit is allowed.
7. TARGET reached: the SAME order becomes a LIMIT SELL at price - Rs 2 (never two sell orders). Exit now / 15:10:
   the same. If the price jumps below the SL-L limit, the limit follows the price down - never a market order.

Settings on the right: capital allocation %, max risk per trade, **max loss per day** (empty = no limit), buffer,
strict sizing, paper capital, real orders (+ max lots for real orders, 1 to start). No cap on the number of trades.
Prices come from the FYERS live websocket (milliseconds); the time a price takes to reach the screen is shown at the top.

## KILL SWITCH (on every page)

The red **KILL SWITCH** (top of every page, and on the trading desk): type `KILL`, press ACTIVATE.
* Every pending order is cancelled, every open position is closed with LIMIT orders (re-priced until done, never
  market), and the terminal sends NO new order for **12 hours** (also after a restart).
* With real orders OFF (paper), only paper trades are closed: your FYERS account is not touched.
* FYERS has no API for its own kill switch ("Take a Break"): press it yourself in the FYERS app or on trade.fyers.in
  -> Profile if you want FYERS to block you too.
* See it / end it early (folder `C:\Users\USER\Desktop\Kunal\kunal-fyers-terminal`):
  `.\.venv\Scripts\python.exe kill_switch.py` (status) / `.\.venv\Scripts\python.exe kill_switch.py --off`.

## Backtest (past days, real option prices)

At the top of the terminal choose **Backtest** (instead of Live terminal). Pick the index (NIFTY 50, SENSEX, ...),
timeframe, dates (default: the last 30 trading days), Call / Put, premium mode, funds per entry, starting capital,
slippage and charges, then **Run backtest**.

* Same rules as live: a Direction cross on a finished candle, strike from the real option chain at that moment,
  stop = the option's signal-candle low, exit on the opposite cross or 15:10, limit buffer.
* Data: the shared database `C:\Users\USER\Desktop\Kunal\KunalQuant\data\database\kunal_quant.db` first.
  Missing index days and option candles are fetched from FYERS (expired options too) and ADDED to that database
  (nothing is changed or deleted; one backup copy is made in `data\database\backups` before the first write).
  The first run of 30 days takes a few minutes; later runs are fast.
* Report: Net P&L, win rate, profit factor, expectancy, max drawdown, Sharpe / Sortino, equity and drawdown curves,
  daily P&L, monthly table, P&L spread, breakdowns (side, exit reason, hour, weekday, expiry day), every trade with
  MAE / MFE / R multiple, a chart replay of any trade (index + option), and every signal with what happened.
* Fills are realistic: entry at the next minute's open + slippage (no fill if it ran more than buffer + Rs 1 away),
  exit at the next open - slippage, stop at the stop price (or worse if the price jumped through it). Charges:
  brokerage Rs 20 an order, STT, exchange, SEBI, stamp, GST.
* Results are saved in `data\backtests\<time>_<index>\` (trades.csv, signals.csv, days.csv, summary.json).
  The "Signals & notes" tab has a summary you can paste to Claude.

From PowerShell instead (same engine), folder `C:\Users\USER\Desktop\Kunal\kunal-fyers-terminal`, after today's
login in the terminal:
```
.\.venv\Scripts\python.exe backtest.py --index NIFTY SENSEX --days 30 --timeframe 3 --low 180 --high 200 --funds 20000
```
(`--per-lot 6500` instead of `--low/--high` for the per lot amount mode.)

## Real orders (later, only when you decide)

Two locks, both needed, every day:
1. Settings tab: switch **Real orders** ON.
2. In PowerShell in the same folder: `.\.venv\Scripts\python.exe arm_real_orders.py` and type the phrase it shows.
   It is valid for TODAY only. `.\.venv\Scripts\python.exe arm_real_orders.py --off` switches it off.

FYERS only accepts API orders from your **whitelisted static IP** (SEBI rule 2026): set it up with FYERS first.
Orders are LIMIT orders only (buy price + buffer, re-priced at most Rs 1 higher, sell price - buffer). Never market.

## Files

| Path | What |
|---|---|
| `app.py` | the screen (login + terminal) |
| `terminal/engine.py` | the loop: prices, chain, candles, signals, stop loss, 15:10, chart data |
| `terminal/strategy.py` | the owner's rules (strike pick, lots, crosses) |
| `terminal/indicators.py` | BBP_NM (from the owner's Pine code), VWAP, KTMI Uni Lite |
| `terminal/fyers.py` | FYERS login, quotes, history, funds, contract list |
| `terminal/broker.py` | paper fills and real limit orders |
| `terminal/risk/` | Manual trade desk: sizing, trade states, paper / FYERS orders, live feed, local fast link, chart page |
| `terminal/killswitch.py`, `kill_switch.py` | kill switch (12 h, cancel + limit close-out) |
| `terminal/backtest.py`, `metrics.py`, `history.py`, `backtest_view.py`, `backtest.py` | the backtest: engine, statistics, past data (database + FYERS), screen, command |
| `indicators/ktmi*`, `live_trading/` | copied unchanged from kunal-quant-claude-version (KTMI is locked) |
| `data/` (on your PC only) | token, settings, open trade, trades.csv, events.log |

Charts by TradingView Lightweight Charts™ (Apache 2.0, `static/lightweight-charts/LICENSE`).
