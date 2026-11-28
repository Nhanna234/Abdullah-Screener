# EMA Reclaim Lab

Streamlit app that screens NSE indices for the monthly **EMA-reclaim breakout** and backtests it.

**Buy = Setup A or Setup B (month-end):**
- **A · Reclaim** — previous close below the 12-month EMA, this close above it · month return ≥ 5% ·
  closing range ≥ 0.80 · EMA flat or rising over the 6 months before the signal (±3% band).
- **B · Pullback reversal** — 12-month closing high made 2–5 months earlier (1–4 pullback candles) ·
  every close since the high at/above the EMA · EMA rising (≥ +3% in 6 months) · pullback average daily
  volume ≤ 0.8× the 12 months ending at the high · optional (off): reversal-month volume ≥ pullback average ·
  signal-month low within −10%…+5% of that month's EMA · close above EMA · return ≥ 5% · closing range ≥ 0.60.
- **Both:** close ≥ 30% above the 12-month low · not already above 1.7× EMA · ≥ 24 months of
  history · liquidity floor.

**Defaults:** universe Nifty Total Market, Setup B only (switch to A + B or A only in the sidebar).

**Rank:** B · Pullback first, then A · Rising, then A · Flat; within each, Close / 12-month low.
**Exit:** close below EMA or Close/EMA > 1.7.
**Portfolio:** 10 slots, unfilled slots stay in cash, new signals ignored when full.

**Weekly timeframe (reversal from the 10-week EMA, Timeframe box):** scans only stocks on the monthly list
(joined on a monthly Setup A or B signal, left on the monthly exit; each week uses the list from the last
completed month-end) · 26-week high ÷ 26-week low ≥ 1.3 with the low first · 10W EMA up ≥ 2% over 6 weeks ·
a weekly low within −5%…+3% of the 10W EMA in the signal week or the 2 weeks before · signal week closes above
the 10W EMA with return > 4% and closing range > 0.75. Entry: closing range > 0.90 buys at the signal close,
otherwise above the signal week's high the following week. Exit at +20% target, −7.5% stop (from the entry
price, checked daily) or a weekly close below the 10W EMA. Full rules are on the dashboard's Rules page.

Indices: Nifty 50, 100, 200, LargeMidcap 250, Smallcap 100, Smallcap 250, 500, Microcap 250, Total Market.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit entry point that serves the prebuilt React dashboard |
| `dashboard.py` | Python bridge for the screener, backtest, and Excel export |
| `frontend/` | React and Vite source plus the compiled dashboard in `dist/` |
| `streamlit_legacy.py` | Original Streamlit interface |
| `engine.py` | Signals, screener, portfolio simulation, analytics (pure pandas, no Streamlit) |
| `engine_weekly.py` | Weekly reversal from the 10W EMA: monthly scan list, signals, entries, target/stop exits, daily simulation |
| `data.py` | Constituent lists, per-stock Yahoo price store, Nifty 50 / 500 TRI benchmarks |
| `requirements.txt` | Pinned dependencies |

## Deploy on Streamlit Community Cloud

1. Push the project to GitHub, including `frontend/dist` and `constituents`. Omit `frontend/node_modules` and `__pycache__`.
2. On Streamlit Community Cloud, create an app from the repository and branch you pushed.
3. Use `app.py` as the **main file path**. Choose Python **3.13** under Advanced settings to match the tested runtime.
4. Deploy. The first screen or backtest downloads prices for the selected index. Later runs reuse stored prices and refresh them after the weekday 5 pm IST cutoff.

## Things to know

- **Yahoo throttling.** Streamlit Cloud apps share IP addresses, and Yahoo sometimes rate-limits them.
  The app downloads in batches of 50 with retries and shows how many stocks have data. If coverage is
  below 90%, wait a few minutes and enable **Refresh prices on next run** in Settings; results cover only stocks with data.
- **Cold starts.** Streamlit Cloud puts apps to sleep after 12 hours without traffic, and a reboot or
  redeploy clears the price store, so the next visit downloads again.
- **Constituents** come from NSE's official CSV files (three mirrors). If they're blocked, the app uses the bundled dated snapshot. You can also
  upload a CSV in Settings.
- **Benchmarks** default to the Total Return Index (stock prices are dividend-adjusted), from niftyindices.com,
  with the price index or Yahoo as fallback; the source used is shown under the backtest KPIs.
- **Survivorship bias.** Today's constituent list is used for every past month, which flatters the
  backtest. The equal-weight universe line shares the same bias and is the fairer comparison.

Rules output, not investment advice.

## React dashboard

The compiled assets in `frontend/dist` are served by Streamlit. No Node server is required in the hosted environment.

To run the included build locally:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

After changing the React source, rebuild and commit the updated build output:

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
```

To use the original interface locally, run
`python -m streamlit run streamlit_legacy.py`. The React dashboard calls the
same engine. If the official NSE endpoints fail, the app uses the bundled
snapshots downloaded on 15 November 2026 and shows that source in the results.

### Chart tools

After loading a stock, choose **Trend line** or **Measure** and click two
different candles in the price pane. **Horizontal level** takes one click.
**Undo drawing** removes the latest annotation, **Clear drawings** removes all
annotations, and Escape cancels a tool. Drawings are temporary for the current
ticker and timeframe.
