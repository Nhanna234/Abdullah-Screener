"""
Data layer for the EMA-Reclaim app (no Streamlit imports - testable on its own).

* Index constituents : official NSE Indices CSV files, three mirrors tried in turn.
                       Nifty Total Market falls back to Nifty 500 + Nifty Microcap 250
                       (NSE's own definition of the index).
* Stock prices       : Yahoo Finance via yfinance, daily OHLCV, split + dividend adjusted.
                       Kept in a per-symbol store on disk, so every index shares the same
                       downloads and a refresh only fetches the last few days. If Yahoo
                       re-adjusts history (new split / dividend) the symbol is re-downloaded.
* Benchmarks         : Nifty 50 / Nifty 500 Total Return Index (or price index) from the
                       niftyindices.com historical API; Yahoo price index as fallback.
"""

from __future__ import annotations

import io
import json
import os
import pickle
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NI_BASE = "https://www.niftyindices.com"
DATA_START = "2003-01-01"
FIELDS = ("Open", "High", "Low", "Close", "Volume")
SESSION_CUTOFF_IST = (17, 0)        # after 5 pm IST the day's prices count as final

INDEX_FILES = {
    "Nifty 50": "ind_nifty50list.csv",
    "Nifty 100": "ind_nifty100list.csv",
    "Nifty 200": "ind_nifty200list.csv",
    "Nifty LargeMidcap 250": "ind_niftylargemidcap250list.csv",
    "Nifty Smallcap 100": "ind_niftysmallcap100list.csv",
    "Nifty Smallcap 250": "ind_niftysmallcap250list.csv",
    "Nifty 500": "ind_nifty500list.csv",
    "Nifty Microcap 250": "ind_niftymicrocap250_list.csv",
    "Nifty Total Market": "ind_niftytotalmarket_list.csv",
}
CONSTITUENT_MIRRORS = ["https://niftyindices.com/IndexConstituent/{f}",
                       "https://www.niftyindices.com/IndexConstituent/{f}",
                       "https://nsearchives.nseindia.com/content/indices/{f}"]
# NSE: "All stocks that are part of the Nifty 500 index and Nifty Microcap 250 index
# will form part of the Nifty Total Market index at all points in time"
DERIVED = {"Nifty Total Market": ("Nifty 500", "Nifty Microcap 250")}

BENCH = {"Nifty 50": ("NIFTY 50", "^NSEI"), "Nifty 500": ("NIFTY 500", "^CRSLDX")}


# ════════════════════════════════════════════════════════════════════ time
def last_cutoff(now: datetime | None = None) -> datetime:
    """Most recent weekday 5 pm IST that has passed - prices fetched before it are stale."""
    now = (now or datetime.now(IST)).astimezone(IST)
    d = now.date()
    if (now.hour, now.minute) < SESSION_CUTOFF_IST:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return datetime(d.year, d.month, d.day, *SESSION_CUTOFF_IST, tzinfo=IST)


# ════════════════════════════════════════════════════════════ constituents
def _clean(s) -> str:
    return str(s).strip().upper().replace(".NS", "")


def parse_constituents(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if "Symbol" not in df.columns:
        raise ValueError(f"No 'Symbol' column (columns: {list(df.columns)})")
    if "Series" in df.columns:
        df = df[df["Series"].astype(str).str.strip().isin(["EQ", "BE", "BZ"])]
    df["Symbol"] = df["Symbol"].map(_clean)
    for c in ("Company Name", "Industry"):
        if c not in df.columns:
            df[c] = ""
    return df.drop_duplicates("Symbol")[["Symbol", "Company Name", "Industry"]].reset_index(drop=True)


def _download_list(fname: str) -> tuple[pd.DataFrame, str]:
    errors = []
    for m in CONSTITUENT_MIRRORS:
        url = m.format(f=fname)
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=25)
            r.raise_for_status()
            df = parse_constituents(pd.read_csv(io.StringIO(r.text)))
            if len(df) == 0:
                raise ValueError("empty list")
            return df, url
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
    raise RuntimeError("\n".join(errors))


def fetch_constituents(index_name: str) -> tuple[pd.DataFrame, str]:
    try:
        return _download_list(INDEX_FILES[index_name])
    except Exception as e1:  # noqa: BLE001
        if index_name in DERIVED:
            parts, srcs = [], []
            for sub in DERIVED[index_name]:
                d, s = _download_list(INDEX_FILES[sub])
                parts.append(d)
                srcs.append(s)
            df = pd.concat(parts).drop_duplicates("Symbol").reset_index(drop=True)
            return df, "built from " + " + ".join(DERIVED[index_name]) + " lists"
        raise RuntimeError(f"Could not download the {index_name} list. Upload it from the "
                           f"sidebar (NSE CSV with a 'Symbol' column).\n{e1}")


# ═══════════════════════════════════════════════════════════════ prices
def _yf_download(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    """One yfinance call for a batch. Returns {SYMBOL: OHLCV frame} for tickers with data."""
    import yfinance as yf
    raw = yf.download(tickers, start=start, auto_adjust=True, actions=False,
                      progress=False, threads=True, group_by="column")
    out = {}
    if raw is None or raw.empty:
        return out
    if not isinstance(raw.columns, pd.MultiIndex):        # single ticker, flat columns
        raw.columns = pd.MultiIndex.from_product([raw.columns, tickers[:1]])
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    lvl = 1 if set(FIELDS) & set(raw.columns.get_level_values(0)) else 0
    for t in tickers:
        try:
            df = raw.xs(t, level=lvl, axis=1)
        except KeyError:
            continue
        df = df.reindex(columns=list(FIELDS)).dropna(how="all", subset=["Close"])
        if len(df):
            out[_clean(t)] = df.astype("float64")
    return out


class PriceStore:
    """Per-symbol daily OHLCV cache (memory + disk), shared by every index and every user."""

    def __init__(self, cache_dir: str, downloader=None, batch: int = 50, pause: float = 1.0):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.mem: dict[str, dict] = {}
        self.downloader = downloader or (lambda tickers, start: _yf_download(tickers, start))
        self.batch = batch
        self.pause = pause
        self.version = 0                       # bumps on every change -> cache key for callers

    # ---- persistence
    def _path(self, sym: str) -> str:
        return os.path.join(self.dir, quote(sym, safe="") + ".pkl")

    def _get(self, sym: str) -> dict | None:
        if sym in self.mem:
            return self.mem[sym]
        p = self._path(sym)
        if os.path.exists(p):
            try:
                with open(p, "rb") as fh:
                    rec = pickle.load(fh)
                self.mem[sym] = rec
                return rec
            except Exception:  # noqa: BLE001
                return None
        return None

    def _put(self, sym: str, df: pd.DataFrame, fetched: datetime):
        df = df[~df.index.duplicated(keep="last")].sort_index()
        rec = {"df": df.astype("float32"), "fetched": fetched}
        self.mem[sym] = rec
        try:
            with open(self._path(sym), "wb") as fh:
                pickle.dump(rec, fh, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:  # noqa: BLE001
            pass

    # ---- status / update
    def plan(self, symbols, fresh_after: datetime) -> tuple[list, list]:
        full, stale = [], []
        for s in symbols:
            rec = self._get(s)
            if rec is None or rec["df"].empty:
                if rec is None or rec["fetched"] < fresh_after:
                    full.append(s)
            elif rec["fetched"] < fresh_after:
                stale.append(s)
        return full, stale

    def _fetch(self, syms: list[str], start: str, progress=None, done0=0, total=1) -> dict:
        got = {}
        for i in range(0, len(syms), self.batch):
            chunk = syms[i:i + self.batch]
            for attempt in range(3):
                try:
                    got.update(self.downloader([f"{s}.NS" for s in chunk], start))
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(self.pause * (attempt + 2))
            if progress:
                progress(done0 + min(i + self.batch, len(syms)), total)
            time.sleep(self.pause)
        missing = [s for s in syms if s not in got]
        if missing and len(missing) < len(syms):            # one slower retry for gaps
            time.sleep(self.pause * 3)
            for i in range(0, len(missing), 20):
                try:
                    got.update(self.downloader([f"{s}.NS" for s in missing[i:i + 20]], start))
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(self.pause)
        return got

    def update(self, symbols, fresh_after: datetime, progress=None) -> dict:
        """Bring `symbols` up to date. Returns counts for display."""
        with self.lock:
            full, stale = self.plan(symbols, fresh_after)
            now = datetime.now(IST)
            stats = {"full": len(full), "refreshed": len(stale), "re-downloaded": 0, "failed": []}
            total = len(full) + len(stale)
            if total == 0:
                return stats
            # incremental refresh, then verify the overlap (catches Yahoo re-adjustments)
            redo = []
            # symbols whose data stopped long ago (suspended / renamed) get a full refresh
            cutoff_old = pd.Timestamp(now.date()) - pd.Timedelta(days=40)
            old = [s for s in stale if self._get(s)["df"].index.max() < cutoff_old]
            stale = [s for s in stale if s not in old]
            full = full + old
            if stale:
                last = min(self._get(s)["df"].index.max() for s in stale)
                start = (last - timedelta(days=12)).strftime("%Y-%m-%d")
                got = self._fetch(stale, start, progress, 0, total)
                for s in stale:
                    old = self._get(s)["df"]
                    new = got.get(s)
                    if new is None or new.empty:
                        self._put(s, old, now)              # nothing new (holiday) - keep
                        continue
                    common = old.index.intersection(new.index)
                    if len(common):
                        ratio = (new.loc[common, "Close"] / old.loc[common, "Close"].astype("float64")).dropna()
                        if len(ratio) and (ratio - 1).abs().max() > 0.002:
                            redo.append(s)
                            continue
                    self._put(s, pd.concat([old.astype("float64"), new]), now)
            full_all = full + redo
            stats["re-downloaded"] = len(redo)
            if full_all:
                got = self._fetch(full_all, DATA_START, progress, len(stale), total + len(redo))
                for s in full_all:
                    df = got.get(s)
                    if df is None or df.empty:
                        stats["failed"].append(s)
                        if self._get(s) is None:
                            self._put(s, pd.DataFrame(columns=list(FIELDS), dtype="float64"), now)
                    else:
                        self._put(s, df, now)
            self.version += 1
            return stats

    def panel(self, symbols) -> dict[str, pd.DataFrame]:
        """{field: DataFrame(date x symbol)} for the symbols that have data."""
        frames = {s: self._get(s)["df"] for s in symbols
                  if self._get(s) is not None and not self._get(s)["df"].empty}
        if not frames:
            raise RuntimeError("No price data available for this universe.")
        out = {}
        for f in FIELDS:
            out[f] = pd.concat({s: df[f] for s, df in frames.items()}, axis=1).sort_index().astype("float64")
        return out

    def coverage(self, symbols) -> int:
        return sum(1 for s in symbols if self._get(s) is not None and not self._get(s)["df"].empty)


# ═══════════════════════════════════════════════════════════ benchmarks
def _ni_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get(f"{NI_BASE}/reports/historical-data", timeout=15)
    except requests.RequestException:
        pass
    return s


def _ni_rows(s: requests.Session, endpoints: list[str], api_name: str, start: date, end: date) -> list[dict]:
    cinfo = (f"{{'name':'{api_name}','startDate':'{start:%d-%b-%Y}',"
             f"'endDate':'{end:%d-%b-%Y}','indexName':'{api_name}'}}")
    last_err = None
    for ep in endpoints:
        try:
            r = s.post(ep, data=json.dumps({"cinfo": cinfo}), timeout=30,
                       headers={"Content-Type": "application/json; charset=utf-8",
                                "Accept": "application/json, text/javascript, */*; q=0.01",
                                "X-Requested-With": "XMLHttpRequest", "Origin": NI_BASE,
                                "Referer": f"{NI_BASE}/reports/historical-data"})
            r.raise_for_status()
            txt = r.text.strip()
            if not txt.startswith(("[", "{")):
                raise ValueError("non-JSON response (blocked or changed)")
            data = json.loads(txt)
            if isinstance(data, dict):
                data = data.get("d", [])
                data = json.loads(data) if isinstance(data, str) else data
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"niftyindices failed: {last_err}")


def _rows_to_series(rows: list[dict], kind: str) -> pd.Series:
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("no rows")
    dcol = next(c for c in df.columns if "date" in c.lower())
    if kind == "TRI":
        vcol = next(c for c in df.columns if "totalreturn" in c.lower().replace(" ", "")
                    or c.upper() in ("TRI", "TOTAL RETURNS INDEX"))
    else:
        vcol = next(c for c in df.columns if c.upper() == "CLOSE")
    dates = pd.to_datetime(df[dcol], format="mixed", dayfirst=True, errors="coerce")
    ser = pd.to_numeric(df[vcol].astype(str).str.replace(",", ""), errors="coerce")
    ser.index = dates
    ser = ser[ser.index.notna()].sort_index()
    return ser[~ser.index.duplicated(keep="last")].dropna()


def fetch_index_niftyindices(api_name: str, start: date, end: date, kind: str) -> pd.Series:
    eps = ([f"{NI_BASE}/BackPage/getTotalReturnIndexString", f"{NI_BASE}/Backpage.aspx/getTotalReturnIndexString"]
           if kind == "TRI" else
           [f"{NI_BASE}/BackPage/getHistoricaldatatabletoString", f"{NI_BASE}/Backpage.aspx/getHistoricaldatatabletoString"])
    s, rows, cur = _ni_session(), [], start
    while cur <= end:
        nxt = min(cur + timedelta(days=360), end)
        rows += _ni_rows(s, eps, api_name, cur, nxt)
        cur = nxt + timedelta(days=1)
        time.sleep(0.3)
    return _rows_to_series(rows, kind)


def fetch_index_yahoo(ticker: str, start: date, end: date) -> pd.Series:
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end + timedelta(days=1), auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise ValueError(f"Yahoo returned nothing for {ticker}")
    c = df["Close"]
    c = c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c
    c.index = pd.to_datetime(c.index).tz_localize(None)
    return c.dropna()


def fetch_benchmarks(start: date, end: date, kind: str = "TRI") -> tuple[pd.DataFrame, dict]:
    """Monthly (period index) closes of Nifty 50 and Nifty 500. kind: 'TRI' or 'PRICE'."""
    out, src = {}, {}
    for name, (api, yh) in BENCH.items():
        ser = None
        tries = [("TRI", "niftyindices TRI"), ("PRICE", "niftyindices price index")] if kind == "TRI" \
            else [("PRICE", "niftyindices price index")]
        errs = []
        for k, label in tries:
            try:
                ser = fetch_index_niftyindices(api, start, end, k)
                src[name] = label
                break
            except Exception as e:  # noqa: BLE001
                errs.append(f"{label}: {e}")
        if ser is None:
            try:
                ser = fetch_index_yahoo(yh, start, end)
                src[name] = f"Yahoo {yh} (price index)"
            except Exception as e:  # noqa: BLE001
                src[name] = "unavailable - " + "; ".join(errs + [f"Yahoo: {e}"])
                continue
        m = ser.groupby(ser.index.to_period("M")).last()
        out[name] = m
    return (pd.concat(out, axis=1) if out else pd.DataFrame()), src
