"""
Weekly reversal from the 10-week EMA, with a fixed target and stop-loss.
Pure pandas/numpy. Weekly candles are built from daily prices (weeks end on Friday).

SCAN LIST: only stocks on the monthly list are scanned. A stock joins the list at a month-end
            where it gives a monthly Setup A or B signal (both setups, monthly settings) and leaves
            at a month-end where the monthly exit fires (close < 12M EMA or Close/EMA > exit_ext).
            A week uses the list as of the last month-end before the week started.
SIGNAL (the reversal candle, week w, decided at its Friday close)
  W1. 26-week range : highest weekly high / lowest weekly low of the w_range_weeks weeks before w
                      >= w_range, and the low came before the high (first occurrence of each)
  W2. EMA rising    : E[w-1] / E[w-1-w_slope_window] - 1 >= w_min_slope
  W3. Near the EMA  : in week w or the (w_near_weeks - 1) weeks before it, a weekly low within
                      w_low_lo..w_low_hi of that week's 10W EMA
  W4. Above the EMA : Close[w] > E[w]
  W5. Reversal      : Close[w] / Close[w-1] - 1 > w_min_ret and closing range > w_min_cr
  COMMON: history >= min_history months; 26-week median daily turnover >= min_turnover_cr
ENTRY
  closing range > w_cr_close  -> buy at week w's close
  otherwise                   -> in week w+1, buy the first break above week w's high (fill at the
                                 high, or at the open if the day opens above it); expires after w+1
EXIT  (checked daily, from the exact entry price)
  Stop-loss = entry x (1 - w_stop); Target = entry x (1 + w_target).
  Entry day: only the close is compared (the order of the intraday low/high vs the fill is
  unknown), except when the fill was at the open. Later days: an open beyond a level exits
  at the open; else low <= stop exits at the stop, high >= target at the target (stop first
  if both). A weekly close below the 10W EMA exits at the next open (or that close).
PORTFOLIO: daily event simulation with `slots` equal slots, exits before entries, same-day
           entries ranked by close / 26-week low; cash earns cash_yield.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import engine as E

W_DEFAULTS = dict(
    w_ema=10, w_slope_window=6, w_min_slope=0.02, w_range_weeks=26, w_range=1.30,
    w_low_lo=-0.05, w_low_hi=0.03, w_near_weeks=3, w_min_ret=0.04, w_min_cr=0.75,
    w_cr_close=0.90, w_target=0.20, w_stop=0.075,
)
FREQ = "W-FRI"
ENTRY_TYPES = {1: "Buy above signal high", 2: "Buy at signal close"}
STATUS_ORDER = {"TRIGGERED": 0, "BUY STOP": 1, "WATCH": 2, "Rejected": 3}
EMA_EXIT = "Weekly close < 10W EMA"


def history_weeks(P: dict) -> int:
    return int(round(P["min_history"] * 52 / 12))


def week_label(period) -> str:
    """A weekly period shown as its Friday date."""
    return str(period.end_time.date())


# ════════════════════════════════════════════════════════════ monthly scan list
def monthly_list(daily: dict, P: dict, last_final_day=None) -> dict:
    """Which stocks are on the monthly list after each completed month-end, and why."""
    mp = {key: P[key] for key in E.DEFAULTS}
    mp["setups"] = "A+B"
    m = E.to_monthly(daily, last_final_day)
    ind = E.compute_indicators(m, mp)
    sig, tier, _ = E.compute_signals(ind, mp)
    months = ind["C"].index
    n = len(months) - 1 if m["partial"] else len(months)        # completed months only
    C, Em = ind["C"].values, ind["E"].values
    with np.errstate(invalid="ignore", divide="ignore"):
        exit_ = (C < Em) | ((C / Em > mp["exit_ext"]) if mp["exit_ext"] else False)
    sig_v, tier_v = sig.values, tier.values
    on = np.zeros((n, C.shape[1]), bool)
    since = np.full((n, C.shape[1]), -1)
    cur, src = np.zeros(C.shape[1], bool), np.full(C.shape[1], -1)
    for t in range(n):
        cur = sig_v[t] | (cur & ~exit_[t])
        src = np.where(sig_v[t], t, np.where(cur, src, -1))
        on[t], since[t] = cur, src
    return {"months": months[:n], "on": on, "since": since, "tier": tier_v[:n], "syms": list(ind["C"].columns)}


def week_list_month(week, months) -> int:
    """Index of the last completed month-end before the week opened (-1 if none)."""
    month = (week.start_time + pd.Timedelta(days=2)).to_period("M") - 1     # Monday's month - 1
    return int(min(months.searchsorted(month, "right") - 1, len(months) - 1))


def list_label(ml: dict, mi: int, j: int) -> str:
    t = ml["since"][mi, j] if mi >= 0 else -1
    if t < 0:
        return ""
    month = ml["months"][t].strftime("%b %Y")
    return f"{E.TIER[ml['tier'][t, j]]} · {month}"


# ════════════════════════════════════════════════════════════ weekly data
def to_weekly(daily: dict, last_final_day: pd.Timestamp | None = None) -> dict:
    """Weekly OHLC + turnover from cleaned daily prices. w['partial']: last week still running."""
    O, H, L, C, V = (daily[f] for f in E.FIELDS)
    if last_final_day is not None:
        keep = C.index <= last_final_day
        O, H, L, C, V = O[keep], H[keep], L[keep], C[keep], V[keep]
    g = C.index.to_period(FREQ)
    w = {"Open": O.groupby(g).first(), "High": H.groupby(g).max(),
         "Low": L.groupby(g).min(), "Close": C.groupby(g).last()}
    w["AvgVol"] = V.where(V > 0).groupby(g).mean()
    med = (C * V).groupby(g).median()                               # median daily turnover in week
    w["Turnover"] = med.rolling(26, min_periods=13).median()        # ~6-month median
    has = C.notna().groupby(g).any()
    for k in list(w):
        w[k] = w[k].where(has)
    last_day = C.index.max()
    w["partial"] = bool((last_day + pd.offsets.BDay(1)).to_period(FREQ) == last_day.to_period(FREQ))
    w["last_day"] = last_day
    w["daily"] = {"Open": O, "High": H, "Low": L, "Close": C}
    return w


def _low_before_high(H: pd.DataFrame, L: pd.DataFrame, n: int) -> pd.DataFrame:
    """True where, in the n weeks before each row, the lowest low came before the highest high."""
    h = H.shift(1).values
    l_ = L.shift(1).values
    out = np.zeros(h.shape, bool)
    if len(h) >= n:
        hw = sliding_window_view(np.where(np.isnan(h), -np.inf, h), n, axis=0)   # (rows, cols, n)
        lw = sliding_window_view(np.where(np.isnan(l_), np.inf, l_), n, axis=0)
        out[n - 1:] = np.argmin(lw, axis=2) < np.argmax(hw, axis=2)
    return pd.DataFrame(out, H.index, H.columns)


def compute_indicators(w: dict, P: dict) -> dict:
    """Weekly indicators. One empty week is appended so 'ready for next week' can be read."""
    weeks = w["Close"].index
    full = weeks.append(pd.PeriodIndex([weeks[-1] + 1], freq=FREQ))
    C, H, L, O, T = (w[k].reindex(full) for k in ("Close", "High", "Low", "Open", "Turnover"))
    Ew = C.ewm(span=P["w_ema"], adjust=False).mean().where(C.notna())
    sw, rw, nw = P["w_slope_window"], int(P["w_range_weeks"]), int(P["w_near_weeks"])
    hmax = H.shift(1).rolling(rw, min_periods=rw).max()
    lmin = L.shift(1).rolling(rw, min_periods=rw).min()
    lve = L / Ew - 1                                               # each week's low vs its own EMA
    near = (lve >= P["w_low_lo"]) & (lve <= P["w_low_hi"])
    near_prev = pd.DataFrame(False, C.index, C.columns)            # weeks w-1 .. w-(nw-1)
    for k in range(1, nw):
        near_prev = near_prev | near.shift(k, fill_value=False)
    return {"weeks": weeks, "all_weeks": full, "C": C, "H": H, "L": L, "O": O, "E": Ew,
            "cr": ((C - L) / (H - L)).where(H > L), "ret": C / C.shift(1) - 1,
            "slope": Ew.shift(1) / Ew.shift(1 + sw) - 1,           # before the signal week
            "hist": C.notna().cumsum().shift(1), "turn": T.shift(1), "prev_close": C.shift(1),
            "hmax": hmax, "lmin": lmin, "range": hmax / lmin, "low_first": _low_before_high(H, L, rw),
            "low_vs_ema": lve, "near": near, "near_prev": near_prev, "near_any": near | near_prev,
            "close_vs_ema": C / Ew - 1, "score": C / lmin, "score_prev": C.shift(1) / lmin}


def signal_rules(ind: dict, P: dict) -> dict:
    """Boolean frames for the signal rules, cumulative (shared by signals, screener and funnel)."""
    r = {"listed": ind["listed"]}
    r["eligible"] = r["listed"] & (ind["hist"] >= history_weeks(P)) & (ind["turn"] >= P["min_turnover_cr"] * 1e7) \
        & ind["prev_close"].notna()
    r["range"] = r["eligible"] & (ind["range"] >= P["w_range"]) & ind["low_first"]
    r["rising"] = r["range"] & (ind["slope"] >= P["w_min_slope"])
    r["near"] = r["rising"] & ind["near_any"]
    r["above"] = r["near"] & (ind["C"] > ind["E"])
    r["ret"] = r["above"] & (ind["ret"] > P["w_min_ret"])
    r["signal"] = r["ret"] & (ind["cr"] > P["w_min_cr"])
    r["watch"] = r["rising"] & ind["near_prev"]                    # read on next week's row
    return r


# ════════════════════════════════════════════════════════════ daily panel
def daily_panel(w: dict, ind: dict) -> dict:
    d = w["daily"]
    days = d["Close"].index
    weeks = ind["all_weeks"]
    day_w = weeks.get_indexer(days.to_period(FREQ))
    idx = np.arange(len(weeks))
    first = np.searchsorted(day_w, idx, "left")
    last = np.searchsorted(day_w, idx, "right") - 1
    arr = {f: d[f].values.astype(float) for f in ("Open", "High", "Low", "Close")}
    valid = ~np.isnan(arr["Close"])
    n_sym = valid.shape[1]
    lastv = np.full((len(weeks), n_sym), -1)
    for wi in range(len(weeks)):
        a, b = first[wi], last[wi]
        if b < a:
            continue
        blk = valid[a:b + 1]
        lastv[wi] = np.where(blk.any(0), b - np.argmax(blk[::-1], axis=0), -1)
    # day on which a weekly close below the 10W EMA is known (the stock's last close of that week)
    below = (ind["C"] < ind["E"]).values
    we_below = np.zeros_like(valid)
    rows, cols = np.nonzero(below & (lastv >= 0))
    we_below[lastv[rows, cols], cols] = True
    return {"days": days, "weeks": weeks, "day_w": day_w, "first": first, "last": last,
            "O": arr["Open"], "H": arr["High"], "L": arr["Low"], "C": arr["Close"],
            "lastv": lastv, "we_below": we_below, "syms": list(d["Close"].columns)}


def _first_open(dp, j, a, b):
    """First day in a..b (inclusive) with an opening price for column j, else None."""
    o = dp["O"][a:b + 1, j]
    ok = np.nonzero(~np.isnan(o))[0]
    return a + ok[0] if len(ok) else None


def _fill(open_px, stop):
    """Buy-stop fill: the stop price, or the open when the day opens above it."""
    return open_px if open_px >= stop else stop


def find_entries(ind: dict, P: dict, dp: dict):
    """Every entry the rules produce (independent of any portfolio), plus each signal's outcome."""
    r = signal_rules(ind, P)
    Cw, Hw, CRw = (ind[k].values for k in ("C", "H", "cr"))
    O, H = dp["O"], dp["H"]
    first, last, lastv = dp["first"], dp["last"], dp["lastv"]
    n_days, n_real = len(dp["days"]), len(ind["weeks"])
    cands, setups = [], {}
    for wi, j in zip(*np.nonzero(r["signal"].values[:n_real])):
        base = {"j": j, "week": wi, "slope": ind["slope"].iat[wi, j], "score": ind["score"].iat[wi, j],
                "range": ind["range"].iat[wi, j], "ret": ind["ret"].iat[wi, j], "cr": CRw[wi, j],
                "low_vs_ema": ind["low_vs_ema"].iat[wi, j], "close": Cw[wi, j], "week_high": Hw[wi, j]}
        entry = None
        if CRw[wi, j] > P["w_cr_close"]:
            status = "TRIGGERED"
            if lastv[wi, j] >= 0:
                entry = (lastv[wi, j], Cw[wi, j], 2, False, True)
        else:
            status, stop = "BUY STOP", Hw[wi, j]
            if wi + 1 < n_real:
                for d in range(first[wi + 1], last[wi + 1] + 1):
                    if H[d, j] > stop:
                        entry = (d, _fill(O[d, j], stop), 1, O[d, j] >= stop, False)
                        break
        out = {**base, "status": status, "entry": None}
        if entry and entry[0] < n_days and not np.isnan(entry[1]):
            d, px, typ, at_open, at_close = entry
            c = {**base, "day": int(d), "price": float(px), "type": typ,
                 "at_open": bool(at_open), "at_close": bool(at_close)}
            cands.append(c)
            out["entry"] = c
        setups[(wi, j)] = out
    cands.sort(key=lambda c: (c["day"], c["j"], c["week"]))
    unique, seen = [], set()
    for c in cands:
        if (c["j"], c["day"]) not in seen:
            seen.add((c["j"], c["day"]))
            unique.append(c)
    return unique, setups, r


def prepare(daily: dict, P: dict, last_final_day=None) -> dict:
    w = to_weekly(daily, last_final_day)
    ind = compute_indicators(w, P)
    ind["partial"] = w["partial"]
    ml = monthly_list(daily, P, last_final_day)
    cols = list(ind["C"].columns)
    pos = [ml["syms"].index(s) for s in cols]
    week_month = np.array([week_list_month(wk, ml["months"]) for wk in ind["all_weeks"]])
    listed = np.zeros((len(week_month), len(cols)), bool)
    ok = week_month >= 0
    listed[ok] = ml["on"][week_month[ok]][:, pos]
    ind["listed"] = pd.DataFrame(listed, ind["all_weeks"], cols)
    ind["week_month"], ind["mlist"] = week_month, {**ml, "pos": pos}
    dp = daily_panel(w, ind)
    cands, setups, rules = find_entries(ind, P, dp)
    return {"w": w, "ind": ind, "dp": dp, "cands": cands, "setups": setups, "rules": rules}


# ═══════════════════════════════════════════════════════════ positions
def trade_exit(dp: dict, j: int, c: dict, P: dict, last_idx: int):
    """(exit day index or None if still open, exit price, reason) for entry candidate c."""
    O, H, L, C, wb = dp["O"], dp["H"], dp["L"], dp["C"], dp["we_below"]
    e, px = c["day"], c["price"]
    sl, tg = px * (1 - P["w_stop"]), px * (1 + P["w_target"])
    close_exec = P["execution"] == "close"
    if not c["at_close"]:
        lo_, hi_ = (L[e, j], H[e, j]) if c["at_open"] else (C[e, j], C[e, j])
        if lo_ <= sl:
            return e, sl, "Stop-loss"
        if hi_ >= tg:
            return e, tg, "Target"
    if close_exec and wb[e, j]:
        return e, C[e, j], EMA_EXIT
    with np.errstate(invalid="ignore"):
        s = slice(e + 1, last_idx + 1)
        o, h, l = O[s, j], H[s, j], L[s, j]
        hit = (o <= sl) | (o >= tg) | (l <= sl) | (h >= tg)
        if close_exec:
            hit = hit | wb[s, j]
    u = e + 1 + int(np.argmax(hit)) if hit.any() else None
    pending = False
    if not close_exec:
        ends = np.nonzero(wb[e:last_idx + 1, j])[0]
        if len(ends):
            d0 = e + ends[0]
            x = _first_open(dp, j, d0 + 1, last_idx) if d0 < last_idx else None
            if x is not None and (u is None or x <= u):
                return x, O[x, j], EMA_EXIT
            pending = x is None and u is None
    if u is not None:
        if O[u, j] <= sl:
            return u, O[u, j], "Stop-loss (gap)"
        if O[u, j] >= tg:
            return u, O[u, j], "Target (gap)"
        if L[u, j] <= sl:
            return u, sl, "Stop-loss"
        if H[u, j] >= tg:
            return u, tg, "Target"
        return u, C[u, j], EMA_EXIT
    closes = np.nonzero(~np.isnan(C[e:last_idx + 1, j]))[0]
    lv = e + closes[-1]
    if lv < last_idx - 5 and lv > e:
        return lv, C[lv, j], "Data ended"
    return None, C[lv, j], EMA_EXIT + " (exit pending)" if pending else "Open"


def monthly_label(ind: dict, wi: int, j: int) -> str:
    ml = ind["mlist"]
    return list_label(ml, ind["week_month"][wi], ml["pos"][j])


def _info(c, dp, ind, P, names, inds):
    s = dp["syms"][c["j"]]
    return {"Symbol": s, "Company": names.get(s, ""), "Industry": inds.get(s, ""),
            "Monthly signal": monthly_label(ind, c["week"], c["j"]),
            "Signal week": week_label(ind["all_weeks"][c["week"]]),
            "Entry type": ENTRY_TYPES[c["type"]], "Entry date": str(dp["days"][c["day"]].date()),
            "Entry price": c["price"], "Stop-loss": c["price"] * (1 - P["w_stop"]),
            "Target": c["price"] * (1 + P["w_target"]), "Week return": c["ret"],
            "Closing range": c["cr"], "26W high/low": c["range"], "Signal low vs EMA": c["low_vs_ema"],
            "EMA slope": c["slope"]}


def _weeks_held(dp, e, x):
    return round((dp["days"][x] - dp["days"][e]).days / 7, 1)


def simulate(W: dict, P: dict, start_idx: int, end_idx: int, names=None, inds=None):
    names, inds = names or {}, inds or {}
    dp, ind = W["dp"], W["ind"]
    syms, cost = dp["syms"], P["cost"]
    by_day = defaultdict(list)
    for c in W["cands"]:
        if start_idx <= c["day"] <= end_idx:
            by_day[c["day"]].append(c)
    C_ff = pd.DataFrame(dp["C"]).ffill().values
    cash = float(P["capital"])
    d_cash = (1 + P["cash_yield"]) ** (1 / 252)
    holdings, trades, values, snap = {}, [], [], []
    week0 = dp["day_w"][start_idx]
    values.append({"Week": ind["all_weeks"][week0 - 1], "Value": cash, "Cash": cash, "Holdings": 0})
    new_in, new_out = [], []

    def close_out(s, i):
        nonlocal cash
        h = holdings.pop(s)
        proceeds = h["qty"] * h["exit_px"] * (1 - cost)
        cash += proceeds
        new_out.append(s)
        trades.append({**h["info"], "Exit date": str(dp["days"][i].date()), "Exit price": h["exit_px"],
                       "Exit reason": h["reason"], "Weeks held": _weeks_held(dp, h["day"], i),
                       "Invested": h["invested"], "Return": proceeds / h["invested"] - 1,
                       "P&L": proceeds - h["invested"], "Status": "Closed"})

    for i in range(start_idx, end_idx + 1):
        if i > start_idx:
            cash *= d_cash
        for s in [s for s, h in holdings.items() if h["exit_idx"] == i]:
            close_out(s, i)
        today = [c for c in by_day.get(i, []) if syms[c["j"]] not in holdings]
        free = P["slots"] - len(holdings)
        if today and free > 0:
            today.sort(key=lambda c: -c["score"])
            today = today[:free]
            prev = max(i - 1, 0)
            equity = cash + sum(h["qty"] * C_ff[prev, h["j"]] for h in holdings.values())
            target = equity / P["slots"]
            alloc = min(target, cash / len(today))
            if alloc >= P["min_alloc_frac"] * target:
                for c in today:
                    j = c["j"]
                    x_idx, x_px, reason = trade_exit(dp, j, c, P, end_idx)
                    holdings[syms[j]] = {"j": j, "qty": alloc * (1 - cost) / c["price"], "invested": alloc,
                                         "day": i, "exit_idx": x_idx, "exit_px": x_px, "reason": reason,
                                         "info": _info(c, dp, ind, P, names, inds)}
                    cash -= alloc
                    new_in.append(syms[j])
                for s in [s for s, h in holdings.items() if h["exit_idx"] == i]:
                    close_out(s, i)
        if dp["last"][dp["day_w"][i]] == i or i == end_idx:
            inv = sum(h["qty"] * C_ff[i, h["j"]] for h in holdings.values())
            wk = ind["all_weeks"][dp["day_w"][i]]
            values.append({"Week": wk, "Value": cash + inv, "Cash": cash, "Holdings": len(holdings)})
            snap.append({"Week": week_label(wk), "Slots used": len(holdings),
                         "New entries": ", ".join(new_in), "Exits": ", ".join(new_out)})
            new_in, new_out = [], []

    for s, h in holdings.items():
        px = C_ff[end_idx, h["j"]]
        trades.append({**h["info"], "Exit date": "open", "Exit price": px,
                       "Exit reason": h["reason"] if "pending" in h["reason"] else "still held",
                       "Weeks held": _weeks_held(dp, h["day"], end_idx), "Invested": h["invested"],
                       "Return": h["qty"] * px / h["invested"] - 1,
                       "P&L": h["qty"] * px - h["invested"], "Status": "Open"})
    vals = pd.DataFrame(values).drop_duplicates("Week", keep="last").set_index("Week")
    return vals, pd.DataFrame(trades), pd.DataFrame(snap)


def all_signal_trades(W: dict, P: dict, start_idx: int, end_idx: int, names=None) -> pd.DataFrame:
    """Every entry as an independent equal trade (no slot limit, one position per stock)."""
    names, dp, ind = names or {}, W["dp"], W["ind"]
    rows, busy_until = [], {}
    for c in W["cands"]:
        if not start_idx <= c["day"] <= end_idx:
            continue
        j = c["j"]
        if c["day"] < busy_until.get(j, -1) or busy_until.get(j) == np.inf:
            continue
        x_idx, x_px, reason = trade_exit(dp, j, c, P, end_idx)
        busy_until[j] = np.inf if x_idx is None else x_idx
        info = _info(c, dp, ind, P, names, {})
        info.pop("Industry")
        rows.append({**info, "Exit date": str(dp["days"][x_idx].date()) if x_idx is not None else "open",
                     "Exit price": x_px, "Exit reason": reason,
                     "Weeks held": _weeks_held(dp, c["day"], x_idx if x_idx is not None else end_idx),
                     "Return": x_px / c["price"] * (1 - P["cost"]) ** 2 - 1,
                     "Status": "Closed" if x_idx is not None else "Open"})
    return pd.DataFrame(rows)


def funnel(W: dict) -> pd.DataFrame:
    """Weekly counts for each cumulative signal rule and the entries by type."""
    r, ind, dp = W["rules"], W["ind"], W["dp"]
    weeks = ind["weeks"]
    n = len(weeks)
    by_type = {t: np.zeros(n, int) for t in ENTRY_TYPES}
    for c in W["cands"]:
        by_type[c["type"]][dp["day_w"][c["day"]]] += 1
    count = lambda key: r[key].sum(axis=1).values[:n]
    f = pd.DataFrame({
        "On monthly list": count("listed"), "Eligible": count("eligible"),
        "26W high/low (low first)": count("range"), "+ EMA rising": count("rising"),
        "+ near 10W EMA": count("near"), "+ close above EMA": count("above"),
        "+ week return": count("ret"), "Reversal signals": count("signal"),
        **{ENTRY_TYPES[t]: v for t, v in by_type.items()},
    }, index=weeks)
    f["Entries"] = sum(f[ENTRY_TYPES[t]] for t in ENTRY_TYPES)
    return f


def ew_universe(ind: dict, start_w: int, end_w: int) -> pd.Series:
    C = ind["C"]
    r = (C / C.shift(1) - 1).iloc[start_w:end_w + 1]
    lvl = (1 + r.mean(axis=1).fillna(0)).cumprod()
    return pd.concat([pd.Series([1.0], index=[ind["all_weeks"][start_w - 1]]), lvl])


def trade_stats(tr: pd.DataFrame) -> dict:
    if tr is None or tr.empty:
        return {}
    st = E.trade_stats(tr.rename(columns={"Weeks held": "Months held"}))
    st["Avg weeks held"] = st.pop("Avg months held")
    exits = tr.loc[tr["Status"] == "Closed", "Exit reason"].str.replace(r" \(gap\)", "", regex=True)
    for label in ("Target", "Stop-loss", EMA_EXIT):
        st[f"Exits: {label}"] = int((exits == label).sum())
    return st


def run_backtest(W: dict, P: dict, start_idx: int, end_idx: int, names=None, inds=None) -> dict:
    vals, trades, snap = simulate(W, P, start_idx, end_idx, names, inds)
    allsig = all_signal_trades(W, P, start_idx, end_idx, names)
    w0, w1 = W["dp"]["day_w"][start_idx], W["dp"]["day_w"][end_idx]
    return {"vals": vals, "trades": trades, "snap": snap, "allsig": allsig,
            "funnel": funnel(W).iloc[w0:w1 + 1], "ew": ew_universe(W["ind"], w0, w1)}


# ═══════════════════════════════════════════════════════════ screener
def screen_week(W: dict, P: dict, wi: int, names: dict, inds: dict) -> pd.DataFrame:
    """Entries in week wi, buy-stops for next week, the watch list and near-misses."""
    ind, dp = W["ind"], W["dp"]
    syms = dp["syms"]
    r = W["rules"]
    rows, done = [], set()
    stop_pct, tgt_pct = P["w_stop"], P["w_target"]

    def common(j, wk, score):
        turn = ind["turn"].iat[wk, j]
        return {"Symbol": syms[j], "Company": names.get(syms[j], ""), "Industry": inds.get(syms[j], ""),
                "Monthly signal": monthly_label(ind, wk, j), "Close": ind["C"].iat[wi, j],
                "10W EMA": ind["E"].iat[wi, j], "Close vs EMA": ind["close_vs_ema"].iat[wi, j],
                "Low vs EMA": ind["low_vs_ema"].iat[wi, j], "Week return": ind["ret"].iat[wi, j],
                "Closing range": ind["cr"].iat[wi, j], "26W high/low": ind["range"].iat[wk, j],
                "EMA slope": ind["slope"].iat[wk, j],
                "Turnover (Rs cr)": turn / 1e7 if pd.notna(turn) else np.nan, "_score": score}

    def levels(trigger):
        return {"Trigger": trigger, "Stop-loss": trigger * (1 - stop_pct), "Target": trigger * (1 + tgt_pct)}

    blank = {"Trigger": np.nan, "Stop-loss": np.nan, "Target": np.nan, "Entry date": None}
    # 1. entries during week wi
    for c in W["cands"]:
        j = c["j"]
        if dp["day_w"][c["day"]] != wi or j in done:     # one row per stock (already bought)
            continue
        done.add(j)
        rows.append({**common(j, wi, c["score"]), "Week return": c["ret"], "Closing range": c["cr"],
                     "Status": "TRIGGERED", "Action": ENTRY_TYPES[c["type"]],
                     **levels(c["price"]), "Entry date": str(dp["days"][c["day"]].date()),
                     "Reason": f"signal week {week_label(ind['all_weeks'][c['week']])}"})
    # 2. reversal candles this week with a closing range up to w_cr_close: buy above the high next week
    for (sw, j), s in W["setups"].items():
        if sw != wi or j in done or s["status"] != "BUY STOP":
            continue
        done.add(j)
        rows.append({**common(j, wi, s["score"]), "Status": "BUY STOP",
                     "Action": f"Buy above this week's high {s['week_high']:,.2f} next week",
                     **levels(s["week_high"]), "Entry date": None,
                     "Reason": f"reversal candle: return {s['ret']:.1%}, closing range {s['cr']:.2f}"})
    # 3. watch list: near the EMA and every trend rule passes for next week; needs a reversal candle
    nxt = wi + 1
    for j in np.nonzero(r["watch"].values[nxt])[0]:
        if j in done:
            continue
        done.add(j)
        rows.append({**common(j, nxt, ind["score_prev"].iat[nxt, j]), "Status": "WATCH",
                     "Action": f"Needs a reversal week: close above the 10W EMA, return > {P['w_min_ret']:.0%}, "
                               f"closing range > {P['w_min_cr']:.2f}", **blank,
                     "Reason": "low near the 10W EMA in the last 2 weeks" if P["w_near_weeks"] == 3
                               else "low near the 10W EMA recently"})
    # 4. near-misses: on the list and near the EMA this week, but no signal
    near = ind["listed"].values[wi] & ind["near_any"].values[wi]
    for j in np.nonzero(near)[0]:
        if j in done:
            continue
        why = []
        hist, turn = ind["hist"].iat[wi, j], ind["turn"].iat[wi, j]
        if not hist >= history_weeks(P):
            why.append(f"history {0 if pd.isna(hist) else int(hist)}w < {history_weeks(P)}w")
        if not turn >= P["min_turnover_cr"] * 1e7:
            why.append(f"turnover Rs {0 if pd.isna(turn) else turn / 1e7:.1f} cr < {P['min_turnover_cr']:g} cr")
        rng = ind["range"].iat[wi, j]
        if not rng >= P["w_range"]:
            why.append(f"26W high/low {0 if pd.isna(rng) else rng:.2f} < {P['w_range']:.2f}")
        elif not ind["low_first"].iat[wi, j]:
            why.append("26W low came after the high")
        sl = ind["slope"].iat[wi, j]
        if not sl >= P["w_min_slope"]:
            why.append(f"EMA slope {0 if pd.isna(sl) else sl:+.1%} < {P['w_min_slope']:+.0%}")
        if not ind["C"].iat[wi, j] > ind["E"].iat[wi, j]:
            why.append("closed below the 10W EMA")
        ret, cr = ind["ret"].iat[wi, j], ind["cr"].iat[wi, j]
        if not ret > P["w_min_ret"]:
            why.append(f"week return {0 if pd.isna(ret) else ret:.1%} <= {P['w_min_ret']:.0%}")
        if not cr > P["w_min_cr"]:
            why.append(f"closing range {0 if pd.isna(cr) else cr:.2f} <= {P['w_min_cr']:.2f}")
        if not why:
            why.append("no entry")
        rows.append({**common(j, wi, ind["score"].iat[wi, j]), "Status": "Rejected", "Action": None, **blank,
                     "Reason": "; ".join(why)})
    cols = ["Symbol", "Company", "Industry", "Monthly signal", "Status", "Action", "Trigger", "Stop-loss", "Target",
            "Entry date", "Close", "10W EMA", "Close vs EMA", "Low vs EMA", "Week return", "Closing range",
            "26W high/low", "EMA slope", "Turnover (Rs cr)", "Reason"]
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["Rank"] + cols)
    df["_o"] = df["Status"].map(STATUS_ORDER)
    df = df.sort_values(["_o", "_score"], ascending=[True, False])
    df.insert(0, "Rank", np.where(df["Status"] != "Rejected", np.arange(1, len(df) + 1), np.nan))
    return df[["Rank"] + cols]
