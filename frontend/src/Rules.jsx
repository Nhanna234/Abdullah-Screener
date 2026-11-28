// Rules & logic page.
// KEEP IN SYNC: this page documents engine.py (signals, screener, backtest, statistics),
// engine_weekly.py (weekly reversal from the 10W EMA: monthly scan list, signal, entries, target/stop exits),
// dashboard.py (screener/backtest/chart requests) and data.py (constituents, prices,
// benchmarks). Parameter values are read live from the current settings, and defaults
// come from engine.DEFAULTS + engine_weekly.W_DEFAULTS through dashboard_config(). Whenever the logic in those files
// changes, update the text below and LOGIC_REVIEWED.
import React from 'react'
import { BookOpenText, CircleHelp } from 'lucide-react'

const LOGIC_REVIEWED = '28 Nov 2026'

const pct = v => v == null ? 'off' : `${+(v * 100).toFixed(2)}%`
const signedPct = v => v == null ? 'off' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${+(Math.abs(v) * 100).toFixed(2)}%`
const plain = v => v == null ? 'off' : String(v)
const onOff = v => v ? 'on' : 'off'
const times = v => v == null ? 'off' : `${v}×`
const crore = v => `₹${v} crore`
const rupees = v => '₹' + Number(v).toLocaleString('en-IN')
const setupName = v => ({ B: 'B only', A: 'A only', 'A+B': 'A + B' }[v] || v)
const execName = v => v === 'close' ? 'at the signal month’s close' : 'at the next trading day’s open'
const weekExec = v => v === 'close' ? 'at that Friday’s close' : 'at the next trading day’s open'

const SECTIONS = [
  ['rules-overview', 'Overview'], ['rules-data', 'Universe & data'], ['rules-monthly', 'Monthly candles'],
  ['rules-eligible', 'Eligibility'], ['rules-a', 'Setup A'], ['rules-b', 'Setup B'],
  ['rules-common', 'Common filters'], ['rules-rank', 'Ranking'], ['rules-screener', 'Screener'],
  ['rules-backtest', 'Backtest'], ['rules-stats', 'Benchmarks & statistics'], ['rules-weekly', 'Weekly · 10W EMA'],
  ['rules-limits', 'Limitations'],
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
        <li>Sections 2–11 describe the <strong>Monthly</strong> timeframe. The <strong>Weekly</strong> timeframe (Timeframe
          box above the results) looks for a weekly reversal candle from a rising 10-week EMA among stocks with a monthly
          signal, with a fixed target and stop-loss. It shares the universe, price data and portfolio settings, and is described in section 12.</li>
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


    <Section id="rules-weekly" title="12. Weekly timeframe · Reversal from the 10W EMA">
      <h3>Which stocks are scanned: the monthly list</h3>
      <ul>
        <li>The weekly timeframe does not scan the whole index. It scans only stocks of the selected universe that are on the
          <strong> monthly list</strong>.</li>
        <li><strong>Joining:</strong> a month-end where the stock gives a monthly signal under Setup A (Rising or Flat) or
          Setup B, using the monthly rules and settings in sections 4–7. Both setups count, whatever the Buy setup box
          says for the monthly timeframe.</li>
        <li><strong>Leaving:</strong> a later month-end where the monthly exit fires: a close below the{' '}
          <V k="ema_span"/>-month EMA, or Close ÷ EMA above {ext}. A new monthly signal adds it back.</li>
        <li><strong>No peeking ahead:</strong> each week uses the list as of the last month-end completed before the week
          started. A week starting Monday 27 Oct uses September’s list; a week starting Monday 3 Nov uses October’s.</li>
        <li>Leaving the list only stops new entries. Positions already bought keep their weekly target, stop-loss and EMA
          exits.</li>
        <li>The <strong>Monthly signal</strong> column shows the setup and month of the signal that put the stock on the list.
          The funnel’s first row counts the stocks on the list each week.</li>
      </ul>
      <h3>Weekly candles</h3>
      <ul>
        <li>Built from the same cleaned daily prices. Weeks end on Friday: open = first day’s open, high = highest high,
          low = lowest low, close = last day’s close.</li>
        <li><strong>EMA:</strong> <V k="w_ema"/>-week exponential moving average of weekly closes.</li>
        <li><strong>Turnover:</strong> the median of daily close × volume within each week; the liquidity filter uses the
          median of the last 26 weekly values (at least 13 needed).</li>
        <li>A week in progress can be screened, but its results are provisional until Friday’s close. Backtests end on the
          last completed week.</li>
      </ul>
      <h3>Reversal signal (all must hold; judged at the signal week’s Friday close)</h3>
      <ol>
        <li><strong>Strength:</strong> over the <V k="w_range_weeks"/> weeks before the signal week, the highest weekly high ÷
          the lowest weekly low ≥ <V k="w_range" f={times}/>, and the <strong>low came before the high</strong>, so the move
          was up and not down. If the same price occurs twice, its first occurrence counts.</li>
        <li><strong>EMA rising:</strong> last week’s 10W EMA ÷ the EMA <V k="w_slope_window"/> weeks earlier − 1 ≥{' '}
          <V k="w_min_slope" f={signedPct}/>.</li>
        <li><strong>Consolidation near the EMA:</strong> in the signal week or the{' '}
          {Math.max((p.w_near_weeks ?? 1) - 1, 0)} weeks before it (<V k="w_near_weeks"/> weeks checked), a weekly low between{' '}
          <V k="w_low_lo" f={signedPct}/> and <V k="w_low_hi" f={signedPct}/> of that week’s own 10W EMA. Closes below the
          EMA during the consolidation are allowed.</li>
        <li><strong>Uptrend:</strong> the signal week closes <strong>above</strong> its 10W EMA.</li>
        <li><strong>Reversal candle:</strong> the signal week’s close ÷ last week’s close − 1 is more than{' '}
          <V k="w_min_ret" f={pct}/>, and its closing range is more than <V k="w_min_cr"/>.</li>
        <li><strong>Eligibility:</strong> at least <V k="min_history"/> months of history, counted as{' '}
          {Math.round((p.min_history ?? 0) * 52 / 12)} weekly closes, and a 26-week median daily turnover of at least{' '}
          <V k="min_turnover_cr" f={crore}/>.</li>
      </ol>
      <h3>Entry</h3>
      <ol>
        <li><strong>Buy at signal close:</strong> closing range above <V k="w_cr_close"/>. The buy is made at the signal
          week’s Friday close, whatever the execution setting.</li>
        <li><strong>Buy above signal high:</strong> closing range above <V k="w_min_cr"/> and up to <V k="w_cr_close"/>. In the
          following week only, the first day whose high is above the signal week’s high buys. It fills at that high, or at
          the day’s open if it opens above it. If the high is not crossed that week, the signal expires.</li>
        <li>A stock already held is skipped. If two signals of one stock trigger on the same day, only one entry is
          made.</li>
      </ol>
      <h3>Exit (checked on daily prices, from the exact entry price)</h3>
      <ul>
        <li><strong>Target</strong> = entry × (1 + <V k="w_target" f={pct}/>). <strong>Stop-loss</strong> = entry × (1 −{' '}
          <V k="w_stop" f={pct}/>).</li>
        <li><strong>Entry day:</strong> when the buy filled during the day, only the close is compared (a close at or below the
          stop exits at the stop; at or above the target exits at the target), because the order of the day’s low and high
          against the fill is unknown. When the buy filled at the open, the day’s low and high count.</li>
        <li><strong>Later days:</strong> an open at or below the stop exits at the open; an open at or above the target exits at
          the open; otherwise a low at or below the stop exits at the stop and a high at or above the target exits at the
          target. If both are touched on the same day, the stop is assumed to come first.</li>
        <li><strong>Trend exit:</strong> a weekly close below the 10W EMA (including the entry week) sells{' '}
          <V k="execution" f={weekExec}/>.</li>
        <li>There is no time stop. If a stock’s prices stop for more than 5 trading days before the end of the test, it is sold
          at its last close. An exit due after the last day is marked pending.</li>
      </ul>
      <h3>Weekly backtest</h3>
      <ul>
        <li>Runs day by day from the first trading day of the first signal month to the last completed week that ends in the
          final valuation month. Portfolio value is recorded at each week’s last trading day.</li>
        <li>Each day, exits are made before entries. New entries that day are ranked by the signal week’s close ÷ 26-week
          low; free slots are filled from the top.</li>
        <li>Position size, the <V k="min_alloc_frac" f={pct}/> minimum, <V k="slots"/> slots, <V k="capital" f={rupees}/>{' '}
          starting cash and <V k="cost" f={pct}/> cost per side work as in the monthly backtest. Equity for sizing uses the
          previous day’s closes. Idle cash earns <V k="cash_yield" f={pct}/> a year, compounded daily (252 days).</li>
        <li><strong>All signals:</strong> every entry as its own equal trade with no slot limit, one position per stock at a
          time.</li>
        <li>Benchmarks and the equal-weight universe use Friday closes. Statistics are annualised on 52 weeks (volatility
          × √52), the rolling chart shows 52-week returns, and the monthly labels become weekly (Weekly tab, best and worst
          week, positive weeks, weeks beating). Trade statistics add average weeks held and the count of target, stop-loss
          and EMA exits.</li>
      </ul>
      <h3>Weekly screener</h3>
      <ul>
        <li><strong>Signal week:</strong> the week containing the chosen date; defaults to the latest completed week.</li>
        <li><strong>TRIGGERED:</strong> an entry happened during the week: a signal this week with a closing range above{' '}
          <V k="w_cr_close"/> (bought at the close), or a break of last week’s signal high. Trigger = the fill price, with its
          stop-loss and target. Week return and closing range show the signal candle’s values.</li>
        <li><strong>BUY STOP:</strong> a reversal signal this week with a closing range up to <V k="w_cr_close"/>: buy above
          this week’s high next week. Trigger, stop-loss and target are worked out from that high.</li>
        <li><strong>WATCH:</strong> the trend rules (monthly list, strength, rising EMA) pass for next week and the low was near
          the 10W EMA this week or last week. It needs a reversal week next week to signal.</li>
        <li><strong>Rejected</strong> (All candidates only): on the monthly list and near the EMA this week, but no signal. The
          Reason column lists every rule that failed.</li>
        <li>Rows are ranked by status, then by close ÷ 26-week low. Each stock appears once. <strong>Export Excel</strong>{' '}
          downloads every row that is not Rejected.</li>
        <li><strong>Check exits:</strong> “Exit · Weekly close &lt; 10W EMA” or “Hold”. Target and stop-loss need your own entry
          price, so they are not checked here.</li>
        <li><strong>Chart:</strong> opens on weekly candles with the <V k="w_ema"/>-week EMA and marks the signal week.</li>
      </ul>
    </Section>

    <Section id="rules-limits" title="13. Known limitations">
      <ul>
        <li><strong>Survivorship bias:</strong> today’s constituent list is used for every past month. Stocks that failed
          and left the index are missing, which flatters the backtest. Treat results as an upper bound.</li>
        <li>Yahoo data for small and microcap stocks can have gaps or bad prints. One-day spikes are removed; other errors
          are not.</li>
        <li>Volume rules use Yahoo’s daily share volume. If a split falls inside the comparison window and Yahoo has not
          adjusted that stock’s volume history, the volume ratio is distorted.</li>
        <li>Signals for a month in progress can change until the month-end close.</li>
        <li><strong>Weekly:</strong> daily candles do not show the order of moves within a day. Moves on the same
          day cannot be ordered, so a stop and target hit on the same day assume the stop came first. Stops and buy
          triggers fill exactly at their level unless the day opens beyond it; real fills can be worse in fast markets.</li>
      </ul>
      <p className="rules-foot"><BookOpenText size={14}/> Rules output, not investment advice.</p>
    </Section>
  </>
}
