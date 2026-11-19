// Rules & logic page.
// KEEP IN SYNC: this page documents engine.py (signals, screener, backtest, statistics),
// dashboard.py (screener/backtest/chart requests) and data.py (constituents, prices,
// benchmarks). Parameter values are read live from the current settings, and defaults
// come from engine.DEFAULTS through dashboard_config(). Whenever the logic in those files
// changes, update the text below and LOGIC_REVIEWED.
import React from 'react'
import { BookOpenText, CircleHelp } from 'lucide-react'

const LOGIC_REVIEWED = '19 Nov 2026'

const pct = v => v == null ? 'off' : `${+(v * 100).toFixed(2)}%`
const signedPct = v => v == null ? 'off' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${+(Math.abs(v) * 100).toFixed(2)}%`
const plain = v => v == null ? 'off' : String(v)
const onOff = v => v ? 'on' : 'off'
const times = v => v == null ? 'off' : `${v}×`
const crore = v => `₹${v} crore`
const rupees = v => '₹' + Number(v).toLocaleString('en-IN')
const setupName = v => ({ B: 'B only', A: 'A only', 'A+B': 'A + B' }[v] || v)
const execName = v => v === 'close' ? 'at the signal month’s close' : 'at the next trading day’s open'

const SECTIONS = [
  ['rules-overview', 'Overview'], ['rules-data', 'Universe & data'], ['rules-monthly', 'Monthly candles'],
  ['rules-eligible', 'Eligibility'], ['rules-a', 'Setup A'], ['rules-b', 'Setup B'],
  ['rules-common', 'Common filters'], ['rules-rank', 'Ranking'], ['rules-screener', 'Screener'],
  ['rules-backtest', 'Backtest'], ['rules-stats', 'Benchmarks & statistics'], ['rules-limits', 'Limitations'],
]

function Section({ id, title, children }) {
  return <section id={id} className="panel rules-section"><h2>{title}</h2>{children}</section>
}

export default function Rules({ params, defaults, indices, bench }) {
  const p = params || {}
  const d = defaults || {}
  // Current value in bold; the default is shown when the current value differs from it.
  const V = ({ k, f = plain, off }) => {
    const changed = k in d && p[k] !== d[k]
    const shown = p[k] == null && off ? off : f(p[k])
    return <span className="rule-val">{shown}{changed && <span className="rule-default">default {f(d[k])}</span>}</span>
  }
  const ext = <V k="exit_ext" f={times} off="no limit (switched off)"/>
  const band = <V k="flat_band" f={pct}/>
  return <>
    <div className="section-header"><div><h2>Rules & logic</h2>
      <p>How the screener and backtest decide, in the order the code applies the rules.</p></div></div>
    <div className="notice"><CircleHelp size={16}/>
      <span>Values in <strong>bold</strong> are your current Strategy settings, so this page always matches the next run.
        Where a setting differs from the default, the default is shown next to it. Logic last reviewed against the code on {LOGIC_REVIEWED}.</span></div>
    <nav className="rules-toc" aria-label="Rules sections">
      {SECTIONS.map(([id, label]) => <a key={id} href={'#' + id}>{label}</a>)}
    </nav>

    <Section id="rules-overview" title="1. Overview">
      <ul>
        <li>Every rule works on <strong>monthly candles</strong> built from daily prices. A stock is judged on the
          month-end close of the signal month.</li>
        <li>There are two buy setups. <strong>A · EMA reclaim</strong>: the stock closes back above its EMA after closing
          below it. <strong>B · Pullback reversal</strong>: a stock in an uptrend pulls back to a rising EMA and forms a strong
          reversal month. Setups in use: <V k="setups" f={setupName}/>.</li>
        <li>The two setups never overlap. A needs last month’s close <em>below</em> the EMA. B needs every close since the
          recent high <em>at or above</em> it.</li>
        <li>Exits are the same for both setups: a month-end close below the EMA, or a close too far above it (extension limit).</li>
      </ul>
    </Section>

    <Section id="rules-data" title="2. Universe and price data">
      <ul>
        <li><strong>Indices:</strong> {(indices || []).join(', ')}.</li>
        <li><strong>Constituents:</strong> today’s NSE list, tried in this order: niftyindices.com (two addresses),
          nsearchives.nseindia.com, then the bundled NSE snapshot downloaded on 15 Nov 2026. For Nifty Total Market, if all
          of those fail, the list is rebuilt from Nifty 500 + Nifty Microcap 250 (NSE’s own definition). A CSV uploaded in
          Settings replaces the list. Only EQ, BE and BZ series are kept. The source used is shown above the results.</li>
        <li><strong>Prices:</strong> Yahoo Finance daily open, high, low, close and volume, adjusted for splits and
          dividends, from January 2003. Each stock is stored once and shared by every index.</li>
        <li><strong>Refresh:</strong> stored prices are refreshed after 5:00 pm IST on weekdays, or on the next run when
          “Refresh prices on next run” is ticked. Only the last few days are fetched. If Yahoo’s revised history differs
          from the stored copy by more than 0.2% on any overlapping day (a new split or dividend), the stock is downloaded
          again in full.</li>
        <li><strong>Downloads</strong> run in batches of 50 with retries. A notice appears if fewer than 90% of the stocks
          have prices; results then cover only the stocks with data.</li>
        <li><strong>Cleaning:</strong> zero or negative prices are removed. A day that jumps more than 60% up or down and
          fully reverses the next day is treated as a bad print and removed. Each day’s high and low are widened to include
          its open and close.</li>
        <li>Only sessions up to the latest 5:00 pm IST cutoff are used, so a trading day in progress never counts.</li>
      </ul>
    </Section>

    <Section id="rules-monthly" title="3. Monthly candles and indicators">
      <ul>
        <li><strong>Monthly candle:</strong> open = first day’s open, high = highest high, low = lowest low,
          close = last day’s close.</li>
        <li><strong>Average daily volume:</strong> mean daily share volume in the month (zero-volume days ignored).
          Used only by Setup B.</li>
        <li><strong>Turnover:</strong> the median of daily close × volume within each month. The liquidity filter uses the
          median of the last 6 monthly values (at least 3 needed).</li>
        <li><strong>EMA:</strong> <V k="ema_span"/>-month exponential moving average of monthly closes, starting from the
          first available close.</li>
        <li><strong>EMA slope:</strong> EMA at last month ÷ EMA <V k="slope_window"/> months before that − 1. It is
          measured <em>before</em> the signal month, because a close above the EMA always lifts the EMA that month and a
          close below always lowers it.</li>
        <li><strong>EMA trend:</strong> Rising if slope ≥ +{band}; Flat if within ±{band}; Falling if below −{band}.</li>
        <li><strong>12-month low:</strong> lowest monthly low of the last 12 months, including the signal month.
          Distance above it = close ÷ 12-month low − 1.</li>
        <li><strong>Extension:</strong> close ÷ EMA.</li>
        <li><strong>Month in progress:</strong> it can be screened, but its signals are provisional until month-end.
          Backtests use completed months only.</li>
      </ul>
    </Section>

    <Section id="rules-eligible" title="4. Eligibility (both setups)">
      <ul>
        <li>At least <V k="min_history"/> months of price history.</li>
        <li>Median daily turnover (about 6 months) of at least <V k="min_turnover_cr" f={crore}/>.</li>
        <li>A close price in the signal month.</li>
      </ul>
    </Section>

    <Section id="rules-a" title="5. Setup A · EMA reclaim (all must hold)">
      <ol>
        <li><strong>Reclaim:</strong> last month’s close is below last month’s EMA, and this month’s close is above this
          month’s EMA.</li>
        <li><strong>Strength:</strong> this month’s close ÷ last month’s close − 1 ≥ <V k="min_ret" f={pct}/>.</li>
        <li><strong>Closing range:</strong> (close − low) ÷ (high − low) of the month ≥ <V k="min_cr"/>.</li>
        <li><strong>EMA trend:</strong> Rising, or Flat when “Allow flat EMA” is on (currently <V k="allow_flat" f={onOff}/>).
          Falling is always rejected.</li>
        <li>Plus the eligibility rules and the common filters.</li>
      </ol>
    </Section>

    <Section id="rules-b" title="6. Setup B · Pullback reversal (all must hold)">
      <ol>
        <li><strong>Recent high:</strong> the highest monthly close of the 12 months before the signal month was made
          2 to {(p.b_kmax ?? 0) + 1} months before it, which leaves 1 to <V k="b_kmax"/> pullback months between the high and
          the signal month. If last month is itself the high, there is no pullback and the stock is not a Setup B candidate.
          If two months tie for the high, the more recent one counts.</li>
        <li><strong>Closes above the EMA:</strong> every monthly close from the high month to last month is at or above
          that month’s EMA.</li>
        <li><strong>EMA rising:</strong> slope ≥ +{band}. Flat is not allowed for Setup B.</li>
        <li><strong>Low-volume pullback</strong> (currently <V k="b_use_vol_dry" f={onOff}/>): average daily volume of the pullback
          months ≤ <V k="b_vol_dry" f={times}/> the average of the 12 months ending at the high month (at least 6 months of
          volume needed).</li>
        <li><strong>Volume on the reversal</strong> (currently <V k="b_use_vol_exp" f={onOff}/>): the signal month’s average daily
          volume ≥ the pullback average. In a month in progress this uses only the days traded so far.</li>
        <li><strong>Low near the EMA:</strong> the signal month’s low is between <V k="b_low_lo" f={signedPct}/> and{' '}
          <V k="b_low_hi" f={signedPct}/> of the signal month’s own EMA (the value plotted on a monthly chart).</li>
        <li><strong>Reversal candle:</strong> close above the EMA, month return ≥ <V k="b_min_ret" f={pct}/>, closing
          range ≥ <V k="b_min_cr"/>.</li>
        <li>Plus the eligibility rules and the common filters.</li>
      </ol>
    </Section>

    <Section id="rules-common" title="7. Common filters (both setups)">
      <ul>
        <li><strong>Off the low:</strong> distance above the 12-month low ≥ <V k="min_dist_low" f={pct}/>.</li>
        <li><strong>Not stretched:</strong> close ÷ EMA ≤ {ext}. A stock above the extension limit
          would be an immediate sell, so it is not bought.</li>
      </ul>
    </Section>

    <Section id="rules-rank" title="8. Ranking">
      <ul>
        <li>Signals are ranked by setup first: <strong>B · Pullback</strong>, then <strong>A · Rising</strong>, then
          <strong> A · Flat</strong>.</li>
        <li>Within each group, a higher close ÷ 12-month low ranks first.</li>
        <li>The backtest fills free portfolio slots from the top of this ranking.</li>
      </ul>
    </Section>

    <Section id="rules-screener" title="9. Screener page">
      <ul>
        <li><strong>Signal month:</strong> defaults to the latest completed month. Choosing the month in progress gives
          provisional signals.</li>
        <li><strong>Signals</strong> lists stocks that pass every active rule. <strong>All candidates</strong> adds the
          near-misses: Setup A stocks that crossed above the EMA, and Setup B stocks whose high, pullback and above-EMA
          closes are in place and whose low reached the EMA zone. The Reason column lists every rule a stock failed.</li>
        <li><strong>Counts:</strong> Eligible stocks, Buy signals, B · Pullback, and A · Reclaim (Rising + Flat).</li>
        <li><strong>Export Excel</strong> (next to the Signals / All candidates switch) downloads the signal stocks only
          (stocks that passed every active rule), with every column, in rank order. It is greyed out when the month has
          no signals. The search box and the All candidates view do not change the download.</li>
        <li><strong>Signal frequency:</strong> monthly signal counts by setup for the 36 months up to the signal month.</li>
        <li><strong>Check exits:</strong> for each holding on the signal month’s close: “Exit · Close &lt; EMA” if the close
          is below the EMA; “Exit · Extension limit” if close ÷ EMA is above {ext}; otherwise “Hold”.</li>
        <li><strong>Chart:</strong> opens the top row of the screen (RELIANCE if the screen is empty). Daily (last 2,000
          sessions), weekly (last 520 weeks) and monthly candles with the EMA at the current span; the monthly view also
          shows the 12-month low.</li>
      </ul>
    </Section>

    <Section id="rules-backtest" title="10. Backtest">
      <ul>
        <li><strong>Period:</strong> signals are taken at each month-end from the first signal month up to the month
          before the final valuation month. The final month is only valued.</li>
        <li><strong>Start:</strong> <V k="capital" f={rupees}/> in cash and <V k="slots"/> slots.</li>
        <li><strong>Each month-end close:</strong> first, exits are decided for holdings (close below the EMA, or close ÷ EMA
          above {ext}). Then free slots are filled from the ranked signals. Stocks already held are
          skipped. When every slot is full, new signals are ignored.</li>
        <li><strong>Position size:</strong> the target for a new position is current equity ÷ slots. If cash is short,
          the available cash is split equally among that month’s new entries. If that comes to less than{' '}
          <V k="min_alloc_frac" f={pct}/> of a target slot, nothing is bought that month. Positions are never resized.</li>
        <li><strong>Execution:</strong> decisions use the month-end close; trades happen <V k="execution" f={execName}/>.
          For a next-open exit with no open price, the close is used. A stock without a next open price is not bought.</li>
        <li><strong>Costs:</strong> <V k="cost" f={pct}/> per side, on every buy and sell.</li>
        <li><strong>Idle cash</strong> earns <V k="cash_yield" f={pct}/> a year, compounded monthly.</li>
        <li><strong>Holding:</strong> a position stays until an exit rule fires. If a stock’s price data stops, it is sold at
          its last close. An exit that triggers in the final month is marked pending and valued at that close.</li>
        <li><strong>Trade return:</strong> sale proceeds after costs ÷ amount invested − 1.</li>
        <li><strong>All signals:</strong> every signal treated as its own equal-sized trade with no slot limit, using the
          same entry and exit rules. Return = exit price ÷ entry price × (1 − cost)² − 1. It shows the raw edge of the
          signal, independent of which signals fitted into the slots.</li>
        <li><strong>Report tabs:</strong> Monthly (portfolio value, cash, holdings, return, share invested), Annual
          (strategy’s calendar-year returns; the first year runs from the start month), Trades, All signals, Allocation
          (holdings count and new entries each month) and Funnel (stocks passing each rule each month).</li>
      </ul>
    </Section>

    <Section id="rules-stats" title="11. Benchmarks and statistics">
      <ul>
        <li><strong>Nifty 50 and Nifty 500:</strong> {bench === 'PRICE' ? 'price index' : 'Total Return Index'} from
          niftyindices.com (Settings → Benchmarks). If the Total Return Index cannot be fetched, the niftyindices price
          index is used, and if that also fails, Yahoo Finance (^NSEI, ^CRSLDX; price only). The source used is listed
          under “Benchmark sources”.</li>
        <li><strong>Equal-weight universe:</strong> average monthly return of every stock in the selected universe,
          rebalanced monthly. It carries the same survivorship bias as the strategy, so it is the fairest comparison.</li>
        <li>All lines are rebased to 100 at the first month.</li>
        <li><strong>CAGR</strong> (shown for one year or more); <strong>Volatility</strong> = standard deviation of monthly
          returns × √12; <strong>Sharpe</strong> = (average monthly return × 12 − cash yield) ÷ volatility;
          <strong> Sortino</strong> uses the volatility of negative months only; <strong>Max drawdown</strong> and
          <strong> Longest drawdown</strong> on month-end values; <strong>Calmar</strong> = CAGR ÷ |max drawdown|;
          best and worst month; share of positive months.</li>
        <li><strong>Against each benchmark:</strong> beta, annualised alpha, correlation, up capture and down capture
          (average strategy return in the benchmark’s up or down months ÷ the benchmark’s), and share of months beating it.</li>
        <li><strong>Trade statistics</strong> (closed trades): win rate, average win and loss, payoff ratio, profit factor
          (on ₹ P&amp;L), expectancy, median trade, average months held, and the top 10 trades’ share of total profit.</li>
      </ul>
    </Section>

    <Section id="rules-limits" title="12. Known limitations">
      <ul>
        <li><strong>Survivorship bias:</strong> today’s constituent list is used for every past month. Stocks that failed
          and left the index are missing, which flatters the backtest. Treat results as an upper bound.</li>
        <li>Yahoo data for small and microcap stocks can have gaps or bad prints. One-day spikes are removed; other errors
          are not.</li>
        <li>Volume rules use Yahoo’s daily share volume. If a split falls inside the comparison window and Yahoo has not
          adjusted that stock’s volume history, the volume ratio is distorted.</li>
        <li>Signals for a month in progress can change until the month-end close.</li>
      </ul>
      <p className="rules-foot"><BookOpenText size={14}/> Rules output, not investment advice.</p>
    </Section>
  </>
}
