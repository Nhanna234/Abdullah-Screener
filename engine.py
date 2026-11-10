"""
Strategy engine for the EMA-Reclaim strategy (monthly candles). Pure pandas/numpy.
Two buy setups (OR). They cannot overlap: A needs last month's close BELOW the EMA,
B needs every pullback close AT/ABOVE it.

SETUP A - RECLAIM, at month-end t (all must hold)
  A1. Cross       : Close[t-1] < EMA[t-1]  and  Close[t] > EMA[t]           (EMA on monthly closes)
  A2. Strength    : Close[t] / Close[t-1] - 1 >= min_ret
  A3. Close range : (Close - Low) / (High - Low) >= min_cr                  (signal month)
  A4. EMA trend   : slope = EMA[t-1] / EMA[t-1-w] - 1  (measured BEFORE the signal candle,
                    because the signal candle always pushes the EMA up)
                    Rising >= +band | Flat within +/-band (if allowed) | Falling -> rejected

SETUP B - PULLBACK REVERSAL in an uptrend, at month-end t (all must hold)
  B1. Recent high : the highest close of months t-12..t-1 was made in month t-k-1, 1 <= k <= b_kmax
                    (k = number of pullback candles between the high and the signal month)
  B2. Above EMA   : every close from the high month to t-1 is >= EMA
  B3. EMA rising  : slope (as in A4) >= +band
  B4. Volume dry-up (switch): average daily volume of the k pullback months
                    <= b_vol_dry x average daily volume of the 12 months ending at the high month
  B5. Volume expansion (switch, off by default): signal-month average daily volume
                    >= pullback average
  B6. Near the EMA: b_low_lo <= Low[t] / EMA[t] - 1 <= b_low_hi   (signal month's own EMA)
  B7. Reversal    : Close[t] > EMA[t]; return >= b_min_ret; closing range >= b_min_cr

COMMON to both: Close / 12-month low - 1 >= min_dist_low; Close / EMA <= exit_ext;
                >= min_history months of data; median daily turnover >= min_turnover_cr
RANK  : B (pullback) first, then A-Rising, then A-Flat; within a tier Close / 12-month low, desc.
EXIT  : month-end Close < EMA  or  Close / EMA > exit_ext.
TRADES: decided at the month-end close, executed at the next trading day's open (or the close).
PORTFOLIO: `slots` equal slots; empty slots stay in cash (earning cash_yield) until new
           signals arrive; when full, new signals are ignored.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULTS = dict(
    ema_span=12, min_ret=0.05, min_cr=0.80, min_dist_low=0.30, low_lookback=12,
    slope_window=6, flat_band=0.03, allow_flat=True, exit_ext=1.70,
    min_history=24, min_turnover_cr=1.0,
    slots=10, min_alloc_frac=0.20, cost=0.004, cash_yield=0.06,
    execution="next_open", capital=1_000_000.0,
    setups="B",
    b_kmax=4, b_low_lo=-0.10, b_low_hi=0.05, b_min_ret=0.05, b_min_cr=0.60,
    b_use_vol_dry=True, b_vol_dry=0.80, b_use_vol_exp=False,
)
FIELDS = ("Open", "High", "Low", "Close", "Volume")
B_KMAX_LIMIT = 6                     # pullback lengths computed up to this; UI allows 1..6
TIER = {1: "B · Pullback", 2: "A · Rising", 3: "A · Flat"}
TIER_ORDER = {"B · Pullback": 0, "A · Rising": 1, "A · Flat": 2, "A · Falling": 3, "B · Not rising": 4}


# ════════════════════════════════════════════════════════════ monthly data
def clean_daily(daily: dict) -> tuple[dict, int]:
    """Remove one-day spikes that fully revert (bad ticks) and fix OHLC order."""
    O, H, L, C, V = (daily[f].copy() for f in FIELDS)
    for X in (O, H, L, C):
        X[X <= 0] = np.nan
    r = C / C.ffill().shift(1)
    nxt = C.shift(-1) / C
    spike = ((r > 1.6) & (nxt < 1 / 1.6)) | ((r < 1 / 1.6) & (nxt > 1.6))
    n = int(spike.sum().sum())
    for X in (O, H, L, C, V):
        X[spike] = np.nan
    H = pd.DataFrame(np.fmax(np.fmax(H.values, O.values), C.values), H.index, H.columns)
    L = pd.DataFrame(np.fmin(np.fmin(L.values, O.values), C.values), L.index, L.columns)
    return {"Open": O, "High": H, "Low": L, "Close": C, "Volume": V}, n


def to_monthly(daily: dict, last_final_day: pd.Timestamp | None = None) -> dict:
    """Monthly OHLC + turnover. m['partial'] is True when the last month is still running."""
    O, H, L, C, V = (daily[f] for f in FIELDS)
    if last_final_day is not None:
        keep = C.index <= last_final_day
        O, H, L, C, V = O[keep], H[keep], L[keep], C[keep], V[keep]
    g = C.index.to_period("M")
    m = {"Open": O.groupby(g).first(), "High": H.groupby(g).max(),
         "Low": L.groupby(g).min(), "Close": C.groupby(g).last()}
    m["AvgVol"] = V.where(V > 0).groupby(g).mean()                  # average daily volume (shares)
    med = (C * V).groupby(g).median()                               # median daily turnover in month
    m["Turnover"] = med.rolling(6, min_periods=3).median()          # ~6-month median
    has = C.notna().groupby(g).any()
    for k in list(m):
        m[k] = m[k].where(has)
    last_day = C.index.max()
    m["partial"] = bool((last_day + pd.offsets.BDay(1)).month == last_day.month)
    m["last_day"] = last_day
    return m


def compute_indicators(m: dict, P: dict) -> dict:
    C, H, L, O = m["Close"], m["High"], m["Low"], m["Open"]
    E = C.ewm(span=P["ema_span"], adjust=False).mean().where(C.notna())
    low = L.rolling(P["low_lookback"], min_periods=P["low_lookback"]).min()
    ind = {
        "C": C, "E": E, "H": H, "L": L,
        "ret": C / C.shift(1) - 1,
        "cr": ((C - L) / (H - L)).where(H > L),
        "slope": E.shift(1) / E.shift(1 + P["slope_window"]) - 1,
        "ext": C / E,
        "low12": low,
        "score": C / low,
        "hist": C.notna().cumsum(),
        "turn": m["Turnover"],
        "prev_below": C.shift(1) < E.shift(1),
        "above": C > E,
    }
    ind["cross"] = ind["prev_below"] & ind["above"]
    ind["exec_px"] = O.shift(-1) if P["execution"] == "next_open" else C
    ind["low_vs_ema"] = L / E - 1

    # ---- Setup B structure: where was the 12-month closing high (t-12..t-1)?
    AV = m["AvgVol"]
    hi12 = C.shift(1).rolling(12, min_periods=12).max()
    nan = pd.DataFrame(np.nan, C.index, C.columns)
    b_k, b_pull, b_ref = nan.copy(), nan.copy(), nan.copy()
    b_above = pd.DataFrame(False, C.index, C.columns)
    b_k = b_k.mask(C.shift(1) >= hi12, 0)               # last month IS the high -> no pullback
    above_run = C.shift(1) >= E.shift(1)                # closes >= EMA from t-1 back to the high
    for k in range(1, B_KMAX_LIMIT + 1):
        above_run = above_run & (C.shift(k + 1) >= E.shift(k + 1))
        hit = b_k.isna() & hi12.notna() & (C.shift(k + 1) >= hi12)
        b_k = b_k.mask(hit, k)
        b_above = b_above | (hit & above_run)
        pull = AV.shift(1).rolling(k, min_periods=1).mean()            # pullback months t-k..t-1
        ref = AV.shift(k + 1).rolling(12, min_periods=6).mean()        # 12 months ending at the high
        b_pull = b_pull.mask(hit, pull)
        b_ref = b_ref.mask(hit, ref)
    ind["b_k"] = b_k
    ind["b_above"] = b_above
    ind["b_vol_ratio"] = b_pull / b_ref
    ind["b_rev_vol"] = AV / b_pull
    return ind


# ══════════════════════════════════════════════════════════════ signals
def _rules(ind: dict, P: dict) -> dict:
    """Boolean frames for every rule of both setups (shared by signals and the funnel)."""
    r = {}
    r["eligible"] = (ind["hist"] >= P["min_history"]) & \
        (ind["turn"] >= P["min_turnover_cr"] * 1e7) & ind["C"].notna()
    r["dist"] = ind["score"].notna() & (ind["score"] - 1 >= P["min_dist_low"])
    r["ext"] = (ind["ext"] <= P["exit_ext"]) if P["exit_ext"] else pd.DataFrame(True, ind["C"].index, ind["C"].columns)
    r["rising"] = ind["slope"] >= P["flat_band"]
    r["flat"] = (ind["slope"] >= -P["flat_band"]) & ~r["rising"]
    # Setup A
    r["a_cross"] = r["eligible"] & ind["cross"]
    r["a_ret"] = r["a_cross"] & (ind["ret"] >= P["min_ret"])
    r["a_cr"] = r["a_ret"] & (ind["cr"] >= P["min_cr"])
    r["a_common"] = r["a_cr"] & r["dist"] & r["ext"]
    r["a_sig"] = r["a_common"] & ((r["rising"] | r["flat"]) if P["allow_flat"] else r["rising"])
    # Setup B
    k = ind["b_k"]
    r["b_struct"] = r["eligible"] & (k >= 1) & (k <= P["b_kmax"]) & ind["b_above"]
    r["b_zone"] = r["b_struct"] & (ind["low_vs_ema"] >= P["b_low_lo"]) & (ind["low_vs_ema"] <= P["b_low_hi"])
    r["b_rising"] = r["b_zone"] & r["rising"]
    vol = r["b_rising"]
    if P["b_use_vol_dry"]:
        vol = vol & (ind["b_vol_ratio"] <= P["b_vol_dry"])
    if P["b_use_vol_exp"]:
        vol = vol & (ind["b_rev_vol"] >= 1.0)
    r["b_vol"] = vol
    r["b_rev"] = vol & ind["above"] & (ind["ret"] >= P["b_min_ret"]) & (ind["cr"] >= P["b_min_cr"])
    r["b_sig"] = r["b_rev"] & r["dist"] & r["ext"]
    if P["setups"] == "A":
        r["b_sig"] = r["b_sig"] & False
    if P["setups"] == "B":
        r["a_sig"] = r["a_sig"] & False
    return r


def compute_signals(ind: dict, P: dict):
    """Returns (signal bool frame, tier frame 1=B 2=A-Rising 3=A-Flat, funnel DataFrame)."""
    r = _rules(ind, P)
    sig = r["a_sig"] | r["b_sig"]
    tier = pd.DataFrame(np.where(r["b_sig"], 1, np.where(r["rising"], 2, 3)), ind["C"].index, ind["C"].columns)
    funnel = pd.DataFrame({
        "Eligible": r["eligible"].sum(axis=1),
        "A: crossed above EMA": r["a_cross"].sum(axis=1),
        "A: + return": r["a_ret"].sum(axis=1),
        "A: + closing range": r["a_cr"].sum(axis=1),
        "A: + 12M-low & extension": r["a_common"].sum(axis=1),
        "B: uptrend + 1-k pullback above EMA": r["b_struct"].sum(axis=1),
        "B: + low near EMA": r["b_zone"].sum(axis=1),
        "B: + EMA rising": r["b_rising"].sum(axis=1),
        "B: + volume rules": r["b_vol"].sum(axis=1),
        "B: + reversal candle": r["b_rev"].sum(axis=1),
        "Signals": sig.sum(axis=1),
        "B · Pullback": r["b_sig"].sum(axis=1),
        "A · Rising": (r["a_sig"] & r["rising"]).sum(axis=1),
        "A · Flat": (r["a_sig"] & ~r["rising"]).sum(axis=1),
    })
    return sig, tier, funnel


def screen_month(ind: dict, P: dict, t, names: dict, inds: dict) -> pd.DataFrame:
    """Every Setup-A candidate (crossed above the EMA) and Setup-B candidate (pullback in an
    uptrend whose low reached the EMA zone) in month t, with SIGNAL or the rules it failed."""
    rows = []
    for s in ind["C"].columns:
        c = ind["C"].at[t, s]
        if pd.isna(c):
            continue
        is_a = bool(ind["cross"].at[t, s]) and P["setups"] in ("A", "A+B")
        k = ind["b_k"].at[t, s]
        lve = ind["low_vs_ema"].at[t, s]
        is_b = (P["setups"] in ("B", "A+B") and pd.notna(k) and 1 <= k <= P["b_kmax"]
                and bool(ind["b_above"].at[t, s]) and pd.notna(lve)
                and P["b_low_lo"] <= lve <= P["b_low_hi"])
        if not (is_a or is_b):
            continue
        ret, cr, sc, ext, sl = (ind[x].at[t, s] for x in ("ret", "cr", "score", "ext", "slope"))
        hist, turn, e = ind["hist"].at[t, s], ind["turn"].at[t, s], ind["E"].at[t, s]
        why = []
        if hist < P["min_history"]:
            why.append(f"history {int(hist)}m < {P['min_history']}m")
        if not (turn >= P["min_turnover_cr"] * 1e7):
            why.append(f"turnover Rs {0 if pd.isna(turn) else turn / 1e7:.1f} cr < {P['min_turnover_cr']:g} cr")
        slope_txt = f"{0 if pd.isna(sl) else sl:+.1%} in {P['slope_window']}m"
        if is_a:
            tier = "A · Rising" if sl >= P["flat_band"] else ("A · Flat" if sl >= -P["flat_band"] else "A · Falling")
            if not (ret >= P["min_ret"]):
                why.append(f"return {ret:.1%} < {P['min_ret']:.0%}")
            if not (cr >= P["min_cr"]):
                why.append(f"closing range {0 if pd.isna(cr) else cr:.2f} < {P['min_cr']:.2f}")
            if tier == "A · Falling" or (tier == "A · Flat" and not P["allow_flat"]) or pd.isna(sl):
                why.append(f"EMA {tier.split(' · ')[1].lower()} ({slope_txt})")
        else:
            tier = "B · Pullback" if sl >= P["flat_band"] else "B · Not rising"
            if tier != "B · Pullback":
                why.append(f"EMA not rising ({slope_txt})")
            vr, rv = ind["b_vol_ratio"].at[t, s], ind["b_rev_vol"].at[t, s]
            if P["b_use_vol_dry"] and not (vr <= P["b_vol_dry"]):
                why.append(f"pullback volume {0 if pd.isna(vr) else vr:.2f}x > {P['b_vol_dry']:.2f}x")
            if P["b_use_vol_exp"] and not (rv >= 1.0):
                why.append(f"reversal volume {0 if pd.isna(rv) else rv:.2f}x of pullback < 1x")
            if not (c > e):
                why.append("close not above EMA")
            if not (ret >= P["b_min_ret"]):
                why.append(f"return {ret:.1%} < {P['b_min_ret']:.0%}")
            if not (cr >= P["b_min_cr"]):
                why.append(f"closing range {0 if pd.isna(cr) else cr:.2f} < {P['b_min_cr']:.2f}")
        if not (sc - 1 >= P["min_dist_low"]):
            why.append(f"only {0 if pd.isna(sc) else sc - 1:.0%} above 12M low")
        if P["exit_ext"] and ext > P["exit_ext"]:
            why.append(f"already {ext:.2f}x EMA")
        rows.append({"Symbol": s, "Company": names.get(s, ""), "Industry": inds.get(s, ""),
                     "Status": "SIGNAL" if not why else "Rejected", "Setup": tier,
                     "Close": c, "EMA": e, "Month return": ret, "Closing range": cr,
                     "Low vs EMA": lve, "Above 12M low": sc - 1, "Close/EMA": ext, "EMA slope": sl,
                     "Pullback months": k if is_b else np.nan,
                     "Pullback vol x": ind["b_vol_ratio"].at[t, s] if is_b else np.nan,
                     "Reversal vol x": ind["b_rev_vol"].at[t, s] if is_b else np.nan,
                     "Turnover (Rs cr)": turn / 1e7 if pd.notna(turn) else np.nan,
                     "Reason": "; ".join(why) if why else "passes all rules"})
    df = pd.DataFrame(rows)
    if len(df):
        df["_s"] = (df["Status"] != "SIGNAL").astype(int)
        df["_t"] = df["Setup"].map(TIER_ORDER)
        df = df.sort_values(["_s", "_t", "Above 12M low"], ascending=[True, True, False]).drop(columns=["_s", "_t"])
        df.insert(0, "Rank", np.where(df["Status"] == "SIGNAL", np.arange(1, len(df) + 1), np.nan))
    return df


# ═══════════════════════════════════════════════════════════ positions
def trade_outcome(ind: dict, sym: str, t_idx: int, P: dict, last_idx: int):
    """Entry at the signal of month t_idx; walks forward to the first exit up to last_idx."""
    C = ind["C"][sym].values
    E = ind["E"][sym].values
    X = ind["exec_px"][sym].values
    entry_px = X[t_idx]
    valid = np.where(~np.isnan(C[: last_idx + 1]))[0]
    last_valid = valid[-1]
    for u in range(t_idx + 1, last_idx + 1):
        if u > last_valid:
            break
        if np.isnan(C[u]):
            continue
        reason = None
        if C[u] < E[u]:
            reason = "Close < EMA"
        elif P["exit_ext"] and C[u] / E[u] > P["exit_ext"]:
            reason = f"Close/EMA > {P['exit_ext']:g}"
        if reason:
            px = X[u] if not np.isnan(X[u]) else C[u]
            if u == last_idx:
                return entry_px, None, C[u], reason + " (signal on last month - exit pending)"
            return entry_px, u, px, reason
    if last_valid < last_idx:
        return entry_px, max(last_valid, t_idx + 1), C[last_valid], "Data ended"
    return entry_px, None, C[last_idx], "Open"


def simulate(ind, sig, tier, months, start_idx, end_idx, P, names=None, inds=None):
    names, inds = names or {}, inds or {}
    C_ff = ind["C"].ffill()
    syms = list(ind["C"].columns)
    cash = float(P["capital"])
    m_cash = (1 + P["cash_yield"]) ** (1 / 12)
    cost = P["cost"]
    holdings, values, trades, snap = {}, [], [], []
    sig_v, tier_v, score_v, ext_v = sig.values, tier.values, ind["score"].values, ind["exec_px"].values

    for i in range(start_idx, end_idx + 1):
        t = months[i]
        if i > start_idx:
            cash *= m_cash
        inv = sum(h["qty"] * C_ff.iat[i, h["col"]] for h in holdings.values())
        values.append({"Month": t, "Value": cash + inv, "Cash": cash, "Holdings": len(holdings)})
        if i == end_idx:
            break
        for s in [s for s, h in holdings.items() if h["exit_idx"] == i]:
            h = holdings.pop(s)
            proceeds = h["qty"] * h["exit_px"] * (1 - cost)
            cash += proceeds
            trades.append({**h["info"], "Exit month": str(t), "Exit price": h["exit_px"],
                           "Exit reason": h["reason"], "Months held": i - h["sig_idx"],
                           "Invested": h["invested"], "Return": proceeds / h["invested"] - 1,
                           "P&L": proceeds - h["invested"], "Status": "Closed"})
        free = P["slots"] - len(holdings)
        cand = []
        if free > 0:
            for j in np.where(sig_v[i])[0]:
                s = syms[j]
                if s in holdings or np.isnan(ext_v[i, j]):
                    continue
                cand.append((tier_v[i, j], -score_v[i, j], s, j))
            cand.sort()
            cand = cand[:free]
        if cand:
            equity = cash + sum(h["qty"] * C_ff.iat[i, h["col"]] for h in holdings.values())
            target = equity / P["slots"]
            alloc = min(target, cash / len(cand))
            if alloc < P["min_alloc_frac"] * target:
                cand = []
            for tr, _, s, j in cand:
                px = ext_v[i, j]
                _, x_idx, x_px, reason = trade_outcome(ind, s, i, P, end_idx)
                info = {"Symbol": s, "Company": names.get(s, ""), "Industry": inds.get(s, ""),
                        "Tier": TIER[int(tr)], "Signal month": str(t),
                        "Signal low vs EMA": ind["low_vs_ema"].iat[i, j],
                        "Entry price": px, "Signal return": ind["ret"].iat[i, j],
                        "Signal CR": ind["cr"].iat[i, j], "Signal above 12M low": score_v[i, j] - 1,
                        "Signal EMA slope": ind["slope"].iat[i, j], "Signal Close/EMA": ind["ext"].iat[i, j]}
                holdings[s] = {"col": j, "qty": alloc * (1 - cost) / px, "invested": alloc,
                               "sig_idx": i, "exit_idx": x_idx, "exit_px": x_px, "reason": reason,
                               "info": info}
                cash -= alloc
        snap.append({"Month": str(t), "Slots used": len(holdings),
                     "New entries": ", ".join(c[2] for c in cand)})

    last = months[end_idx]
    for s, h in holdings.items():
        px = C_ff.iat[end_idx, h["col"]]
        trades.append({**h["info"], "Exit month": "open", "Exit price": px,
                       "Exit reason": h["reason"] if "pending" in h["reason"] else "still held",
                       "Months held": end_idx - h["sig_idx"], "Invested": h["invested"],
                       "Return": h["qty"] * px / h["invested"] - 1,
                       "P&L": h["qty"] * px - h["invested"], "Status": "Open",
                       "Close/EMA now": px / ind["E"].iat[end_idx, h["col"]]})
    vals = pd.DataFrame(values).set_index("Month")
    return vals, pd.DataFrame(trades), pd.DataFrame(snap), last


def all_signal_trades(ind, sig, tier, months, start_idx, end_idx, P, names=None) -> pd.DataFrame:
    """Every signal as an independent equal trade (no slot limit) - the raw edge of the signal."""
    names = names or {}
    rows, syms = [], list(ind["C"].columns)
    for i in range(start_idx, end_idx):
        for j in np.where(sig.values[i])[0]:
            s = syms[j]
            if np.isnan(ind["exec_px"].iat[i, j]):
                continue
            e_px, x_idx, x_px, reason = trade_outcome(ind, s, i, P, end_idx)
            rows.append({"Signal month": str(months[i]), "Symbol": s, "Company": names.get(s, ""),
                         "Tier": TIER[int(tier.iat[i, j])],
                         "Signal low vs EMA": ind["low_vs_ema"].iat[i, j],
                         "Signal return": ind["ret"].iat[i, j], "Signal CR": ind["cr"].iat[i, j],
                         "Signal above 12M low": ind["score"].iat[i, j] - 1,
                         "Signal EMA slope": ind["slope"].iat[i, j],
                         "Entry price": e_px, "Exit price": x_px, "Exit reason": reason,
                         "Months held": (x_idx if x_idx is not None else end_idx) - i,
                         "Return": x_px / e_px * (1 - P["cost"]) ** 2 - 1,
                         "Status": "Closed" if x_idx is not None else "Open"})
    return pd.DataFrame(rows)


def ew_universe(ind, months, start_idx, end_idx) -> pd.Series:
    """Equal-weight, monthly-rebalanced return of the same universe (same survivorship bias)."""
    C = ind["C"]
    r = (C / C.shift(1) - 1).iloc[start_idx + 1:end_idx + 1]
    lvl = (1 + r.mean(axis=1).fillna(0)).cumprod()
    return pd.concat([pd.Series([1.0], index=[months[start_idx]]), lvl])


# ═══════════════════════════════════════════════════════════ analytics
def perf_stats(v: pd.Series, rf: float) -> dict:
    v = v.dropna()
    r = v.pct_change().dropna()
    if len(r) < 1:
        return {}
    yrs = len(r) / 12
    dd = v / v.cummax() - 1
    under = (dd < 0).astype(int)
    longest = under.groupby((under == 0).cumsum()).sum().max()
    vol = r.std() * np.sqrt(12) if len(r) > 1 else np.nan
    down = r[r < 0].std() * np.sqrt(12) if (r < 0).sum() > 1 else np.nan
    cagr = (v.iloc[-1] / v.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    ex = r.mean() * 12 - rf
    return {"Total return": v.iloc[-1] / v.iloc[0] - 1,
            "CAGR": cagr if yrs >= 1 else np.nan,
            "Volatility (ann.)": vol,
            "Sharpe": ex / vol if vol else np.nan,
            "Sortino": ex / down if down else np.nan,
            "Max drawdown": dd.min(),
            "Longest DD (months)": int(longest),
            "Calmar": cagr / abs(dd.min()) if dd.min() < 0 and yrs >= 1 else np.nan,
            "Best month": r.max(), "Worst month": r.min(),
            "Positive months": (r > 0).mean()}


def relative_stats(strat: pd.Series, bench: pd.Series) -> dict:
    a = pd.concat([strat.pct_change(), bench.pct_change()], axis=1).dropna()
    if len(a) < 6:
        return {}
    s, b = a.iloc[:, 0], a.iloc[:, 1]
    beta = np.cov(s, b)[0, 1] / b.var()
    up, dn = b > 0, b < 0
    return {"Beta": beta, "Alpha (ann.)": (s.mean() - beta * b.mean()) * 12,
            "Correlation": s.corr(b),
            "Up capture": s[up].mean() / b[up].mean() if up.any() else np.nan,
            "Down capture": s[dn].mean() / b[dn].mean() if dn.any() else np.nan,
            "Months beating": (s > b).mean()}


def trade_stats(tr: pd.DataFrame) -> dict:
    if tr is None or tr.empty:
        return {}
    closed = tr[tr["Status"] == "Closed"]
    r = closed["Return"]
    w, l = r[r > 0], r[r <= 0]
    pl = closed["P&L"] if "P&L" in closed else r
    gl = -pl[pl <= 0].sum()
    top = pl.sort_values(ascending=False)
    return {"Entries": len(tr), "Closed trades": len(closed), "Open positions": len(tr) - len(closed),
            "Win rate": len(w) / len(r) if len(r) else np.nan,
            "Avg win": w.mean() if len(w) else np.nan, "Avg loss": l.mean() if len(l) else np.nan,
            "Payoff ratio": -w.mean() / l.mean() if len(w) and len(l) and l.mean() < 0 else np.nan,
            "Profit factor": pl[pl > 0].sum() / gl if gl > 0 else np.nan,
            "Expectancy / trade": r.mean() if len(r) else np.nan,
            "Median trade": r.median() if len(r) else np.nan,
            "Avg months held": closed["Months held"].mean() if len(closed) else np.nan,
            "Top-10 trades share of profit": (top.head(10).sum() / pl.sum())
            if len(pl) and pl.sum() > 0 else np.nan}


def run_backtest(ind, P, months, start_idx, end_idx, names=None, inds=None) -> dict:
    sig, tier, funnel = compute_signals(ind, P)
    vals, trades, snap, last = simulate(ind, sig, tier, months, start_idx, end_idx, P, names, inds)
    allsig = all_signal_trades(ind, sig, tier, months, start_idx, end_idx, P, names)
    return {"vals": vals, "trades": trades, "snap": snap, "allsig": allsig,
            "funnel": funnel.iloc[start_idx:end_idx + 1],
            "ew": ew_universe(ind, months, start_idx, end_idx)}
