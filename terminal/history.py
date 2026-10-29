"""Past data for the backtest: the ONE shared price database first, FYERS only for what is missing.

Database: KunalQuant\\data\\database\\kunal_quant.db (the same file kunal-quant-claude-version uses). Where it is:
the box on the Backtest screen, else the environment variable KQ_DATABASE, else
C:\\Users\\USER\\Desktop\\Kunal\\KunalQuant\\data\\database\\kunal_quant.db.
Writes are ADD-ONLY, in KunalQuant's own format (instruments row broker 'FYERS', candles resolution '1',
source FYERS_HISTORY for live contracts / FYERS_FNO_HISTORY for expired options, INSERT OR IGNORE: nothing already
there is ever changed). Before the FIRST write a backup copy is made next to it (data\\database\\backups).
No database on this PC: a new one is made in this project's data/ folder.

FYERS (API v3):
  * history(): index and live contracts, 1-minute, up to 100 days a call;
  * expiry_dates() / history_underlying_symbols(): past expiries and the option symbols of an expired expiry;
  * fno_historical_data(): 1-minute candles of an EXPIRED option.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from terminal.candles import IST, Candle, parse_history
from terminal.fyers import INDEXES, Contracts, data_dir, load_master, nearest_contracts

WINDOWS_DB = Path(r"C:\Users\USER\Desktop\Kunal\KunalQuant\data\database\kunal_quant.db")
SOURCE_LIVE = "FYERS_HISTORY"
SOURCE_EXPIRED = "FYERS_FNO_HISTORY"
FULL_DAY_MINUTES = 360            # a day with fewer index minutes (of 375) is asked again from FYERS
CALL_GAP = 0.35                   # seconds between FYERS calls (their limit is 200 a minute)
DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, broker TEXT NOT NULL, exchange TEXT NOT NULL, segment TEXT NOT NULL,
    symbol TEXT NOT NULL, broker_token TEXT, security_id TEXT, underlying TEXT, instrument_type TEXT NOT NULL,
    expiry_date TEXT, strike REAL, option_type TEXT, lot_size INTEGER, tick_size REAL, currency TEXT DEFAULT 'INR',
    is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (broker, symbol));
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id INTEGER NOT NULL, timestamp INTEGER NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER,
    open_interest INTEGER, resolution TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (instrument_id, timestamp, resolution, source));
CREATE INDEX IF NOT EXISTS idx_candles_instrument_timestamp ON candles(instrument_id, timestamp);
"""


def default_db() -> Path:
    if os.environ.get("KQ_DATABASE"):
        return Path(os.environ["KQ_DATABASE"])
    return WINDOWS_DB if WINDOWS_DB.exists() else data_dir() / "kunal_quant.db"


def day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=IST)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def strike_from_symbol(symbol: str) -> Optional[float]:
    """NSE:NIFTY2692223250CE (weekly YYMDD) or NSE:NIFTY26SEP23250CE (monthly YYMON): strike = after 5 date chars."""
    m = re.match(r"^[A-Z]+:([A-Z]+?)(\d\d[0-9OND]\d\d|\d\d[A-Z]{3})(\d+(?:\.\d+)?)(CE|PE)$", symbol)
    return float(m.group(3)) if m else None


def _find_dates(value: Any) -> set[date]:
    """Every date inside a FYERS answer (ISO text or epoch seconds under an 'expiry' key), whatever its shape."""
    out: set[date] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (int, float)) and "expir" in str(k).lower() and v > 10 ** 9:
                out.add(datetime.fromtimestamp(int(v), IST).date())
            else:
                out |= _find_dates(v)
    elif isinstance(value, list):
        for v in value:
            out |= _find_dates(v)
    elif isinstance(value, str):
        out |= {date.fromisoformat(x) for x in DATE_RE.findall(value)}
        if value.isdigit() and len(value) == 10:
            out.add(datetime.fromtimestamp(int(value), IST).date())
    return out


def _find_symbols(value: Any, prefix: str) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_find_symbols(v, prefix) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_find_symbols(v, prefix) for v in value)) if value else set()
    if isinstance(value, str) and value.startswith(prefix) and value.endswith(("CE", "PE")):
        return {value}
    return set()


class PriceDB:
    """Read (and add to) the shared database."""

    def __init__(self, path: Optional[Path] = None, *, log: Callable[[str], Any] = print) -> None:
        self.path = Path(path) if path else default_db()
        self.log = log
        self._checked = False
        self._ids: dict[str, int] = {}

    def _read(self) -> Optional[sqlite3.Connection]:
        if not self.path.exists():
            return None
        return sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=30)

    def minutes(self, symbol: str, start: int, end: int) -> list[Candle]:
        db = self._read()
        if db is None:
            return []
        try:
            rows = db.execute(
                "SELECT c.timestamp, MAX(c.open), MAX(c.high), MIN(c.low), MAX(c.close), MAX(COALESCE(c.volume, 0)) "
                "FROM candles c JOIN instruments i ON i.id = c.instrument_id WHERE i.symbol = ? AND "
                "c.resolution = '1' AND c.timestamp >= ? AND c.timestamp < ? GROUP BY c.timestamp "
                "ORDER BY c.timestamp", (symbol, start, end)).fetchall()
        finally:
            db.close()
        return [Candle(int(t), float(o), float(h), float(l), float(c), float(v or 0)) for t, o, h, l, c, v in rows]

    def option_symbols(self, underlying: str, expiry: date) -> dict[tuple[float, str], str]:
        db = self._read()
        if db is None:
            return {}
        try:
            rows = db.execute("SELECT symbol, strike, option_type FROM instruments WHERE underlying = ? AND "
                              "instrument_type = 'OPTION' AND expiry_date = ?", (underlying, expiry.isoformat())
                              ).fetchall()
        finally:
            db.close()
        return {(float(k), t): s for s, k, t in rows if k is not None and t in ("CE", "PE")}

    def _writer(self) -> sqlite3.Connection:
        if not self._checked:
            self._checked = True
            if self.path.exists():
                folder = self.path.parent / "backups"
                if not any(folder.glob("kunal_quant_before_kunal_fyers_terminal_*.db")):
                    folder.mkdir(parents=True, exist_ok=True)
                    target = folder / f"kunal_quant_before_kunal_fyers_terminal_{date.today().isoformat()}.db"
                    self.log(f"First write to the shared database: backup copy -> {target} (once)")
                    src, dst = self._read(), sqlite3.connect(target)
                    try:
                        src.backup(dst)
                    finally:
                        src.close()
                        dst.close()
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        if not self._has_tables(db):
            db.executescript(SCHEMA)
        return db

    @staticmethod
    def _has_tables(db: sqlite3.Connection) -> bool:
        return db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
                          "('instruments', 'candles')").fetchone()[0] == 2

    def add(self, symbol: str, candles: Iterable[Candle], *, source: str, underlying: str = "",
            expiry: Optional[date] = None, strike: Optional[float] = None, option_type: Optional[str] = None,
            lot_size: Optional[int] = None) -> int:
        rows = [(int(c.ts), c.open, c.high, c.low, c.close, int(c.volume or 0)) for c in candles]
        if not rows:
            return 0
        db = self._writer()
        try:
            return self._add(db, symbol, rows, source, underlying, expiry, strike, option_type, lot_size)
        finally:
            db.close()

    def _add(self, db, symbol, rows, source, underlying, expiry, strike, option_type, lot_size) -> int:
        with db:
            if symbol not in self._ids:
                row = db.execute("SELECT id FROM instruments WHERE broker='FYERS' AND symbol=?", (symbol,)).fetchone()
                if row is None:
                    option = option_type is not None
                    db.execute(
                        "INSERT OR IGNORE INTO instruments (broker, exchange, segment, symbol, underlying, "
                        "instrument_type, expiry_date, strike, option_type, lot_size, tick_size, currency, is_active) "
                        "VALUES ('FYERS', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.05, 'INR', 1)",
                        (symbol.split(":")[0], "11" if option else "10", symbol, underlying,
                         "OPTION" if option else "INDEX", expiry.isoformat() if expiry else None, strike,
                         option_type, lot_size))
                    row = db.execute("SELECT id FROM instruments WHERE broker='FYERS' AND symbol=?",
                                     (symbol,)).fetchone()
                self._ids[symbol] = int(row[0])
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO candles (instrument_id, timestamp, open, high, low, close, volume, "
                           "resolution, source) VALUES (?,?,?,?,?,?,?,'1',?)",
                           [(self._ids[symbol], *r, source) for r in rows])
            return db.total_changes - before


class History:
    """Index and option 1-minute candles for past days (database first, FYERS for the gaps)."""

    def __init__(self, client, db: PriceDB, *, today: Optional[date] = None, log: Callable[[str], Any] = print,
                 master_loader: Optional[Callable[[str, date], dict]] = None, pause: float = CALL_GAP) -> None:
        self.client, self.db, self.log, self.pause = client, db, log, pause
        self.today = today or datetime.now(IST).date()
        self.master_loader = master_loader or (lambda name, d: load_master(name, d))
        self._last_call = 0.0
        self._expiries: dict[str, list[date]] = {}
        self._symbols: dict[tuple[str, date], dict[tuple[float, str], str]] = {}
        self._minutes: dict[tuple[str, date], list[Candle]] = {}
        self.fetched = 0

    @property
    def model(self):
        return getattr(self.client, "model", self.client)

    def _call(self, fn, *args, **kwargs):
        wait = self.pause - (_time.monotonic() - self._last_call)
        if wait > 0:
            _time.sleep(wait)
        try:
            return fn(*args, **kwargs)
        finally:
            self._last_call = _time.monotonic()
            self.fetched += 1

    # ------------------------------------------------------------------ the index
    def index_days(self, index: str, start: date, end: date) -> dict[date, list[Candle]]:
        """Trading day -> 1-minute candles of the index (09:15-15:29). Days FYERS has nothing for = holidays."""
        symbol = INDEXES[index].spot
        lo, _ = day_bounds(start)
        _, hi = day_bounds(end)
        have: dict[date, list[Candle]] = {}
        for c in self.db.minutes(symbol, lo, hi):
            have.setdefault(datetime.fromtimestamp(c.ts, IST).date(), []).append(c)
        missing = [d for d in _weekdays(start, end) if len(have.get(d, [])) < FULL_DAY_MINUTES]
        for a, b in _spans(missing):
            self.log(f"{index}: asking FYERS for 1-minute candles {a} to {b} (not complete in the database)")
            got = parse_history(self._call(self.model.history, data={
                "symbol": symbol, "resolution": "1", "date_format": "1", "range_from": a.isoformat(),
                "range_to": b.isoformat(), "cont_flag": "1"}))
            added = self.db.add(symbol, got, source=SOURCE_LIVE, underlying=index)
            if added:
                self.log(f"  added {added} candles to the database")
            fresh: dict[date, dict[int, Candle]] = {}
            for c in got:
                fresh.setdefault(datetime.fromtimestamp(c.ts, IST).date(), {})[c.ts] = c
            for d, by_ts in fresh.items():
                merged = {c.ts: c for c in have.get(d, [])}
                merged.update(by_ts)
                have[d] = list(merged.values())
        return {d: sorted(v, key=lambda c: c.ts) for d, v in sorted(have.items()) if v}

    # ------------------------------------------------------------------ expiries and contracts
    def expiries(self, index: str, start: date) -> list[date]:
        if index in self._expiries:
            return self._expiries[index]
        info = INDEXES[index]
        found: set[date] = set()
        try:
            answer = self._call(self.model.expiry_dates, data={
                "symbol": info.spot, "range_from": (start - timedelta(days=3)).isoformat(),
                "range_to": self.today.isoformat(), "date_format": "1"})
            found |= {d for d in _find_dates(answer) if d >= start - timedelta(days=3)}
        except Exception as exc:          # older library / no permission: the live list still covers today on
            self.log(f"{index}: FYERS past expiry list not available ({exc})")
        try:
            master = self.master_loader(info.master, self.today)
            found |= {datetime.fromtimestamp(int(r["expiryDate"]), IST).date() for r in master.values()
                      if r.get("underSym") == info.underlying and r.get("optType") in ("CE", "PE")}
        except Exception as exc:
            self.log(f"{index}: FYERS contract list not available ({exc})")
        self._expiries[index] = sorted(found)
        return self._expiries[index]

    def expiries_from(self, index: str, day: date, start: date) -> list[date]:
        return [e for e in self.expiries(index, start) if e >= day]

    def nearest_expiry(self, index: str, day: date, start: date) -> Optional[date]:
        later = self.expiries_from(index, day, start)
        return later[0] if later else None

    def lot_size(self, index: str) -> int:
        try:
            info = INDEXES[index]
            return nearest_contracts(self.master_loader(info.master, self.today), info.underlying, self.today).lot_size
        except Exception:
            return {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}[index]

    def chain(self, index: str, expiry: date) -> dict[tuple[float, str], str]:
        """(strike, 'CE'/'PE') -> FYERS symbol for one expiry."""
        key = (index, expiry)
        if key in self._symbols:
            return self._symbols[key]
        info = INDEXES[index]
        out = self.db.option_symbols(info.underlying, expiry)
        if expiry >= self.today:
            master = self.master_loader(info.master, self.today)
            for sym, r in master.items():
                if r.get("underSym") == info.underlying and r.get("optType") in ("CE", "PE") and \
                        datetime.fromtimestamp(int(r["expiryDate"]), IST).date() == expiry:
                    out[(float(r["strikePrice"]), r["optType"])] = str(r.get("symTicker") or sym)
        else:
            try:
                answer = self._call(self.model.history_underlying_symbols,
                                    data={"symbol": info.spot, "expiry_date": expiry.isoformat()})
                for sym in _find_symbols(answer, info.spot.split(":")[0] + ":" + info.underlying):
                    k = strike_from_symbol(sym)
                    if k is not None:
                        out[(k, sym[-2:])] = sym
            except Exception as exc:
                self.log(f"{index} {expiry}: FYERS expired contract list failed ({exc})")
        self._symbols[key] = out
        return out

    def contracts(self, index: str, expiry: date, lot_size: int) -> Contracts:
        ch = self.chain(index, expiry)
        strikes = sorted({k for k, _ in ch})
        step = min(b - a for a, b in zip(strikes, strikes[1:])) if len(strikes) > 1 else 50.0
        return Contracts(expiry, lot_size, step, {k: s for (k, t), s in ch.items() if t == "CE"},
                         {k: s for (k, t), s in ch.items() if t == "PE"}, None)

    # ------------------------------------------------------------------ option candles
    def option_minutes(self, index: str, symbol: str, expiry: date, day: date, lot_size: int) -> list[Candle]:
        key = (symbol, day)
        if key in self._minutes:
            return self._minutes[key]
        lo, hi = day_bounds(day)
        got = self.db.minutes(symbol, lo, hi)
        if len(got) < 30:                                    # not in the database (or a thin bit): ask FYERS
            expired = expiry < self.today
            request = {"symbol": symbol, "resolution": "1", "date_format": "1", "range_from": day.isoformat(),
                       "range_to": day.isoformat()}
            try:
                if expired:
                    answer = self._call(self.model.fno_historical_data, {**request, "include_oi": "0",
                                                                         "include_greeks": "0"})
                else:
                    answer = self._call(self.model.history, data={**request, "cont_flag": "1"})
                fresh = [c for c in parse_history(answer) if lo <= c.ts < hi]
            except Exception as exc:
                self.log(f"  {symbol} {day}: FYERS gave no candles ({str(exc)[:120]})")
                fresh = []
            if fresh:
                self.db.add(symbol, fresh, source=SOURCE_EXPIRED if expired else SOURCE_LIVE,
                            underlying=INDEXES[index].underlying, expiry=expiry, strike=strike_from_symbol(symbol),
                            option_type=symbol[-2:], lot_size=lot_size)
                merged = {c.ts: c for c in got}
                merged.update({c.ts: c for c in fresh})
                got = sorted(merged.values(), key=lambda c: c.ts)
        self._minutes[key] = got
        return got


def _weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _spans(days: list[date], max_days: int = 90) -> list[tuple[date, date]]:
    """Missing days -> few (first, last) ranges for FYERS (at most 100 days a call)."""
    spans: list[tuple[date, date]] = []
    for d in sorted(days):
        if spans and (d - spans[-1][1]).days <= 4 and (d - spans[-1][0]).days < max_days:
            spans[-1] = (spans[-1][0], d)
        else:
            spans.append((d, d))
    return spans


__all__ = ["History", "PriceDB", "default_db", "strike_from_symbol"]
