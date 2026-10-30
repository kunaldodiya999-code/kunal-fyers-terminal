# Notes for Claude (read first)

- Owner: Kunal, about 10% coding knowledge, Windows + VS Code + PowerShell. Explain step by step, plain words, always
  give the folder: `C:\Users\USER\Desktop\Kunal\kunal-fyers-terminal` (its own `.venv`, start: start_terminal.bat).
- Setup on the PC: `python setup_pc.py` (fyers-apiv3 installed --no-deps: its aiohttp 3.9.3 pin cannot build on
  Python 3.13 Windows; owner's PowerShell blocks Activate.ps1, so use `.venv\Scripts\python.exe` / start_terminal.bat).
- Tests: `python -m pytest tests -q` (tests/conftest.py keeps every test in a temporary data folder).
- Strategy = owner's rules 28 Oct 2026, see README and `terminal/strategy.py`: BBP_NM Direction cross above 0 -> CALL,
  below 0 -> PUT, finished candles only, SL = option's signal-candle low, exit on the opposite cross, 09:30-15:00,
  15:10 exit. One strike mode at a time (RANGE cheapest inside / PER_LOT nearest at or below). KTMI = chart only.
- NEVER market orders; limit + buffer (2). Real orders need Settings "Real orders" AND `arm_real_orders.py` (today).
  Bearish = BUY PUT, never sell options.
- `indicators/ktmi_uni_lite/KTMI_Uni_Lite_FINAL.pine` must never change (SHA test). `indicators/ktmi/` calculations
  and `live_trading/` are copies from kunal-quant-claude-version: change them there first.
- FYERS data only. Index has no volume: VWAP uses the nearest FUTURES volume (owner rule).
- Never commit `.env`, `data/`, tokens.
- Backtest (owner 29 Oct 2026): "Backtest" view in app.py + `backtest.py` command. Engine `terminal/backtest.py` must
  keep the SAME rules as the live engine (uses strategy.plan / pick_strike / lots_for). Data `terminal/history.py`:
  shared KunalQuant DB first, FYERS for gaps (history; expired options via expiry_dates / history_underlying_symbols /
  fno_historical_data), writes add-only in KunalQuant format + one backup. No real price data in this container:
  the owner runs it and pastes the summary.
- Paper trades size from Settings `paper_capital` (owner's FYERS Available Balance showed Rs 0 on 29 Oct).
- Manual trade desk (owner 30 Oct 2026, branch feature/chart-risk-engine, design docs/design/Chart_Risk_Engine_Design.pdf):
  `terminal/risk/` (sizing.py, desk.py states, gateways.py paper/FYERS, feed.py FYERS data+order websockets,
  service.py local starlette server 127.0.0.1 + secret key + origin check, web/desk.js chart + box). Owner answers:
  box on our chart OK if ms latency; option chart first; sizing PLAIN default + Strict switch; entry only when price
  reaches the box; stop SL-L at exchange + target watched (one exit order, modified); risky stop drag REFUSED (never
  reduce qty); no trade cap; max loss/day box (empty = none); no partial exits yet; separate capital for now; static
  IP not confirmed. Real orders: setting + daily arm + real_max_lots (default 1).
- KILL SWITCH on every page (`terminal/killswitch.py`): 12 h lock for every new order (desk, BBP engine, gateways),
  cancel all pending then ONE limit exit per position (flatten_account, real mode only). FYERS has NO kill-switch API
  (checked changelog to 16 Oct 2026): owner presses FYERS "Take a Break" himself.

