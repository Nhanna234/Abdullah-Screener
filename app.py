"""
EMA Reclaim Lab — Streamlit app
===============================
Monthly strategy with two buy setups (OR):
  A. Reclaim  - close back above the 12-month EMA with a strong, high-closing month
                while the EMA is flat or rising.
  B. Pullback - stock in an uptrend (12-month closing high 2-5 months ago, closes above a
                rising EMA) pulls back on low volume and forms a reversal candle near the EMA.

  * Screener : stocks meeting the buy rules in any month (default: last completed month),
               near-misses with the rule they failed, monthly chart, exit check for holdings.
  * Backtest : slot-based portfolio between two months, analytics, comparison with
               Nifty 50 / Nifty 500 (TRI by default) and an equal-weight universe.

Run locally:  pip install -r requirements.txt  &&  streamlit run app.py
"""

from __future__ import annotations

import io
import os
import tempfile
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data as D
import engine as E

APP_VERSION = "1.3.0"
CACHE_DIR = os.environ.get("EMA_APP_CACHE", os.path.join(tempfile.gettempdir(), "ema_reclaim_prices"))

# palette (validated categorical slots: strategy, Nifty 50, Nifty 500) + neutral for EW universe
COL = {"Strategy": "#2a78d6", "Nifty 50": "#eb6834", "Nifty 500": "#1baf7a",
       "Equal-weight universe": "#8a8986", "EMA": "#eb6834", "Low": "#8a8986"}
GOOD, BAD = "#0ca30c", "#d03b3b"
DIVERGING = [[0.0, "#d03b3b"], [0.5, "#f0efec"], [1.0, "#2a78d6"]]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

st.set_page_config(page_title="EMA Reclaim Lab", layout="wide")


# ════════════════════════════════════════════════════════════════ cached data
@st.cache_resource
def get_store() -> D.PriceStore:
    return D.PriceStore(CACHE_DIR)


@st.cache_data(ttl=24 * 3600, show_spinner="Fetching index constituents…")
def load_constituents(index_name: str):
    return D.fetch_constituents(index_name)


@st.cache_data(ttl=12 * 3600, show_spinner="Fetching Nifty 50 / Nifty 500 history…")
def load_benchmarks(start: str, end: str, kind: str):
    return D.fetch_benchmarks(pd.Timestamp(start).date(), pd.Timestamp(end).date(), kind)


@st.cache_data(max_entries=4, show_spinner="Building monthly candles and indicators…")
def build_indicators(symbols: tuple, store_version: int, final_day: str, ind_key: tuple):
    store = get_store()
    daily, n_spikes = E.clean_daily(store.panel(symbols))
    m = E.to_monthly(daily, pd.Timestamp(final_day))
    P = dict(ind_key)
    ind = E.compute_indicators(m, P)
    return ind, {"partial": m["partial"], "last_day": m["last_day"], "spikes": n_spikes}


def ensure_prices(symbols: list[str], force: bool) -> dict | None:
    store = get_store()
    fresh_after = datetime.now(D.IST) if force else D.last_cutoff()
    full, stale = store.plan(symbols, fresh_after)
    if not full and not stale:
        return None
    msg = (f"Downloading {len(full)} full histories" if full else "") + \
          (" and " if full and stale else "") + (f"refreshing {len(stale)} stocks" if stale else "")
    bar = st.progress(0.0, text=f"{msg} from Yahoo Finance… (first load of a large index takes a few minutes)")
    stats = store.update(symbols, fresh_after,
                         progress=lambda d, t: bar.progress(min(d / max(t, 1), 1.0), text=f"{msg}… {d}/{t}"))
    bar.empty()
    return stats


# ════════════════════════════════════════════════════════════════════ charts
def _layout(fig, h=380, yfmt=None, ytitle=None):
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, x=0), plot_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,.18)", zeroline=False, tickformat=yfmt, title=ytitle)
    fig.update_xaxes(showgrid=False)
    return fig


def chart_lines(df: pd.DataFrame, yfmt, ytitle, h=400, log=False, fill_strategy=False, zero=False):
    fig = go.Figure()
    for c in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df[c], name=c, mode="lines",
            line=dict(width=2.5 if c == "Strategy" else 2, color=COL.get(c, "#555"),
                      dash="dot" if c == "Equal-weight universe" else None),
            fill="tozeroy" if fill_strategy and c == "Strategy" else None,
            fillcolor="rgba(42,120,214,.12)",
            hovertemplate="%{y:" + (yfmt or ",.1f") + "}"))
    if zero:
        fig.add_hline(y=0, line_color="rgba(128,128,128,.5)", line_width=1)
    _layout(fig, h, yfmt, ytitle)
    if log:
        fig.update_yaxes(type="log")
    return fig


def chart_bar(x, y, h=280, yfmt=None, color=COL["Strategy"], ytitle=None, hfmt=None):
    fig = go.Figure(go.Bar(x=list(x), y=list(y), marker_color=color, marker_line_width=0,
                           hovertemplate="%{x}: %{y" + (f":{hfmt}" if hfmt else "") + "}<extra></extra>"))
    return _layout(fig, h, yfmt, ytitle)


SETUP_COL = {"B · Pullback": "#2a78d6", "A · Rising": "#eb6834", "A · Flat": "#1baf7a"}


def chart_signals_per_month(funnel: pd.DataFrame, h=260):
    x = [p.strftime("%b %y") for p in funnel.index]
    fig = go.Figure()
    for name, colr in SETUP_COL.items():
        fig.add_trace(go.Bar(x=x, y=funnel[name], name=name, marker_color=colr, marker_line_width=0,
                             hovertemplate="%{x}: %{y} " + name + "<extra></extra>"))
    fig.update_layout(barmode="stack", bargap=0.2)
    _layout(fig, h, None, "Signals")
    fig.update_layout(hovermode="closest")
    return fig


def chart_monthly_heatmap(ret: pd.Series):
    df = pd.DataFrame({"y": [p.year for p in ret.index], "m": [p.month for p in ret.index], "r": ret.values})
    piv = df.pivot(index="y", columns="m", values="r").reindex(columns=range(1, 13))
    z = piv.values
    lim = np.nanmax(np.abs(z)) if np.isfinite(z).any() else 0.1
    text = [[("" if np.isnan(v) else f"{v:+.1%}") for v in row] for row in z]
    fig = go.Figure(go.Heatmap(z=z, x=MONTHS, y=[str(y) for y in piv.index], text=text,
                               texttemplate="%{text}", colorscale=DIVERGING, zmin=-lim, zmax=lim,
                               xgap=2, ygap=2, showscale=False,
                               hovertemplate="%{y} %{x}: %{z:+.2%}<extra></extra>"))
    fig.update_layout(height=max(220, 30 * len(piv) + 60), margin=dict(l=10, r=10, t=10, b=10),
                      yaxis=dict(autorange="reversed", type="category"))
    return fig


def chart_yearly(yearly: pd.DataFrame):
    fig = go.Figure()
    for c in yearly.columns:
        fig.add_trace(go.Bar(x=yearly.index.astype(str), y=yearly[c], name=c, marker_color=COL[c],
                             marker_line_width=0, hovertemplate="%{y:+.1%}"))
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
    return _layout(fig, 340, ".0%", "Calendar-year return")


def chart_hist(returns: pd.Series):
    fig = go.Figure(go.Histogram(x=returns, nbinsx=40, marker_color=COL["Strategy"],
                                 marker_line_width=1, marker_line_color="rgba(255,255,255,.6)",
                                 hovertemplate="%{x}: %{y} trades<extra></extra>"))
    fig.add_vline(x=0, line_color="rgba(128,128,128,.7)", line_width=1)
    _layout(fig, 300, None, "Trades")
    fig.update_xaxes(tickformat=".0%", title="Trade return (net)")
    fig.update_layout(hovermode="closest")
    return fig


def chart_stock(ind: dict, sym: str, t, P: dict, n=72):
    idx = list(ind["C"].index)
    i = idx.index(t)
    lo = max(0, i - n + 1)
    hi = min(len(idx), i + 7)
    per = idx[lo:hi]
    x = [p.to_timestamp() for p in per]
    O = ind["C"][sym].shift(1).loc[per]               # display open = previous close (monthly gaps are small)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=x, open=O, high=ind["H"][sym].loc[per], low=ind["L"][sym].loc[per],
                                 close=ind["C"][sym].loc[per], name="Monthly",
                                 increasing_line_color=GOOD, decreasing_line_color=BAD))
    fig.add_trace(go.Scatter(x=x, y=ind["E"][sym].loc[per], name=f"{P['ema_span']}M EMA", mode="lines",
                             line=dict(color=COL["EMA"], width=2)))
    fig.add_trace(go.Scatter(x=x, y=ind["low12"][sym].loc[per], name="12M low", mode="lines",
                             line=dict(color=COL["Low"], width=1.5, dash="dot")))
    fig.add_vline(x=t.to_timestamp(), line_color="rgba(42,120,214,.5)", line_width=1, line_dash="dash")
    _layout(fig, 420, ",.0f", "Price (adjusted)")
    fig.update_layout(xaxis_rangeslider_visible=False, hovermode="x unified")
    return fig


def fmt_table(df: pd.DataFrame, fmts: dict):
    out = df.copy().astype(object)
    for r in df.index:
        out.loc[r] = [("—" if pd.isna(v) else fmts.get(r, "{}").format(v)) for v in df.loc[r]]
    return out


ROWFMT = {"Total return": "{:+.1%}", "CAGR": "{:+.1%}", "Volatility (ann.)": "{:.1%}",
          "Sharpe": "{:.2f}", "Sortino": "{:.2f}", "Max drawdown": "{:.1%}",
          "Longest DD (months)": "{:.0f}", "Calmar": "{:.2f}", "Best month": "{:+.1%}",
          "Worst month": "{:+.1%}", "Positive months": "{:.0%}", "Beta": "{:.2f}",
          "Alpha (ann.)": "{:+.1%}", "Correlation": "{:.2f}", "Up capture": "{:.2f}",
          "Down capture": "{:.2f}", "Months beating": "{:.0%}",
          "Entries": "{:.0f}", "Closed trades": "{:.0f}", "Open positions": "{:.0f}",
          "Win rate": "{:.0%}", "Avg win": "{:+.1%}", "Avg loss": "{:+.1%}", "Payoff ratio": "{:.2f}",
          "Profit factor": "{:.2f}", "Expectancy / trade": "{:+.1%}", "Median trade": "{:+.1%}",
          "Avg months held": "{:.1f}", "Top-10 trades share of profit": "{:.0%}"}


# ═══════════════════════════════════════════════════════════════ saved settings
# Sidebar defaults. "Save as default" writes the current values into the page URL
# (query parameters); bookmarking that URL restores them on the next visit.
SETTINGS = {
    "index": "Nifty Total Market", "setups": "B",
    "min_dist": 30.0, "slope_w": 6, "band": 3.0,
    "a_cr": 0.80, "a_ret": 5.0, "a_flat": True,
    "b_kmax": 4, "b_cr": 0.60, "b_ret": 5.0, "b_lo": 10.0, "b_hi": 5.0,
    "b_dry": True, "b_dry_ratio": 0.80, "b_exp": False,
    "ema_span": 12, "min_hist": 24, "min_turn": 1.0, "use_ext": True, "exit_ext": 1.70,
    "slots": 10, "capital": 1_000_000, "cost": 0.40, "cash_yield": 6.0,
    "execution": "next_open", "bench": "TRI",
}
CHOICES = {"index": list(D.INDEX_FILES), "setups": ["A+B", "A", "B"],
           "execution": ["next_open", "close"], "bench": ["TRI", "PRICE"]}


def load_saved() -> tuple[dict, bool]:
    """Defaults, overridden by any valid values saved in the page URL."""
    out, found = dict(SETTINGS), False
    qp = st.query_params
    for k, d in SETTINGS.items():
        if k not in qp:
            continue
        v = qp[k]
        try:
            if isinstance(d, bool):
                val = v in ("1", "true", "True")
            elif isinstance(d, int):
                val = int(float(v))
            elif isinstance(d, float):
                val = float(v)
            else:
                val = v
                if k in CHOICES and v not in CHOICES[k]:
                    continue
            out[k], found = val, True
        except ValueError:
            continue
    return out, found


def clamp(v, lo, hi):
    return min(max(v, lo), hi)


S, from_url = load_saved()

# ═══════════════════════════════════════════════════════════════════ sidebar
with st.sidebar:
    if from_url:
        st.caption("Using settings saved in this page link.")
    st.header("Universe")
    index_name = st.selectbox("Index", CHOICES["index"], index=CHOICES["index"].index(S["index"]), key="index")
    up_const = st.file_uploader("Override constituent list (NSE CSV with a 'Symbol' column)", type="csv")

    st.header("Buy setups")
    setups = st.radio("Use", CHOICES["setups"], index=CHOICES["setups"].index(S["setups"]), horizontal=True,
                      format_func=lambda x: {"A+B": "A + B", "A": "A only", "B": "B only"}[x], key="setups",
                      help="A = reclaim of the EMA from below · B = pullback reversal in an uptrend. "
                           "Ranking: B first, then A-Rising, then A-Flat.")
    min_dist = st.number_input("Min distance above 12-month low (%)", 0.0, 500.0, clamp(S["min_dist"], 0.0, 500.0),
                               5.0, key="min_dist",
                               help="Close / lowest monthly low of the last 12 months − 1. "
                                    "0 = no filter. Applies to both setups; also the ranking metric.") / 100
    c1, c2 = st.columns(2)
    slope_w = c1.number_input("EMA slope window (m)", 2, 12, clamp(S["slope_w"], 2, 12), key="slope_w")
    band = c2.number_input("Flat band ± (%)", 0.0, 20.0, clamp(S["band"], 0.0, 20.0), 0.5, key="band") / 100

    st.subheader("A · Reclaim")
    min_cr = st.slider("A: min closing range", 0.0, 1.0, clamp(S["a_cr"], 0.0, 1.0), 0.05, key="a_cr",
                       help="(Close − Low) / (High − Low) of the monthly candle.")
    min_ret = st.number_input("A: min signal-month return (%)", 0.0, 100.0, clamp(S["a_ret"], 0.0, 100.0), 1.0,
                              key="a_ret") / 100
    allow_flat = st.toggle("A: allow Flat-EMA stocks (ranked after Rising)", S["a_flat"], key="a_flat",
                           help="Off = only stocks whose EMA rose more than the flat band.")

    st.subheader("B · Pullback reversal")
    b_kmax = st.slider("B: pullback candles after the 12-month high (max)", 1, E.B_KMAX_LIMIT,
                       clamp(S["b_kmax"], 1, E.B_KMAX_LIMIT), key="b_kmax",
                       help="The 12-month closing high must be 2 to (max+1) months before the signal month, "
                            "and every close since then at or above the EMA. EMA must be Rising.")
    b_min_cr = st.slider("B: min closing range", 0.0, 1.0, clamp(S["b_cr"], 0.0, 1.0), 0.05, key="b_cr")
    b_min_ret = st.number_input("B: min signal-month return (%)", 0.0, 100.0, clamp(S["b_ret"], 0.0, 100.0), 1.0,
                                key="b_ret") / 100
    c1, c2 = st.columns(2)
    b_low_lo = c1.number_input("B: low ≥ EMA − (%)", 0.0, 50.0, clamp(S["b_lo"], 0.0, 50.0), 1.0, key="b_lo") / 100
    b_low_hi = c2.number_input("B: low ≤ EMA + (%)", 0.0, 50.0, clamp(S["b_hi"], 0.0, 50.0), 1.0, key="b_hi") / 100
    b_use_dry = st.toggle("B: pullback on low volume", S["b_dry"], key="b_dry",
                          help="Average daily volume (shares) of the pullback months ≤ ratio × the average "
                               "of the 12 months ending at the high.")
    b_vol_dry = st.number_input("B: max pullback volume ratio", 0.1, 2.0, clamp(S["b_dry_ratio"], 0.1, 2.0), 0.05,
                                disabled=not b_use_dry, key="b_dry_ratio")
    b_use_exp = st.toggle("B: reversal month volume ≥ pullback average", S["b_exp"], key="b_exp",
                          help="In a month still in progress the average daily volume covers only the days "
                               "traded so far, so this check is provisional until month-end.")

    with st.expander("Advanced rules"):
        ema_span = st.number_input("EMA span (months)", 3, 36, clamp(S["ema_span"], 3, 36), key="ema_span")
        min_hist = st.number_input("Min listing history (months)", 12, 120, clamp(S["min_hist"], 12, 120),
                                   key="min_hist")
        min_turn = st.number_input("Min median daily turnover (Rs crore)", 0.0, 100.0,
                                   clamp(S["min_turn"], 0.0, 100.0), 0.5, key="min_turn",
                                   help="Microcap 250 constituents average roughly Rs 2 cr/day.")
        use_ext = st.toggle("Exit / skip when Close / EMA exceeds", S["use_ext"], key="use_ext")
        exit_ext = st.number_input("Extension limit (× EMA)", 1.1, 5.0, clamp(S["exit_ext"], 1.1, 5.0), 0.05,
                                   disabled=not use_ext, key="exit_ext")

    st.header("Backtest portfolio")
    c1, c2 = st.columns(2)
    slots = c1.number_input("Slots (stocks)", 1, 50, clamp(S["slots"], 1, 50), key="slots")
    capital = c2.number_input("Capital (Rs)", 10_000, 1_000_000_000, clamp(S["capital"], 10_000, 1_000_000_000),
                              100_000, key="capital")
    c1, c2 = st.columns(2)
    cost = c1.number_input("Cost / side (%)", 0.0, 2.0, clamp(S["cost"], 0.0, 2.0), 0.05, key="cost") / 100
    cash_yield = c2.number_input("Cash yield (% pa)", 0.0, 12.0, clamp(S["cash_yield"], 0.0, 12.0), 0.5,
                                 key="cash_yield") / 100
    execution = st.radio("Execution", CHOICES["execution"], index=CHOICES["execution"].index(S["execution"]),
                         horizontal=True, key="execution",
                         format_func=lambda x: "Next day's open" if x == "next_open" else "Signal close")
    bench_kind = st.radio("Benchmarks", CHOICES["bench"], index=CHOICES["bench"].index(S["bench"]),
                          horizontal=True, key="bench",
                          format_func=lambda x: "Total return (TRI)" if x == "TRI" else "Price index",
                          help="Stock prices are dividend-adjusted, so TRI is the fair comparison.")
    force = st.button("Refresh prices now", width="stretch",
                      help="Prices refresh automatically after 5 pm IST on trading days.")
    if st.button("Clear all caches", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("Your settings")
    if st.button("Save as default", type="primary", width="stretch",
                 help="Stores every sidebar setting in this page's link. Bookmark the page afterwards."):
        saved = {}
        for k in SETTINGS:
            v = st.session_state.get(k, S[k])
            saved[k] = ("1" if v else "0") if isinstance(v, bool) else str(v)
        st.query_params.from_dict(saved)
        st.success("Saved into this page's link. **Bookmark the page now** (Ctrl+D, or ⌘+D on Mac) and "
                   "open it from the bookmark next time. The settings come back with it.")
    if st.button("Reset to original defaults", width="stretch"):
        st.query_params.clear()
        for k in SETTINGS:
            st.session_state.pop(k, None)
        st.rerun()
    st.caption("Settings live in the link, not on the server, so each person keeps their own. "
               "Sharing the link shares the settings.")

P = {**E.DEFAULTS, "ema_span": int(ema_span), "min_ret": min_ret, "min_cr": min_cr,
     "min_dist_low": min_dist, "slope_window": int(slope_w), "flat_band": band,
     "allow_flat": allow_flat, "exit_ext": float(exit_ext) if use_ext else None,
     "setups": setups, "b_kmax": int(b_kmax), "b_low_lo": -b_low_lo, "b_low_hi": b_low_hi,
     "b_min_ret": b_min_ret, "b_min_cr": b_min_cr, "b_use_vol_dry": b_use_dry,
     "b_vol_dry": float(b_vol_dry), "b_use_vol_exp": b_use_exp,
     "min_history": int(min_hist), "min_turnover_cr": float(min_turn), "slots": int(slots),
     "cost": cost, "cash_yield": cash_yield, "execution": execution, "capital": float(capital)}
IND_KEY = tuple(sorted({k: P[k] for k in ("ema_span", "low_lookback", "slope_window", "execution")}.items()))

# ═══════════════════════════════════════════════════════════════ universe + data
st.title("EMA reclaim lab")
st.caption("Two monthly buy setups: **A · Reclaim** — close back above the 12-month EMA with a strong, "
           "high-closing month · **B · Pullback** — a stock in an uptrend pulls back to its rising EMA on low "
           "volume and forms a strong reversal candle. Screen any month, or backtest the rules.")
try:
    if up_const is not None:
        const, const_src = D.parse_constituents(pd.read_csv(up_const)), "uploaded file"
    else:
        const, const_src = load_constituents(index_name)
except Exception as e:  # noqa: BLE001
    st.error(str(e))
    st.stop()
names = const.set_index("Symbol")["Company Name"].to_dict()
industry = const.set_index("Symbol")["Industry"].to_dict()
symbols = sorted(const["Symbol"].unique())

try:
    stats = ensure_prices(symbols, force)
except Exception as e:  # noqa: BLE001
    st.error(f"Price download failed: {e}")
    st.stop()
store = get_store()
have = store.coverage(symbols)
if have == 0:
    st.error("Yahoo Finance returned no data for this universe (possibly rate-limited). "
             "Wait a few minutes and press **Refresh prices now**.")
    st.stop()
final_day = D.last_cutoff().date()
ind, meta = build_indicators(tuple(symbols), store.version, str(final_day), IND_KEY)
months = list(ind["C"].index)
last_complete_idx = len(months) - 2 if meta["partial"] else len(months) - 1
last_complete = months[last_complete_idx]

cov_note = f"{have} of {len(symbols)} stocks have price data"
if have < 0.9 * len(symbols):
    st.warning(cov_note + " — Yahoo may be throttling. Results cover only the downloaded stocks; "
               "press **Refresh prices now** later to fill the gaps.")
st.markdown(f"**Universe:** {index_name} · {len(symbols)} stocks ({const_src}) · {cov_note} · "
            f"prices to {pd.Timestamp(meta['last_day']):%d %b %Y}"
            + (f" · last download: {stats['full']} full, {stats['refreshed']} refreshed, "
               f"{stats['re-downloaded']} re-adjusted, {len(stats['failed'])} failed" if stats else ""))

tab_s, tab_b, tab_r = st.tabs(["Screener", "Backtest", "Rules & data"])

# ═══════════════════════════════════════════════════════════════════ screener
with tab_s:
    opts = list(reversed(months[: last_complete_idx + 1]))
    labels = {p: p.strftime("%b %Y") for p in opts}
    if meta["partial"]:
        opts = [months[-1]] + opts
        labels[months[-1]] = f"{months[-1].strftime('%b %Y')} (month in progress — provisional)"
    c1, c2 = st.columns([2, 3])
    t = c1.selectbox("Signal month", opts, index=1 if meta["partial"] else 0, format_func=lambda p: labels[p])
    if meta["partial"] and t == months[-1]:
        c2.warning("Provisional: this month's candle is incomplete. A stock qualifies only on the "
                   "month-end close.")
    else:
        nxt = (t + 1).strftime("%b %Y")
        c2.info(f"Signals are known at the {t.strftime('%b %Y')} close; the backtest buys at the first "
                f"open of {nxt}.")

    sig_all, tier_all, funnel_all = E.compute_signals(ind, P)
    scr = E.screen_month(ind, P, t, names, industry)
    f = funnel_all.loc[t]
    k = st.columns(7)
    k[0].metric("Eligible stocks", int(f["Eligible"]))
    k[1].metric("A: crossed EMA", int(f["A: crossed above EMA"]))
    k[2].metric("B: pullback to EMA", int(f["B: + low near EMA"]))
    k[3].metric("Signals", int(f["Signals"]))
    k[4].metric("B · Pullback", int(f["B · Pullback"]))
    k[5].metric("A · Rising", int(f["A · Rising"]))
    k[6].metric("A · Flat", int(f["A · Flat"]))

    st.subheader(f"Buy signals — {t.strftime('%b %Y')}")
    num = {"Close": "{:,.2f}", "EMA": "{:,.2f}", "Month return": "{:+.1%}", "Closing range": "{:.2f}",
           "Above 12M low": "{:+.1%}", "Close/EMA": "{:.2f}", "EMA slope": "{:+.1%}",
           "Turnover (Rs cr)": "{:,.1f}", "Rank": "{:.0f}", "Low vs EMA": "{:+.1%}",
           "Pullback months": "{:.0f}", "Pullback vol x": "{:.2f}", "Reversal vol x": "{:.2f}"}
    sigs = scr[scr["Status"] == "SIGNAL"] if len(scr) else scr
    if len(sigs):
        show = sigs.drop(columns=["Status", "Reason"])
        st.dataframe(show.style.format(num, na_rep="—"), hide_index=True, width="stretch")
        st.caption(f"Ranked B · Pullback first, then A · Rising, then A · Flat; within each by distance "
                   f"above the 12-month low. The backtest fills free slots from the top of this list "
                   f"(up to {P['slots']} holdings). Pullback/reversal volume = average daily shares vs "
                   f"the reference period.")
    else:
        st.write("No stock meets every rule this month.")
    rej = scr[scr["Status"] != "SIGNAL"] if len(scr) else scr
    with st.expander(f"Near-misses — A: crossed the EMA · B: pulled back into the EMA zone — but failed a rule ({len(rej)})"):
        if len(rej):
            st.dataframe(rej.drop(columns=["Status", "Rank"]).style.format(num, na_rep="—"),
                         hide_index=True, width="stretch",
                         column_config={"Reason": st.column_config.TextColumn(width="large")})
        else:
            st.write("None.")
    if len(scr):
        st.download_button("Download this month's screen (CSV)", scr.to_csv(index=False).encode(),
                           f"ema_reclaim_screen_{index_name.replace(' ', '_')}_{t}.csv", "text/csv")

    st.subheader("Chart")
    pick_from = list(scr["Symbol"]) if len(scr) else []
    c1, c2 = st.columns([1, 3])
    sym = c1.selectbox("Stock", pick_from + [s for s in ind["C"].columns if s not in pick_from],
                       format_func=lambda s: f"{s} — {names.get(s, '')}")
    if sym:
        c2.caption("Monthly candles (open shown as the previous month's close), 12-month EMA and "
                   "rolling 12-month low. Dashed line = selected signal month.")
        st.plotly_chart(chart_stock(ind, sym, t, P), width="stretch")

    st.subheader("Signals per month — last 36 months")
    lo = max(0, months.index(t) - 35)
    st.plotly_chart(chart_signals_per_month(funnel_all.iloc[lo: months.index(t) + 1]), width="stretch")

    with st.expander("Check my holdings against the exit rules"):
        txt = st.text_area("Symbols, one per line or comma-separated", "", height=100)
        if txt.strip():
            rows = []
            for s in [D._clean(x) for x in txt.replace(",", "\n").splitlines() if x.strip()]:
                if s not in ind["C"].columns or pd.isna(ind["C"].at[t, s]):
                    rows.append({"Symbol": s, "Action": "?", "Reason": "no price data for this month"})
                    continue
                c, e = ind["C"].at[t, s], ind["E"].at[t, s]
                why = []
                if c < e:
                    why.append("close below EMA")
                if P["exit_ext"] and c / e > P["exit_ext"]:
                    why.append(f"close {c / e:.2f}× EMA > {P['exit_ext']:g}")
                rows.append({"Symbol": s, "Close": c, "EMA": e, "Close/EMA": c / e,
                             "Action": "SELL" if why else "HOLD",
                             "Reason": "; ".join(why) if why else "above EMA, not over-extended"})
            st.dataframe(pd.DataFrame(rows).style.format({"Close": "{:,.2f}", "EMA": "{:,.2f}",
                                                         "Close/EMA": "{:.2f}"}, na_rep="—"),
                         hide_index=True, width="stretch")
            st.caption("Stocks outside the selected index can be checked only if their prices are "
                       "downloaded (choose Nifty Total Market to cover the most names).")

# ═══════════════════════════════════════════════════════════════════ backtest
with tab_b:
    bt_opts = months[: last_complete_idx]
    first_ok = next((i for i, p in enumerate(bt_opts) if p >= pd.Period("2008-01", "M")), 0)
    first_ok = max(first_ok, min(P["min_history"], len(bt_opts) - 1))
    c1, c2, c3 = st.columns([2, 2, 1])
    b_start = c1.selectbox("First signal month", bt_opts, index=min(first_ok, len(bt_opts) - 1),
                           format_func=lambda p: p.strftime("%b %Y"),
                           help="Signals from this month-end onwards; the portfolio starts in cash.")
    end_opts = [p for p in months[: last_complete_idx + 1] if p > b_start]
    b_end = c2.selectbox("Last month (valuation)", end_opts, index=len(end_opts) - 1,
                         format_func=lambda p: p.strftime("%b %Y"))
    c3.write("")
    run = c3.button("Run backtest", type="primary", width="stretch")

    if run:
        si, ei = months.index(b_start), months.index(b_end)
        with st.spinner("Running backtest…"):
            res = E.run_backtest(ind, P, months, si, ei, names, industry)
            bstart = (b_start - 1).to_timestamp().strftime("%Y-%m-%d")
            bend = (b_end + 1).to_timestamp().strftime("%Y-%m-%d")
            bench, bsrc = load_benchmarks(bstart, bend, bench_kind)
        st.session_state["bt"] = dict(res=res, P=P.copy(), index=index_name, start=b_start, end=b_end,
                                      bench=bench, bsrc=bsrc, nsym=len(symbols), bench_kind=bench_kind)

    B = st.session_state.get("bt")
    if B is None:
        st.info("Pick the period and press **Run backtest**. The same rules as the screener are used.")
    else:
        if B["P"] != P or B["index"] != index_name:
            st.warning("Settings changed since this run — press **Run backtest** to refresh.")
        st.warning("Survivorship bias: today's constituent list is used for the whole period. Stocks "
                   "that failed and left the index are missing — exactly the false reversals this "
                   "strategy would have bought. Treat results as an upper bound; the equal-weight "
                   "universe line carries the same bias and is the fairer yardstick.")
        res, Pb = B["res"], B["P"]
        vals, trades, allsig, funnel = res["vals"], res["trades"], res["allsig"], res["funnel"]
        idx = [p.to_timestamp(how="end").normalize() for p in vals.index]
        curves = pd.DataFrame({"Strategy": vals["Value"].values}, index=idx)
        for c in ("Nifty 50", "Nifty 500"):
            if c in B["bench"].columns:
                curves[c] = B["bench"][c].reindex(vals.index).values
        curves["Equal-weight universe"] = res["ew"].reindex(vals.index).values
        for c in curves.columns:
            first = curves[c].first_valid_index()
            curves[c] = curves[c] / curves[c].loc[first] * 100 if first is not None else np.nan
        curves = curves.dropna(axis=1, how="all")

        rf = Pb["cash_yield"]
        stats = {c: E.perf_stats(curves[c], rf) for c in curves.columns}
        S = stats["Strategy"]
        ts = E.trade_stats(trades)
        expo = 1 - vals["Cash"] / vals["Value"]
        k = st.columns(7)
        k[0].metric("CAGR", "—" if pd.isna(S.get("CAGR")) else f"{S['CAGR']:.1%}",
                    (f"{S['CAGR'] - stats['Nifty 500']['CAGR']:+.1%} vs Nifty 500"
                     if "Nifty 500" in stats and pd.notna(S.get("CAGR")) and pd.notna(stats["Nifty 500"].get("CAGR"))
                     else None))
        k[1].metric("Total return", f"{S['Total return']:+.0%}")
        k[2].metric("Max drawdown", f"{S['Max drawdown']:.1%}")
        k[3].metric("Sharpe", "—" if pd.isna(S["Sharpe"]) else f"{S['Sharpe']:.2f}")
        k[4].metric("Avg invested", f"{expo.iloc[:-1].mean():.0%}" if len(expo) > 1 else "—")
        k[5].metric("Entries", ts.get("Entries", 0))
        k[6].metric("Win rate", "—" if pd.isna(ts.get("Win rate", np.nan)) else f"{ts['Win rate']:.0%}")
        st.caption(f"{B['index']} ({B['nsym']} stocks) · setups {Pb['setups']} · signals {B['start'].strftime('%b %Y')} → valued "
                   f"{B['end'].strftime('%b %Y')} · {Pb['slots']} slots · cost {Pb['cost']:.2%}/side · "
                   f"cash {Pb['cash_yield']:.1%} pa · execution: "
                   f"{'next open' if Pb['execution'] == 'next_open' else 'signal close'} · benchmarks: "
                   + ", ".join(f"{a}: {b}" for a, b in B["bsrc"].items()))

        st.subheader("Growth of ₹100")
        logscale = st.toggle("Log scale", value=len(vals) > 48)
        st.plotly_chart(chart_lines(curves, ",.0f", "Growth of ₹100", 440, log=logscale), width="stretch")
        st.subheader("Drawdown")
        st.plotly_chart(chart_lines(curves / curves.cummax() - 1, ".0%", "Drawdown from peak", 320,
                                    fill_strategy=True), width="stretch")

        st.subheader("Performance vs benchmarks")
        tbl = pd.DataFrame(stats)
        rel = pd.DataFrame({c: E.relative_stats(curves["Strategy"], curves[c].dropna())
                            for c in curves.columns[1:]})
        c1, c2 = st.columns([3, 2])
        c1.markdown("**Absolute**")
        c1.dataframe(fmt_table(tbl, ROWFMT), width="stretch", height=36 * (len(tbl) + 1) + 4)
        c2.markdown("**Strategy vs each benchmark**")
        if len(rel):
            c2.dataframe(fmt_table(rel, ROWFMT), width="stretch", height=36 * (len(rel) + 1) + 4)
            c2.caption("Down capture below 1 = loses less than the benchmark in its down months.")

        mret = curves["Strategy"].pct_change().dropna()
        mret.index = vals.index[1:]
        st.subheader("Monthly returns (strategy)")
        st.plotly_chart(chart_monthly_heatmap(mret), width="stretch")
        st.subheader("Calendar-year returns")
        yr = pd.DataFrame({c: (1 + curves[c].pct_change().dropna()).groupby(lambda d: d.year).prod() - 1
                           for c in curves.columns if c != "Equal-weight universe"})
        st.plotly_chart(chart_yearly(yr), width="stretch")
        if len(curves) > 24:
            st.subheader("Rolling 12-month return")
            st.plotly_chart(chart_lines(curves.pct_change(12).dropna(how="all"), ".0%",
                                        "Rolling 12-month return", 320, zero=True), width="stretch")

        st.subheader("Exposure and signal supply")
        c1, c2 = st.columns(2)
        ex_idx = [p.strftime("%b %y") for p in vals.index]
        c1.plotly_chart(chart_bar(ex_idx, vals["Holdings"], 260, None, ytitle="Stocks held"), width="stretch")
        c2.plotly_chart(chart_bar(ex_idx, expo, 260, ".0%", COL["Equal-weight universe"],
                                  "Share invested", ".0%"), width="stretch")
        st.plotly_chart(chart_signals_per_month(funnel), width="stretch")
        fs = funnel["Signals"]
        st.caption(f"Signals per month: mean {fs.mean():.1f}, median {fs.median():.0f}; months with no "
                   f"signal {(fs == 0).sum()} of {len(fs)}; months with ≥{Pb['slots']} signals "
                   f"{(fs >= Pb['slots']).sum()}.")

        st.subheader("Trade analytics (portfolio)")
        if ts:
            st.dataframe(fmt_table(pd.DataFrame({"Portfolio trades": ts}), ROWFMT).T, width="stretch")
            closed = trades[trades["Status"] == "Closed"]
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Trade return distribution (closed trades)**")
                if len(closed):
                    st.plotly_chart(chart_hist(closed["Return"]), width="stretch")
            with c2:
                st.markdown("**Exit reasons**")
                er = trades["Exit reason"].value_counts()
                st.plotly_chart(chart_bar(er.index, er.values, 300, None, ytitle="Trades"), width="stretch")
            st.markdown("**By setup**")
            g = closed.groupby("Tier")["Return"]
            st.dataframe(pd.DataFrame({"Trades": g.size(), "Win rate": g.apply(lambda x: (x > 0).mean()),
                                       "Avg return": g.mean(), "Median return": g.median()})
                         .style.format({"Win rate": "{:.0%}", "Avg return": "{:+.1%}",
                                        "Median return": "{:+.1%}"}), width="stretch")

            st.markdown("**Does the signal candle predict the trade?** (all signals, no slot limit)")
            base = allsig[allsig["Status"] == "Closed"] if len(allsig) else allsig

            def bucket(col, bins, labels):
                d = base.dropna(subset=[col]).copy()
                if not len(d):
                    return pd.DataFrame()
                d["Bucket"] = pd.cut(d[col], bins=bins, labels=labels, include_lowest=True)
                gg = d.groupby("Bucket", observed=False)["Return"]
                return pd.DataFrame({"Trades": gg.size(), "Win rate": gg.apply(lambda x: (x > 0).mean() if len(x) else np.nan),
                                     "Avg return": gg.mean(), "Median": gg.median()})

            bf = {"Win rate": "{:.0%}", "Avg return": "{:+.1%}", "Median": "{:+.1%}"}
            c1, c2 = st.columns(2)
            c1.caption("Signal-month return")
            c1.dataframe(bucket("Signal return", [-1, 0.12, 0.2, 0.3, 10], ["< 12%", "12–20%", "20–30%", "> 30%"])
                         .style.format(bf, na_rep="—"), width="stretch")
            c2.caption("Closing range")
            c2.dataframe(bucket("Signal CR", [0, 0.85, 0.9, 0.95, 1.0001], ["< 0.85", "0.85–0.90", "0.90–0.95", "0.95–1.00"])
                         .style.format(bf, na_rep="—"), width="stretch")
            c1, c2 = st.columns(2)
            c1.caption("Distance above 12-month low")
            c1.dataframe(bucket("Signal above 12M low", [-1, 0.25, 0.5, 1.0, 100], ["< 25%", "25–50%", "50–100%", "> 100%"])
                         .style.format(bf, na_rep="—"), width="stretch")
            c2.caption("EMA slope before the signal")
            c2.dataframe(bucket("Signal EMA slope", [-1, 0.0, 0.05, 0.15, 10], ["< 0%", "0–5%", "5–15%", "> 15%"])
                         .style.format(bf, na_rep="—"), width="stretch")
            if len(base) and (base["Tier"] == "B · Pullback").any():
                c1, c2 = st.columns(2)
                c1.caption("B · Pullback — signal low vs EMA")
                bb = base[base["Tier"] == "B · Pullback"]
                d = bb.dropna(subset=["Signal low vs EMA"]).copy()
                d["Bucket"] = pd.cut(d["Signal low vs EMA"], [-1, -0.08, -0.03, 0.0, 1],
                                     labels=["< −8%", "−8% to −3%", "−3% to 0%", "above EMA"], include_lowest=True)
                gg = d.groupby("Bucket", observed=False)["Return"]
                c1.dataframe(pd.DataFrame({"Trades": gg.size(), "Win rate": gg.apply(lambda x: (x > 0).mean() if len(x) else np.nan),
                                           "Avg return": gg.mean(), "Median": gg.median()}).style.format(bf, na_rep="—"),
                             width="stretch")
                c2.caption("Setup comparison (every signal)")
                g2 = base.groupby("Tier")["Return"]
                c2.dataframe(pd.DataFrame({"Trades": g2.size(), "Win rate": g2.apply(lambda x: (x > 0).mean()),
                                           "Avg return": g2.mean(), "Median": g2.median()}).style.format(bf, na_rep="—"),
                             width="stretch")
            st.caption("Buckets use every signal as its own trade, so they show the signal's raw edge "
                       "independent of which stocks the 10 slots happened to take.")

            cols = ["Symbol", "Tier", "Signal month", "Entry price", "Exit month", "Exit price",
                    "Return", "Months held", "Exit reason"]
            fm = {"Entry price": "{:,.2f}", "Exit price": "{:,.2f}", "Return": "{:+.1%}", "Months held": "{:.0f}"}
            c1, c2 = st.columns(2)
            c1.markdown("**Best trades**")
            c1.dataframe(trades.nlargest(10, "Return")[cols].style.format(fm), hide_index=True, width="stretch")
            c2.markdown("**Worst trades**")
            c2.dataframe(trades.nsmallest(10, "Return")[cols].style.format(fm), hide_index=True, width="stretch")
            openp = trades[trades["Status"] == "Open"]
            if len(openp):
                st.markdown(f"**Open positions at {B['end'].strftime('%b %Y')}**")
                st.dataframe(openp[cols + ["Close/EMA now"]].style.format({**fm, "Close/EMA now": "{:.2f}"}),
                             hide_index=True, width="stretch")
            with st.expander("Full trade log"):
                st.dataframe(trades, hide_index=True, width="stretch")
        else:
            st.info("No trades in this period with these rules.")

        st.subheader("Signal edge without the slot limit")
        if len(allsig):
            st.dataframe(fmt_table(pd.DataFrame({"Portfolio": ts, "Every signal": E.trade_stats(
                allsig.assign(**{"P&L": allsig["Return"]}))}), ROWFMT), width="stretch")
            st.caption("'Every signal' treats each signal as an equal-sized trade. If it is much worse than "
                       "the portfolio, the result depends on which signals happened to fit in the slots.")

        xbuf = io.BytesIO()
        with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
            pd.concat([tbl, rel.add_prefix("vs ")], axis=1).to_excel(xw, sheet_name="Performance")
            curves.rename_axis("Month end").to_excel(xw, sheet_name="Growth of 100")
            vals.rename(index=str).to_excel(xw, sheet_name="Monthly values")
            trades.to_excel(xw, sheet_name="Portfolio trades", index=False)
            allsig.to_excel(xw, sheet_name="All signals", index=False)
            funnel.rename(index=str).to_excel(xw, sheet_name="Signal funnel")
            res["snap"].to_excel(xw, sheet_name="Entries by month", index=False)
            pd.DataFrame([("App version", APP_VERSION), ("Index", B["index"])] +
                         [(k_, str(v_)) for k_, v_ in Pb.items()],
                         columns=["Setting", "Value"]).to_excel(xw, sheet_name="Settings", index=False)
        st.download_button("Download backtest (Excel)", xbuf.getvalue(),
                           f"ema_reclaim_backtest_{B['start']}_{B['end']}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ═══════════════════════════════════════════════════════════════════ rules
with tab_r:
    ext_txt = f"Close / EMA > {P['exit_ext']:g}" if P["exit_ext"] else "(extension exit off)"
    st.markdown(f"""
A buy needs **Setup A or Setup B** at a month-end close (setups in use: **{P['setups']}**). The two cannot
overlap: A needs last month's close *below* the EMA, B needs every pullback close *at or above* it.

**Setup A · Reclaim (all must hold)**
1. **Reclaim** — previous month closed below its {P['ema_span']}-month EMA; this month closes above it.
2. **Strength** — month return ≥ {P['min_ret']:.0%}.
3. **Closing range** — (Close − Low) / (High − Low) of the month ≥ {P['min_cr']:.2f}.
4. **EMA trend** — EMA change over the {P['slope_window']} months *before* the signal month:
   Rising ≥ +{P['flat_band']:.1%}, Flat within ±{P['flat_band']:.1%}, Falling → rejected
   {'' if P['allow_flat'] else '(Flat also rejected)'}. Measured before the signal candle because a close above
   the EMA always lifts the EMA that month, and a close below always lowers it.

**Setup B · Pullback reversal (all must hold)**
1. **Uptrend high** — the highest monthly close of the previous 12 months was made 2 to {P['b_kmax'] + 1} months
   before the signal month, i.e. 1–{P['b_kmax']} pullback candles between the high and the signal candle.
2. **Above the EMA** — every monthly close from the high to last month is at or above the EMA.
3. **Rising EMA** — EMA change over {P['slope_window']} months before the signal ≥ +{P['flat_band']:.1%} (Flat not allowed).
4. **Low-volume pullback** — {('average daily volume (shares) of the pullback months ≤ ' + format(P['b_vol_dry'], '.2f') + '× the average of the 12 months ending at the high') if P['b_use_vol_dry'] else 'off'}.
5. **Volume on reversal** — {'signal-month average daily volume ≥ pullback average' if P['b_use_vol_exp'] else 'off'}.
6. **Near the EMA** — signal-month low between {P['b_low_lo']:+.0%} and {P['b_low_hi']:+.0%} of the signal month's EMA.
7. **Reversal candle** — close above the EMA, month return ≥ {P['b_min_ret']:.0%}, closing range ≥ {P['b_min_cr']:.2f}.

**Common to both**
- **Off the low** — Close at least {P['min_dist_low']:.0%} above the lowest monthly low of the last 12 months.
- **Not stretched** — {('Close / EMA ≤ ' + format(P['exit_ext'], 'g')) if P['exit_ext'] else 'no limit'} (otherwise it would be an immediate sell).
- **Hygiene** — at least {P['min_history']} months of price history; median daily turnover over ~6 months ≥ Rs {P['min_turnover_cr']:g} cr.

**Ranking** — B · Pullback first, then A · Rising, then A · Flat; within each, Close / 12-month low (highest first).

**Exit** — month-end Close < EMA, or {ext_txt}. Checked only on month-end closes.

**Portfolio (backtest)** — {P['slots']} equal slots of current equity. Each month the empty slots are filled
from the ranked signals; slots that cannot be filled stay in cash earning {P['cash_yield']:.1%} pa. When all
slots are full, new signals are ignored. Positions are not rebalanced. If cash is below 20% of a slot,
the slot stays empty.

**Execution** — decisions at the month-end close; trades at the next trading day's open (or at the
signal close, if selected). Costs {P['cost']:.2%} per side.

**Data**
- Constituents: NSE Indices CSV files (niftyindices.com, nsearchives.nseindia.com). Nifty Total Market falls
  back to Nifty 500 + Nifty Microcap 250, which is how NSE defines it.
- Prices: Yahoo Finance daily OHLCV, split- and dividend-adjusted, cleaned of one-day spikes, aggregated to
  monthly candles. Stored per stock and shared across indices; refreshed after 5 pm IST on trading days.
  If Yahoo re-adjusts history (new split or dividend), the stock is re-downloaded.
- Benchmarks: Nifty 50 and Nifty 500 Total Return Index from niftyindices.com (price index or Yahoo as fallback).
- Monthly candle 'open' in the chart is the previous close; the monthly open from data is used only for
  execution prices.

**Known limitations**
- Survivorship bias: today's constituent list is used for all past months.
- Yahoo data for small and microcaps can have gaps or bad prints; one-day spikes are removed, others are not.
- Signals in the running month are provisional until the month-end close.
- Volume rules use Yahoo's daily share volume. If a split falls inside the comparison window and Yahoo's volume
  history is not split-adjusted for that stock, the volume ratio is distorted for that window.

*Rules output, not investment advice.* App version {APP_VERSION}.
""")
    st.caption(f"Price cache: {CACHE_DIR} · data cleaning removed {meta['spikes']} one-day spikes in this universe.")
