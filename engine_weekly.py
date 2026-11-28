"""
Weekly Setup B - pullback to the 10-week EMA, with a fixed target and stop-loss.
Pure pandas/numpy. Weekly candles are built from daily prices (weeks end on Friday).

SCAN LIST: only stocks on the monthly list are scanned. A stock joins the list at a month-end
            where it gives a monthly Setup A or B signal (both setups, monthly settings) and leaves
            at a month-end where the monthly exit fires (close < 12M EMA or Close/EMA > exit_ext).
            A week uses the list as of the last month-end before the week started.
SETUP (known when the touch week w opens - every value below uses weeks up to w-1)
  W1. Recent high : the highest close of the 52 weeks before w was made in week w-k-1,
                    1 <= k <= w_kmax (k = pullback weeks before the touch week)
  W2. Above EMA   : every weekly close from the high week to w-1 is >= the 10W EMA
  W3. EMA rising  : E[w-1] / E[w-1-w_slope_window] - 1 >= w_min_slope
  W4. Volume dry-up (switch, off by default): average daily volume of the k pullback weeks
                    <= w_vol_dry x average daily volume of the 10 weeks ending at the high
  COMMON: close / 52-week low - 1 >= min_dist_low; history >= min_history months;
          26-week median daily turnover >= min_turnover_cr
TOUCH ZONE: the line is E[w-1], the 10W EMA as it stood when week w opened (the week's own
            EMA is only known at Friday's close). Touch = a daily low <= line x (1 + w_low_hi).
            If the week's low falls below line x (1 + w_low_lo) the setup is cancelled.
ENTRY (first that happens; the setup expires after week w+1)
  1. Prev-week high break : after the touch, the first day whose high > week w-1's high.
                            Fill at that high, or at the open if the day opens above it.
                            On the touch day itself only if the day opened at/below that high.
  2. Strong close         : week w closes >= E[w] with closing range >= b_min_cr ->
                            buy at the next trading day's open (or week w's close).
  3. Touch-week high break: week w closes >= E[w] but the closing range is lower ->
                            in week w+1, buy the first break above week w's high.
EXIT  (checked daily, from the exact entry price)
  Stop-loss = entry x (1 - w_stop); Target = entry x (1 + w_target).
  Entry day: only the close is compared (the order of the intraday low/high vs the fill is
  unknown), except when the fill was at the open. Later days: an open beyond a level exits
  at the open; else low <= stop exits at the stop, high >= target at the target (stop first
  if both). A weekly close below the 10W EMA exits at the next open (or that close).
PORTFOLIO: daily event simulation with `slots` equal slots, exits before entries, same-day
           entries ranked by close / 52-week low; cash earns cash_yield.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

import engine as E

W_DEFAULTS = dict(
    w_ema=10, w_slope_window=6, w_min_slope=0.02, w_kmax=4,
    w_low_lo=-0.05, w_low_hi=0.03, w_target=0.20, w_stop=0.075,
    w_use_vol_dry=False, w_vol_dry=0.80,
)
FREQ = "W-FRI"
W_KMAX_LIMIT = 8                      # pullback lengths computed up to this; UI allows 1..8
ENTRY_TYPES = {1: "Prev-week high break", 2: "Strong close", 3: "Touch-week high break"}
STATUS_ORDER = {"TRIGGERED": 0, "BUY NEXT OPEN": 1, "BUY STOP": 2, "WATCH": 3, "Rejected": 4}
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


def compute_indicators(w: dict, P: dict) -> dict:
    """Weekly indicators. One empty week is appended so 'armed for next week' can be read."""
    weeks = w["Close"].index
    full = weeks.append(pd.PeriodIndex([weeks[-1] + 1], freq=FREQ))
    C, H, L, O, AV, T = (w[k].reindex(full) for k in ("Close", "High", "Low", "Open", "AvgVol", "Turnover"))
    Ew = C.ewm(span=P["w_ema"], adjust=False).mean().where(C.notna())
    sw = P["w_slope_window"]
    low52 = L.shift(1).rolling(52, min_periods=52).min()
    ind = {"weeks": weeks, "all_weeks": full, "C": C, "H": H, "L": L, "O": O, "E": Ew,
           "cr": ((C - L) / (H - L)).where(H > L),
           "slope": Ew.shift(1) / Ew.shift(1 + sw) - 1,                # before the touch week
           "low52": low52, "score": C.shift(1) / low52,
           "hist": C.notna().cumsum().shift(1), "turn": T.shift(1),
           "prev_close": C.shift(1), "prev_ema": Ew.shift(1), "prev_high": H.shift(1)}
    ind["low_vs_ema"] = L / ind["prev_ema"] - 1
    ind["close_vs_ema"] = C / Ew - 1

    # ---- structure: where was the 52-week closing high (weeks w-52..w-1)?
    hi52 = C.shift(1).rolling(52, min_periods=52).max()
    nan = pd.DataFrame(np.nan, C.index, C.columns)
    k_fr, pull_fr, ref_fr = nan.copy(), nan.copy(), nan.copy()
    above = pd.DataFrame(False, C.index, C.columns)
    k_fr = k_fr.mask(C.shift(1) >= hi52, 0)             # last week IS the high -> no pullback
    run = C.shift(1) >= Ew.shift(1)
    for k in range(1, W_KMAX_LIMIT + 1):
        run = run & (C.shift(k + 1) >= Ew.shift(k + 1))
        hit = k_fr.isna() & hi52.notna() & (C.shift(k + 1) >= hi52)
        k_fr = k_fr.mask(hit, k)
        above = above | (hit & run)
        pull_fr = pull_fr.mask(hit, AV.shift(1).rolling(k, min_periods=1).mean())
        ref_fr = ref_fr.mask(hit, AV.shift(k + 1).rolling(10, min_periods=6).mean())
    ind["k"], ind["above"], ind["vol_ratio"] = k_fr, above, pull_fr / ref_fr
    return ind


def setup_rules(ind: dict, P: dict) -> dict:
    """Boolean frames for the pre-week setup rules, cumulative (shared by signals and funnel)."""
    r = {"listed": ind["listed"]}
    r["eligible"] = r["listed"] & (ind["hist"] >= history_weeks(P)) & (ind["turn"] >= P["min_turnover_cr"] * 1e7) \
        & ind["prev_close"].notna() & ind["prev_ema"].notna()
    k = ind["k"]
    r["struct"] = r["eligible"] & (k >= 1) & (k <= P["w_kmax"]) & ind["above"]
    r["rising"] = r["struct"] & (ind["slope"] >= P["w_min_slope"])
    r["dist"] = r["rising"] & (ind["score"] - 1 >= P["min_dist_low"])
    r["armed"] = r["dist"] & (ind["vol_ratio"] <= P["w_vol_dry"]) if P["w_use_vol_dry"] else r["dist"]
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
    """Every entry the rules produce (independent of any portfolio), plus each setup's outcome."""
    r = setup_rules(ind, P)
    armed = r["armed"].values
    Cw, Hw, Ew, CRw = (ind[k].values for k in ("C", "H", "E", "cr"))
    prevE, prevH = ind["prev_ema"].values, ind["prev_high"].values
    O, H, L = dp["O"], dp["H"], dp["L"]
    first, last, lastv = dp["first"], dp["last"], dp["lastv"]
    lo, hi, thr = P["w_low_lo"], P["w_low_hi"], P["b_min_cr"]
    close_exec = P["execution"] == "close"
    n_days, n_real = len(dp["days"]), len(ind["weeks"])
    cands, setups = [], {}
    for wi, j in zip(*np.nonzero(armed[:n_real])):
        eref, ph = prevE[wi, j], prevH[wi, j]
        if not eref > 0 or np.isnan(ph):
            continue
        zl, zh = eref * (1 + lo), eref * (1 + hi)
        touched, broken, entry, wtd = None, False, None, np.inf
        for d in range(first[wi], last[wi] + 1):
            low = L[d, j]
            if np.isnan(low):
                continue
            wtd = min(wtd, low)
            if wtd < zl:
                broken = True
                break
            if touched is None:
                if low <= zh:
                    touched = d
                    if O[d, j] <= ph < H[d, j]:
                        entry = (d, ph, 1, False, False)
            elif H[d, j] > ph:
                entry = (d, _fill(O[d, j], ph), 1, O[d, j] >= ph, False)
            if entry:
                break
        base = {"j": j, "week": wi, "k": ind["k"].iat[wi, j], "slope": ind["slope"].iat[wi, j],
                "score": ind["score"].iat[wi, j], "ema": eref, "prev_high": ph,
                "low_vs_ema": (wtd if np.isfinite(wtd) else np.nan) / eref - 1}
        out = {**base, "touched": touched is not None and not broken, "entry": None}
        week_done = wi < n_real - 1 or not ind.get("partial", False)
        if entry:
            out["status"] = "TRIGGERED"
        elif broken:
            out["status"] = f"low fell more than {abs(lo):.0%} below the 10W EMA"
        elif touched is None:
            out["status"] = "no touch"
        elif np.isnan(Cw[wi, j]):
            out["status"] = "no close"
        elif Cw[wi, j] < Ew[wi, j]:
            out["status"] = "closed below the 10W EMA"
        elif CRw[wi, j] >= thr:
            out["status"] = "BUY NEXT OPEN"
            if close_exec and week_done:
                entry = (lastv[wi, j], Cw[wi, j], 2, False, True)
                out["status"] = "TRIGGERED"
            elif wi + 1 < n_real:
                d = _first_open(dp, j, first[wi + 1], last[wi + 1])
                if d is not None:
                    entry = (d, O[d, j], 2, True, False)
        else:
            out["status"] = "BUY STOP"
            stop = Hw[wi, j]
            if wi + 1 < n_real:
                for d in range(first[wi + 1], last[wi + 1] + 1):
                    if H[d, j] > stop:
                        entry = (d, _fill(O[d, j], stop), 3, O[d, j] >= stop, False)
                        break
        out["close"], out["cr"] = Cw[wi, j], CRw[wi, j]
        out["week_high"], out["week_ema"] = Hw[wi, j], Ew[wi, j]
        if entry:
            d, px, typ, at_open, at_close = entry
            if d >= n_days or np.isnan(px):
                entry = None
            else:
                c = {**base, "day": int(d), "price": float(px), "type": typ,
                     "at_open": bool(at_open), "at_close": bool(at_close),
                     "touch_cr": CRw[wi, j]}
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
            "Touch week": week_label(ind["all_weeks"][c["week"]]),
            "Entry type": ENTRY_TYPES[c["type"]], "Entry date": str(dp["days"][c["day"]].date()),
            "Entry price": c["price"], "Stop-loss": c["price"] * (1 - P["w_stop"]),
            "Target": c["price"] * (1 + P["w_target"]), "Pullback weeks": c["k"],
            "Touch low vs EMA": c["low_vs_ema"], "EMA slope": c["slope"],
            "Above 52W low": c["score"] - 1, "Touch CR": c["touch_cr"]}


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
    """Weekly counts for each cumulative setup rule and the entries by type."""
    r, ind, dp = W["rules"], W["ind"], W["dp"]
    weeks = ind["weeks"]
    n = len(weeks)
    touched = np.zeros(n, int)
    for (wi, _), s in W["setups"].items():
        touched[wi] += bool(s["touched"])
    by_type = {t: np.zeros(n, int) for t in ENTRY_TYPES}
    for c in W["cands"]:
        by_type[c["type"]][dp["day_w"][c["day"]]] += 1
    f = pd.DataFrame({
        "On monthly list": r["listed"].sum(axis=1).values[:n],
        "Eligible": r["eligible"].sum(axis=1).values[:n],
        "52W high 2+ weeks back, closes above EMA": r["struct"].sum(axis=1).values[:n],
        "+ EMA rising": r["rising"].sum(axis=1).values[:n],
        "+ above 52W low": r["dist"].sum(axis=1).values[:n],
        "Setups (all pre-week rules)": r["armed"].sum(axis=1).values[:n],
        "Touched EMA zone": touched,
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
    """Entries in week wi, setups ready for next week, the watch list and near-misses."""
    ind, dp = W["ind"], W["dp"]
    syms = dp["syms"]
    r = W["rules"]
    rows, done = [], set()
    stop_pct, tgt_pct = P["w_stop"], P["w_target"]

    def common(j, wk):
        sc = ind["score"].iat[wk, j]
        turn = ind["turn"].iat[wk, j]
        return {"Symbol": syms[j], "Company": names.get(syms[j], ""), "Industry": inds.get(syms[j], ""),
                "Monthly signal": monthly_label(ind, wk, j), "Close": ind["C"].iat[wi, j], "10W EMA": ind["E"].iat[wi, j],
                "Close vs EMA": ind["close_vs_ema"].iat[wi, j],
                "Pullback weeks": ind["k"].iat[wk, j], "EMA slope": ind["slope"].iat[wk, j],
                "Above 52W low": sc - 1, "Turnover (Rs cr)": turn / 1e7 if pd.notna(turn) else np.nan,
                "_score": sc}

    def levels(trigger):
        return {"Trigger": trigger, "Stop-loss": trigger * (1 - stop_pct), "Target": trigger * (1 + tgt_pct)}

    # 1. entries during week wi
    for c in W["cands"]:
        j = c["j"]
        if dp["day_w"][c["day"]] != wi or j in done:     # one row per stock (already bought)
            continue
        done.add(j)
        rows.append({**common(j, c["week"]), "Status": "TRIGGERED", "Action": ENTRY_TYPES[c["type"]],
                     **levels(c["price"]), "Entry date": str(dp["days"][c["day"]].date()),
                     "Low vs EMA": c["low_vs_ema"], "Closing range": ind["cr"].iat[c["week"], j],
                     "Reason": f"touch week {week_label(ind['all_weeks'][c['week']])}"})
    # 2. setups of week wi still waiting for an entry, as seen at its close
    for (sw, j), s in W["setups"].items():
        if sw != wi or j in done or s["status"] not in ("BUY NEXT OPEN", "BUY STOP"):
            continue
        done.add(j)
        nxt = s["status"] == "BUY NEXT OPEN"
        rows.append({**common(j, wi), "Status": s["status"],
                     "Action": "Strong close: buy at next open" if nxt else "Buy above this week's high",
                     **levels(s["close"] if nxt else s["week_high"]), "Entry date": None,
                     "Low vs EMA": s["low_vs_ema"], "Closing range": s["cr"],
                     "Reason": "touched the 10W EMA and closed above it"})
    # 3. watch list: every setup rule passes for next week; it still needs a touch
    nxt_w = wi + 1
    for j in np.nonzero(r["armed"].values[nxt_w])[0]:
        if j in done:
            continue
        done.add(j)
        e = ind["E"].iat[wi, j]
        rows.append({**common(j, nxt_w), "Status": "WATCH",
                     "Action": f"Wait for a low at/below {e * (1 + P['w_low_hi']):,.2f}, "
                               f"then buy above {ind['H'].iat[wi, j]:,.2f}",
                     **levels(ind["H"].iat[wi, j]), "Entry date": None,
                     "Low vs EMA": ind["L"].iat[wi, j] / e - 1, "Closing range": ind["cr"].iat[wi, j],
                     "Reason": f"zone {e * (1 + P['w_low_lo']):,.2f} - {e * (1 + P['w_low_hi']):,.2f}"})
    # 4. near-misses: came into the week above the EMA, touched the zone, failed a rule
    lve = ind["low_vs_ema"].values[wi]
    prev_ok = (ind["prev_close"].values[wi] >= ind["prev_ema"].values[wi])
    near = ind["listed"].values[wi] & prev_ok & (lve <= P["w_low_hi"]) & ind["k"].notna().values[wi]
    for j in np.nonzero(near)[0]:
        if j in done:
            continue
        why = []
        hist, turn = ind["hist"].iat[wi, j], ind["turn"].iat[wi, j]
        k = ind["k"].iat[wi, j]
        if not hist >= history_weeks(P):
            why.append(f"history {0 if pd.isna(hist) else int(hist)}w < {history_weeks(P)}w")
        if not turn >= P["min_turnover_cr"] * 1e7:
            why.append(f"turnover Rs {0 if pd.isna(turn) else turn / 1e7:.1f} cr < {P['min_turnover_cr']:g} cr")
        if not 1 <= k <= P["w_kmax"]:
            why.append("52W high was last week" if k == 0 else f"52W high {int(k) + 1} weeks back (max {P['w_kmax'] + 1})")
        elif not ind["above"].iat[wi, j]:
            why.append("a pullback week closed below the EMA")
        sl = ind["slope"].iat[wi, j]
        if not sl >= P["w_min_slope"]:
            why.append(f"EMA slope {0 if pd.isna(sl) else sl:+.1%} < {P['w_min_slope']:+.0%}")
        sc = ind["score"].iat[wi, j]
        if not sc - 1 >= P["min_dist_low"]:
            why.append(f"only {0 if pd.isna(sc) else sc - 1:.0%} above 52W low")
        vr = ind["vol_ratio"].iat[wi, j]
        if P["w_use_vol_dry"] and not vr <= P["w_vol_dry"]:
            why.append(f"pullback volume {0 if pd.isna(vr) else vr:.2f}x > {P['w_vol_dry']:.2f}x")
        s = W["setups"].get((wi, j))
        if s and s["status"] not in ("TRIGGERED", "BUY NEXT OPEN", "BUY STOP"):
            why.append(s["status"])
        if not why:
            why.append("no entry")
        rows.append({**common(j, wi), "Status": "Rejected", "Action": None,
                     "Trigger": np.nan, "Stop-loss": np.nan, "Target": np.nan, "Entry date": None,
                     "Low vs EMA": lve[j], "Closing range": ind["cr"].iat[wi, j], "Reason": "; ".join(why)})
    cols = ["Symbol", "Company", "Industry", "Monthly signal", "Status", "Action", "Trigger", "Stop-loss", "Target",
            "Entry date", "Close", "10W EMA", "Close vs EMA", "Low vs EMA", "Closing range",
            "Pullback weeks", "EMA slope", "Above 52W low", "Turnover (Rs cr)", "Reason"]
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["Rank"] + cols)
    df["_o"] = df["Status"].map(STATUS_ORDER)
    df = df.sort_values(["_o", "_score"], ascending=[True, False])
    df.insert(0, "Rank", np.where(df["Status"] != "Rejected", np.arange(1, len(df) + 1), np.nan))
    return df[["Rank"] + cols]
