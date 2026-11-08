# EMA Reclaim Lab

Streamlit app that screens NSE indices for the monthly **EMA-reclaim breakout** and backtests it.

**Buy = Setup A or Setup B (month-end):**
- **A · Reclaim** — previous close below the 12-month EMA, this close above it · month return ≥ 8% ·
  closing range ≥ 0.80 · EMA flat or rising over the 6 months before the signal (±3% band).
- **B · Pullback reversal** — 12-month closing high made 2–5 months earlier (1–4 pullback candles) ·
  every close since the high at/above the EMA · EMA rising (≥ +3% in 6 months) · pullback average daily
  volume ≤ 0.8× the 12 months ending at the high · optional: reversal-month volume ≥ pullback average ·
  signal-month low within −15%…+5% of that month's EMA · close above EMA · return ≥ 8% · closing range ≥ 0.75.
- **Both:** optional minimum distance above the 12-month low · not already above 1.7× EMA · ≥ 36 months of
  history · liquidity floor.

**Rank:** B · Pullback first, then A · Rising, then A · Flat; within each, Close / 12-month low.
**Exit:** close below EMA or Close/EMA > 1.7.
**Portfolio:** 10 slots, unfilled slots stay in cash, new signals ignored when full.

Indices: Nifty 50, 100, 200, LargeMidcap 250, Smallcap 100, Smallcap 250, 500, Microcap 250, Total Market.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit page: sidebar rules, Screener, Backtest, Rules & data |
| `engine.py` | Signals, screener, portfolio simulation, analytics (pure pandas, no Streamlit) |
| `data.py` | Constituent lists, per-stock Yahoo price store, Nifty 50 / 500 TRI benchmarks |
| `requirements.txt` | Pinned dependencies |

## Deploy on Streamlit Community Cloud

1. Create a GitHub repository (e.g. `ema-reclaim-lab`) and upload the four files above to its root.
2. Go to **share.streamlit.io** → **Create app** → *Deploy a public app from GitHub*.
3. Repository: your repo · Branch: `main` · Main file path: `app.py`.
4. **Advanced settings → Python version: 3.12** (tested on 3.13 as well).
5. Deploy. The first page load downloads the chosen index from Yahoo Finance
   (Nifty 500 ≈ 1–3 minutes, Total Market longer). After that, prices are refreshed incrementally
   after 5 pm IST on trading days, and every index reuses the same stored prices.

Run locally instead: `pip install -r requirements.txt` then `streamlit run app.py`.

## Things to know

- **Yahoo throttling.** Streamlit Cloud apps share IP addresses, and Yahoo sometimes rate-limits them.
  The app downloads in batches of 50 with retries and shows how many stocks have data. If coverage is
  below 90%, wait a few minutes and press **Refresh prices now**; results cover only stocks with data.
- **Cold starts.** Streamlit Cloud puts apps to sleep after 12 hours without traffic, and a reboot or
  redeploy clears the price store, so the next visit downloads again.
- **Constituents** come from NSE's official CSV files (three mirrors). If they're blocked, upload the
  CSV in the sidebar. Nifty Total Market falls back to Nifty 500 + Microcap 250.
- **Benchmarks** default to the Total Return Index (stock prices are dividend-adjusted), from niftyindices.com,
  with the price index or Yahoo as fallback; the source used is shown under the backtest KPIs.
- **Survivorship bias.** Today's constituent list is used for every past month, which flatters the
  backtest. The equal-weight universe line shares the same bias and is the fairer comparison.

Rules output, not investment advice.
