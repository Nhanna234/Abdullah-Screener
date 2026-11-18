import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, BarChart3, BookOpenText, CalendarDays,
  ChartNoAxesCombined, ChevronDown, CircleHelp, FileSpreadsheet, Filter, Layers3,
  Menu, Play, ScanSearch, Settings2, ShieldCheck, Upload, X,
} from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { streamlitRequest } from './streamlit.js'
import ChartPreview from './ChartPreview.jsx'
import Rules from './Rules.jsx'
import './style.css'

const INDEXES = ['Nifty 50', 'Nifty 100', 'Nifty 200', 'Nifty LargeMidcap 250',
  'Nifty Smallcap 100', 'Nifty Smallcap 250', 'Nifty 500', 'Nifty Microcap 250', 'Nifty Total Market']
const DEFAULTS = {
  ema_span: 12, min_ret: .05, min_cr: .8, min_dist_low: .3, low_lookback: 12,
  slope_window: 6, flat_band: .03, allow_flat: true, exit_ext: 1.7, min_history: 24,
  min_turnover_cr: 1, slots: 10, min_alloc_frac: .2, cost: .004, cash_yield: .06,
  execution: 'next_open', capital: 1000000, setups: 'B', b_kmax: 4, b_low_lo: -.1,
  b_low_hi: .05, b_min_ret: .05, b_min_cr: .6, b_use_vol_dry: true,
  b_vol_dry: .8, b_use_vol_exp: false,
}
const BENCH_COLORS = { Strategy: '#107457', 'Nifty 50': '#849991', 'Nifty 500': '#bf8b3e', 'Equal-weight universe': '#7587b7' }
const SIGNAL_COLORS = { 'B · Pullback': '#107457', 'A · Rising': '#b28549', 'A · Flat': '#7587b7' }
const num = (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString('en-IN', { maximumFractionDigits: digits }) : '—'
const pct = value => Number.isFinite(value) ? (value * 100).toFixed(1) + '%' : '—'
const rupee = value => Number.isFinite(value) ? '₹' + num(value, 0) : '—'
const monthLabel = value => value ? new Date(value + '-01T00:00:00Z').toLocaleDateString('en-GB',
  { month: 'short', year: 'numeric', timeZone: 'UTC' }) : '—'
const percentPattern = /return|drawdown|volatility|cash %|exposure|expectancy|slope|above 12m low|low vs ema|win rate|avg win|avg loss|positive months|months beating|alpha|share of profit/i
function format(value, key) {
  if (value == null) return '—'
  if (typeof value !== 'number') return String(value)
  if (['Cash','Value','Invested','P&L'].includes(key)) return rupee(value)
  if (key === 'Year') return String(value)
  return percentPattern.test(key) ? pct(value) : num(value)
}

function Field({ label, children, wide = false }) {
  return <label className={'field' + (wide ? ' wide' : '')}><span>{label}</span>{children}</label>
}
function Select({ value, change, children }) {
  return <span className="select"><select value={value} onChange={e => change(e.target.value)}>{children}</select><ChevronDown size={14}/></span>
}
function Tabs({ options, value, change }) {
  return <div className="segmented">{options.map(key => <button type="button" key={key} className={value === key ? 'active' : ''} aria-pressed={value === key} onClick={() => change(key)}>{key}</button>)}</div>
}
function Card({ icon: Icon, label, value, note, tone }) {
  return <article className="metric">
    <div className="metric-top"><span>{label}</span><div className={'metric-icon ' + (tone || '')}><Icon size={17}/></div></div>
    <strong>{value}</strong><small>{note}</small>
  </article>
}
function Panel({ title, hint, action, children, className = '' }) {
  return <section className={'panel ' + className}><div className="panel-head">
    <div><h2>{title}</h2>{hint && <p>{hint}</p>}</div>{action}
  </div>{children}</section>
}
function Table({ rows, columns, pick }) {
  if (!rows?.length) return <div className="empty">No matching rows.</div>
  const keys = columns || Array.from(new Set(rows.flatMap(Object.keys)))
  return <div className="table-scroll"><table><thead><tr>{keys.map(key => <th key={key} scope="col">{key}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>{keys.map(key => <td key={key}>
      {key === 'Symbol' && pick ? <button type="button" className="symbol-button" onClick={() => pick(row.Symbol)}>{row.Symbol}</button>
        : key === 'Status' ? <span className={'status ' + (row.Status === 'SIGNAL' ? 'passes' : '')}>{row.Status}</span>
          : format(row[key], row.Metric || key)}
    </td>)}</tr>)}</tbody></table></div>
}
function Plot({ rows, palette, tick = num, bars = false }) {
  const Chart = bars ? BarChart : LineChart
  const [hidden, setHidden] = useState([])
  const keys = Object.keys(palette).filter(key => key in (rows?.[0] || {}))
  if (!rows?.length || !keys.length) return <div className="empty">No data for this chart.</div>
  return <>
    <div className="legend">{keys.map(key => <button type="button" key={key} aria-pressed={!hidden.includes(key)} className={hidden.includes(key) ? 'muted' : ''} onClick={() => setHidden(old => old.includes(key) ? old.filter(k => k !== key) : [...old, key])}><i style={{ background: palette[key] }}/>{key}</button>)}</div>
    <div className="plot"><ResponsiveContainer width="100%" height="100%">
      <Chart data={rows} margin={{ left: -9, top: 13, right: 17, bottom: 4 }}>
        <CartesianGrid stroke="#e4ebe7" vertical={false} strokeDasharray="4 4"/>
        <XAxis dataKey="Month" tickFormatter={monthLabel} tickLine={false} axisLine={false} minTickGap={36} tick={{ fill: '#63746b', fontSize: 11 }} tickMargin={10}/>
        <YAxis tickFormatter={value => tick(value)} tickLine={false} axisLine={false} tick={{ fill: '#63746b', fontSize: 11 }} width={66} allowDecimals={!bars} domain={bars ? [0,'auto'] : ['auto','auto']}/>
        <Tooltip labelFormatter={monthLabel} formatter={value => tick(value)} contentStyle={{ border: '1px solid #dce6df', borderRadius: 10 }}/>
        {keys.filter(key => !hidden.includes(key)).map((key, i) => bars
          ? <Bar key={key} dataKey={key} fill={palette[key]} stackId="signals" maxBarSize={26} isAnimationActive={false}/>
          : <Line key={key} type="linear" dataKey={key} stroke={palette[key]} strokeWidth={i === 0 ? 3 : 2} dot={false} isAnimationActive={false}/>)}
      </Chart>
    </ResponsiveContainer></div>
  </>
}

function Settings({ close, params, setParams, draft, update }) {
  const put = (key, value) => setParams(old => ({ ...old, [key]: value }))
  const number = (key, label, min, max, step = 1, scale = 1, fallback = null) => <Field label={label}>
    <input type="number" min={min} max={max} step={step} value={(params[key] ?? fallback) * scale} onChange={event => put(key, +event.target.value / scale)}/>
  </Field>
  const toggle = (key, label) => <label className="check"><input type="checkbox" checked={params[key]} onChange={event => put(key, event.target.checked)}/>{label}</label>
  return <div className="overlay" onClick={close}><aside className="drawer" role="dialog" aria-modal="true" aria-label="Strategy settings" onClick={event => event.stopPropagation()}>
    <div className="drawer-head"><div><h2>Strategy settings</h2><p>Applied on the next run.</p></div><button type="button" className="icon-button" aria-label="Close settings" onClick={close}><X size={20}/></button></div>
    <h3>Universe & prices</h3>
    <label className="file"><Upload size={18}/><div><strong>{draft.csvName || 'Override index constituents'}</strong><small>CSV with a Symbol column</small></div><input type="file" accept=".csv,text/csv" onChange={async event => {
      const file = event.target.files[0]
      if (file) update({ constituents: await file.text(), csvName: file.name })
    }}/></label>
    {draft.csvName && <button type="button" className="link-button" onClick={() => update({ constituents: '', csvName: '' })}>Remove uploaded CSV</button>}
    <label className="check"><input type="checkbox" checked={!!draft.refresh} onChange={event => update({ refresh: event.target.checked })}/>Refresh prices on next run</label>
    <h3>Shared rules</h3><div className="setting-grid">
      {number('ema_span','EMA span (months)',3,36)}{number('slope_window','Slope window (months)',2,12)}
      {number('flat_band','Flat EMA band (%)',0,20,.5,100)}{number('min_dist_low','Min. above 12-month low (%)',0,500,5,100)}
      {number('min_history','Min. history (months)',12,120)}{number('min_turnover_cr','Min. daily turnover (₹ crore)',0,100,.5)}
      <label className="check"><input type="checkbox" checked={params.exit_ext != null} onChange={event => put('exit_ext',event.target.checked ? 1.7 : null)}/>Use extension limit</label>
      {number('exit_ext','Extension limit (× EMA)',1.1,5,.05,1,1.7)}
    </div>
    <h3>A · EMA reclaim</h3><div className="setting-grid">
      {number('min_ret','Min. monthly return (%)',0,100,1,100)}{number('min_cr','Min. closing range',0,1,.05)}{toggle('allow_flat','Allow flat EMA')}
    </div>
    <h3>B · Pullback reversal</h3><div className="setting-grid">
      {number('b_kmax','Max. pullback months',1,6)}{number('b_min_ret','Min. monthly return (%)',0,100,1,100)}
      {number('b_min_cr','Min. closing range',0,1,.05)}{number('b_low_lo','Low vs EMA, min. (%)',-50,0,1,100)}
      {number('b_low_hi','Low vs EMA, max. (%)',0,50,1,100)}{number('b_vol_dry','Max. pullback volume ratio',.1,2,.05)}
      {toggle('b_use_vol_dry','Require lower pullback volume')}{toggle('b_use_vol_exp','Require reversal volume expansion')}
    </div>
    <h3>Portfolio & benchmarks</h3><div className="setting-grid">
      {number('slots','Portfolio slots',1,50)}{number('capital','Starting capital (₹)',10000,1e9,100000)}
      {number('cost','Trading cost per side (%)',0,2,.05,100)}{number('cash_yield','Idle cash yield (% p.a.)',0,12,.5,100)}
      <Field label="Execution"><Select value={params.execution} change={value => put('execution',value)}><option value="next_open">Next trading day’s open</option><option value="close">Signal month close</option></Select></Field>
      <Field label="Benchmarks"><Select value={draft.bench} change={bench => update({bench})}><option value="TRI">Total return index</option><option value="PRICE">Price index</option></Select></Field>
    </div>
    <button type="button" className="primary drawer-done" onClick={close}>Done <ArrowRight size={16}/></button>
  </aside></div>
}

function Screener({ report, holdings, setHoldings, busy, scan, requestChart }) {
  const [view, setView] = useState('Signals')
  const [ticker, setTicker] = useState('RELIANCE')
  const [selectedChart, setSelectedChart] = useState(null)
  useEffect(() => {
    setSelectedChart(null)
    if (report?.preview) setTicker(report.preview.symbol)
  }, [report])
  const chart = selectedChart || report?.preview
  async function loadChart(value) {
    const symbol = value.trim().toUpperCase()
    if (!symbol || busy) return
    setTicker(symbol)
    const result = await requestChart(symbol, report?.month, report?.preview?.emaSpan)
    if (result) setSelectedChart(result)
  }
  const [query, setQuery] = useState('')
  const rows = report?.candidates.filter(row =>
    (view === 'All candidates' || row.Status === 'SIGNAL') &&
    [row.Symbol,row.Company,row.Industry].some(value => value?.toLowerCase().includes(query.trim().toLowerCase())),
  )
  const counts = report?.counts || {}
  return <>
    <div className="section-header"><div><h2>Signal overview</h2><p>{report ? monthLabel(report.month) + (report.provisional ? ' · Provisional' : '') : 'Run the screener to see qualifying stocks'}</p></div></div>
    <div className="metrics">
      <Card icon={Layers3} label="Eligible stocks" value={num(counts.Eligible, 0)} note="History and turnover filters"/>
      <Card icon={ScanSearch} label="Buy signals" value={num(counts.Signals, 0)} note="Passed every active rule"/>
      <Card icon={ArrowUpRight} label="B · Pullback" value={num(counts['B · Pullback'], 0)} note="Reversal near a rising EMA" tone="amber"/>
      <Card icon={Activity} label="A · Reclaim" value={report ? num(counts['A · Rising'] + counts['A · Flat'], 0) : '—'} note="Monthly close reclaimed the EMA" tone="slate"/>
    </div>
    {report ? <>
      <Panel title="Screener results" hint={rows.length + ' matches'} action={<Tabs options={['Signals','All candidates']} value={view} change={setView}/>}>
        <input className="search" aria-label="Search screened stocks" placeholder="Search symbol, company, or industry" value={query} onChange={event => setQuery(event.target.value)}/>
        <Table rows={rows} columns={['Rank','Symbol','Company','Status','Setup','Month return','Closing range','Close/EMA','Reason']} pick={loadChart}/>
      </Panel>
    </> : <div className="two-panels">
      <Panel title="A · EMA reclaim" hint="From below the average to a strong close"><div className="setup"><Activity size={25}/><p>The stock crosses back above a flat or rising EMA and closes near the top of its monthly range.</p></div></Panel>
      <Panel title="B · Pullback reversal" hint="A low-volume pullback within an uptrend"><div className="setup amber"><ArrowUpRight size={25}/><p>The stock pulls back toward a rising EMA, then posts a strong reversal. Setup B ranks first when both setups are enabled.</p></div></Panel>
    </div>}
    <ChartPreview data={chart} ticker={ticker} setTicker={setTicker} load={loadChart} busy={busy}
      shortlisted={report?.candidates.filter(row => row.Status === 'SIGNAL').map(row => row.Symbol) || []} signal={chart?.month === report?.month && !!report?.candidates.some(row => row.Symbol === chart.symbol && row.Status === 'SIGNAL')}/>
    {report && <Panel title="Signal frequency" hint="Monthly signal counts, stacked by setup"><Plot rows={report.funnel} palette={SIGNAL_COLORS} bars/></Panel>}
    <Panel title="Check exits" hint="Enter holdings to check their close and EMA on the selected signal month.">
      <div className="exit-form"><input aria-label="Holdings tickers" placeholder="RELIANCE, TCS, INFY" value={holdings} onChange={event => setHoldings(event.target.value)}/>
        <button className="secondary-button" type="button" disabled={busy || !holdings.trim()} onClick={scan}><ShieldCheck size={16}/>Check holdings</button></div>
      {!!report?.exits?.length && <Table rows={report.exits}/>}
    </Panel>
  </>
}

const REPORT_TABS = {
  Performance: ['metrics', 'Performance vs benchmarks'],
  Monthly: ['monthly', 'Monthly portfolio'],
  Annual: ['annual', 'Calendar-year returns'],
  Trades: ['trades', 'Portfolio trades'],
  'All signals': ['allSignals', 'Signal trades without slot limits'],
  Allocation: ['allocation', 'Portfolio positions by month'],
  Funnel: ['funnel', 'Monthly signal funnel'],
}
function Backtest({ report, download, exporting }) {
  const [section, setSection] = useState('Performance')
  const [chart, setChart] = useState('Growth')
  const metrics = Object.fromEntries((report?.metrics || []).map(row => [row.Metric,row.Strategy]))
  const chartKey = { Growth: 'growth', Drawdown: 'drawdown', Rolling: 'rolling' }[chart]
  const [tableKey, title] = REPORT_TABS[section]
  if (!report) return <Panel title="Backtest results" hint="Choose a first signal month and a final valuation month.">
    <div className="empty backtest-empty"><BarChart3 size={26}/> Run a backtest to compare the strategy against NSE benchmarks.</div>
  </Panel>
  return <>
    <div className="section-header"><div><h2>Performance overview</h2><p>{monthLabel(report.first)} – {monthLabel(report.last)}</p></div>
      <button type="button" className="secondary-button" disabled={exporting} onClick={download}><ArrowDownToLine size={16}/>{exporting ? 'Exporting…' : 'Export Excel'}</button>
    </div>
    <div className="metrics">
      <Card icon={ChartNoAxesCombined} label="Total return" value={pct(metrics['Total return'])} note="After trading costs"/>
      <Card icon={ArrowUpRight} label="CAGR" value={pct(metrics.CAGR)} note="Annualized growth"/>
      <Card icon={Activity} label="Max drawdown" value={pct(metrics['Max drawdown'])} note="Peak-to-trough decline" tone="amber"/>
      <Card icon={Layers3} label="Portfolio trades" value={num(report.trades.length, 0)} note="Open and closed positions" tone="slate"/>
    </div>
    <Panel title={{Growth:'Growth of ₹100',Drawdown:'Drawdown',Rolling:'Rolling 12-month return'}[chart]} hint="Month-end strategy and benchmark performance" action={<Tabs options={['Growth','Drawdown','Rolling']} value={chart} change={setChart}/>}>
      <Plot rows={report[chartKey]} palette={BENCH_COLORS} tick={chart === 'Growth' ? rupee : pct}/>
    </Panel>
    <div className="analysis"><div className="analysis-nav" aria-label="Backtest report">{Object.keys(REPORT_TABS).map(label =>
      <button type="button" key={label} className={section === label ? 'active' : ''} aria-pressed={section === label} onClick={() => setSection(label)}>{label}</button>)}</div>
      <Panel title={title}><Table rows={report[tableKey]}/></Panel>
    </div>
    <details className="panel details"><summary>Trade statistics</summary><Table rows={report.tradeMetrics}/></details>
    <details className="panel details"><summary>Benchmark sources</summary><Table rows={Object.entries(report.sources).map(([Benchmark,Source])=>({Benchmark,Source}))}/></details>
  </>
}

function App() {
  const [mode, setMode] = useState('screener')
  const [config, setConfig] = useState({ indices: INDEXES, latest: '' })
  const [draft, setDraft] = useState({ index: 'Nifty Total Market', signal: '', first: '2025-01', last: '', bench: 'TRI' })
  const [params, setParams] = useState(DEFAULTS)
  const [holdings, setHoldings] = useState('')
  const [settings, setSettings] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [results, setResults] = useState({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const update = patch => setDraft(old => ({ ...old, ...patch }))
  const report = results[mode]
  const onScreener = mode === 'screener'
  const onRules = mode === 'rules'

  useEffect(() => {
    if (!streamlitRequest) {
      setError('Open this dashboard with streamlit run app.py.')
      return
    }
    streamlitRequest('config').then(data => {
      setConfig(data)
      setParams(data.params)
      update({ signal: data.latest, last: data.latest })
    })
  }, [])
  useEffect(() => {
    const close = event => { if (event.key === 'Escape') { setSettings(false); setMobileNav(false) } }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [])

  async function submit(event) {
    event?.preventDefault()
    if (busy || !streamlitRequest) return
    setError('')
    if (!onScreener && (!draft.first || !draft.last || draft.first >= draft.last)) {
      setError('The first signal month must be before the final valuation month.')
      return
    }
    setBusy(mode)
    try {
      const result = await streamlitRequest('run', { ...draft, params, holdings, mode })
      setResults(previous => ({ ...previous, [mode]: result }))
      if (draft.refresh) update({ refresh: false })
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }
  async function requestChart(symbol, signalMonth, emaSpan) {
    setBusy('chart')
    setError('')
    try {
      return await streamlitRequest('chart', { symbol, signal: signalMonth || draft.signal, emaSpan: emaSpan ?? params.ema_span })
    } catch (failure) {
      setError(failure.message)
      return null
    } finally {
      setBusy('')
    }
  }
  async function download() {
    setBusy('export')
    setError('')
    try {
      const { xlsx } = await streamlitRequest('export', { report })
      const data = Uint8Array.from(atob(xlsx), character => character.charCodeAt(0))
      const url = URL.createObjectURL(new Blob([data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }))
      const link = document.createElement('a')
      link.href = url
      link.download = 'EMA Reclaim Lab Backtest.xlsx'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy('')
    }
  }
  const switchMode = value => { setMode(value); setError(''); setMobileNav(false) }
  const openSettings = () => { setSettings(true); setMobileNav(false) }
  return <div className="shell">
    {mobileNav && <button type="button" className="nav-shade" aria-label="Close navigation" onClick={() => setMobileNav(false)}/>}
    <aside className={'sidebar ' + (mobileNav ? 'shown' : '')}>
      <div className="brand"><div className="brand-icon"><ChartNoAxesCombined size={22} strokeWidth={2.3}/></div><strong>EMA Reclaim Lab</strong></div>
      <div className="nav-label">WORKSPACE</div>
      <nav>
        <button type="button" className={onScreener ? 'selected' : ''} onClick={() => switchMode('screener')}><ScanSearch size={19}/>Screener<ArrowRight className="nav-arrow" size={15}/></button>
        <button type="button" className={mode === 'backtest' ? 'selected' : ''} onClick={() => switchMode('backtest')}><BarChart3 size={19}/>Backtest<ArrowRight className="nav-arrow" size={15}/></button>
        <button type="button" className={onRules ? 'selected' : ''} onClick={() => switchMode('rules')}><BookOpenText size={19}/>Rules<ArrowRight className="nav-arrow" size={15}/></button>
      </nav>
      <div className="nav-label secondary">CONFIGURATION</div>
      <nav><button type="button" onClick={openSettings}><Settings2 size={19}/>Strategy settings</button></nav>
      <div className="sidebar-foot"><div className="exchange-icon"><Activity size={17}/></div><div><strong>NSE equities</strong><small>Monthly signals</small></div></div>
    </aside>
    <main>
      <header className="topbar"><div className="breadcrumb">
        <button type="button" className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={20}/></button>
        <span>Workspace</span><span className="slash">/</span><strong>{onScreener ? 'Screener' : onRules ? 'Rules' : 'Backtest'}</strong>
      </div><button type="button" className="secondary-button small" onClick={openSettings}><Settings2 size={16}/>Settings</button></header>
      <div className="content">
        <section className="hero">
          <div className="hero-copy"><h1>{onScreener ? 'EMA signal screener' : onRules ? 'Rules & logic' : 'Strategy backtest'}</h1>
            <p>{onScreener ? 'Find monthly EMA reclaims and pullback reversals across NSE equities.' : onRules ? 'How the screener and backtest decide, with your current settings.' : 'Measure how monthly signals translate into a slot-based portfolio.'}</p>
          </div>
          <div className="hero-mark" aria-hidden="true"><ChartNoAxesCombined size={144} strokeWidth={.75}/></div>
          <div className="hero-badge"><CalendarDays size={16}/><span>Latest completed month<strong>{monthLabel(config.latest)}</strong></span></div>
        </section>
        {!onRules && <form className="controls ema-controls" onSubmit={submit}>
          <Field label="Stock universe"><Select value={draft.index} change={index => update({index})}>{config.indices.map(index => <option key={index}>{index}</option>)}</Select></Field>
          <Field label="Buy setup"><Select value={params.setups} change={setups => setParams(old => ({...old,setups}))}><option value="B">B · Pullback reversal</option><option value="A">A · EMA reclaim</option><option value="A+B">Both setups</option></Select></Field>
          {onScreener ? <Field label="Signal month"><input type="month" required value={draft.signal} onChange={event => update({signal:event.target.value})}/></Field> : <>
            <Field label="First signal month"><input type="month" required value={draft.first} onChange={event => update({first:event.target.value})}/></Field>
            <Field label="Final valuation month"><input type="month" required value={draft.last} max={config.latest} onChange={event => update({last:event.target.value})}/></Field>
          </>}
          <button className="primary run" type="submit" disabled={!!busy || !config.latest}><Play size={15} fill="currentColor"/>{busy === mode ? 'Running…' : onScreener ? 'Run screener' : 'Run backtest'}</button>
          {draft.csvName && <div className="attached"><FileSpreadsheet size={15}/>{draft.csvName}</div>}
        </form>}
        {!!busy && !['export','chart'].includes(busy) && <div className="notice" role="status"><Activity size={17}/>Fetching market data and calculating results. The first load can take a few minutes.</div>}
        {error && <div className="notice error" role="alert">{error}</div>}
        {report && <div className="report-meta"><Filter size={15}/><strong>{report.index}</strong><span>{report.covered} / {report.constituents} stocks covered</span><span>Prices to {report.asOf}</span><span>{report.source.startsWith('http') ? 'NSE constituent list' : report.source}</span></div>}
        {report?.notices.map(message => <div key={message} className="notice"><CircleHelp size={16}/>{message}</div>)}
        {onRules
          ? <Rules params={params} defaults={config.params} indices={config.indices} bench={draft.bench}/>
          : onScreener
          ? <Screener report={report} holdings={holdings} setHoldings={setHoldings} busy={busy} scan={() => submit()} requestChart={requestChart}/>
          : <Backtest report={report} download={download} exporting={busy === 'export'}/>}
        <footer><span>EMA Reclaim Lab</span><span>Signals and portfolio research</span></footer>
      </div>
    </main>
    {settings && <Settings close={() => setSettings(false)} draft={draft} update={update} params={params} setParams={setParams}/>}
  </div>
}

createRoot(document.getElementById('root')).render(<App/>)
