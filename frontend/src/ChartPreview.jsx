import React, { useEffect, useId, useRef, useState } from 'react'
import { CandlestickSeries, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers } from 'lightweight-charts'
import { CandlestickChart, Minus, MousePointer2, Plus, RotateCcw, Ruler, Trash2, TrendingUp, Undo2 } from 'lucide-react'

const PERIODS = { '1D': 'Daily', '1W': 'Weekly', '1M': 'Monthly' }
const RANGES = {
  '1D': { '6M': 126, '1Y': 252, '3Y': 756 },
  '1W': { '6M': 26, '1Y': 52, '3Y': 156 },
  '1M': { '6M': 6, '1Y': 12, '3Y': 36 },
}
const INITIAL_RANGE = { '1D': '6M', '1W': '1Y', '1M': '3Y' }
const dateLabel = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
})
const signalMonthLabel = new Intl.DateTimeFormat('en-GB', {
  month: 'short', year: 'numeric', timeZone: 'UTC',
})
const shares = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
const price = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 })

const DRAW_TOOLS = [
  ['cursor', 'Cursor', MousePointer2],
  ['trend', 'Trend line', TrendingUp],
  ['level', 'Horizontal level', Minus],
  ['ruler', 'Measure', Ruler],
]
const signedPercent = new Intl.NumberFormat('en-IN', {
  style: 'percent', signDisplay: 'always', maximumFractionDigits: 2,
})
const signedPrice = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', signDisplay: 'always', maximumFractionDigits: 2,
})

function chartDate(time) {
  if (typeof time === 'string') return time
  if (!time || typeof time !== 'object') return ''
  return [time.year, time.month, time.day].map(value => String(value).padStart(2, '0')).join('-')
}

function DrawingTools({ tool, choose, anchor, drawings, undo, clear }) {
  const measure = drawings.findLast(drawing => drawing.type === 'ruler')
  const hint = tool === 'level' ? 'Click a price in the candlestick pane.'
    : tool === 'trend' || tool === 'ruler' ? (anchor ? 'Click a different candle to finish.' : 'Click the starting point in the candlestick pane.')
      : 'Draw on the price pane.'
  return <div className="drawing-tools">
    <div className="segmented" role="group" aria-label="Chart drawing tools">
      {DRAW_TOOLS.map(([key, label, Icon]) => <button key={key} type="button"
        className={tool === key ? 'active' : ''} aria-pressed={tool === key} onClick={() => choose(key)}>
        <Icon size={14}/> {label}
      </button>)}
    </div>
    <div className="drawing-actions">
      <button type="button" className="icon-button" disabled={!drawings.length} onClick={undo} aria-label="Undo drawing"><Undo2 size={15}/></button>
      <button type="button" className="icon-button" disabled={!drawings.length && !anchor} onClick={clear} aria-label="Clear drawings"><Trash2 size={15}/></button>
    </div>
    <span className="drawing-hint" aria-live="polite">{hint}</span>
    {measure && <output className="measure-result">
      {signedPrice.format(measure.change)} ({signedPercent.format(measure.change / measure.points[0].value)}) · {measure.bars} {measure.bars === 1 ? 'candle' : 'candles'}
    </output>}
  </div>
}

function Candles({ data, period, range, showEma, showLow, signal, controls }) {
  const node = useRef(null)
  const chartRef = useRef(null)
  const [quote, setQuote] = useState(null)
  const [tool, setTool] = useState('cursor')
  const [anchor, setAnchor] = useState(null)
  const [drawings, setDrawings] = useState([])
  const selection = useRef({ tool: 'cursor', anchor: null })
  const choose = next => {
    selection.current = { tool: next, anchor: null }
    setTool(next)
    setAnchor(null)
  }
  const bars = data.series[period]
  useEffect(() => {
    if (!node.current || !bars?.length) return
    const container = node.current
    const chart = createChart(container, {
      autoSize: true,
      layout: { background: { color: '#fff' }, textColor: '#446156', attributionLogo: true },
      grid: { vertLines: { color: '#f0f4f1' }, horzLines: { color: '#edf2ee' } },
      rightPriceScale: { borderColor: '#dce6df' },
      timeScale: { borderColor: '#dce6df', rightOffset: 3 },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: { mouseWheel: false, vertTouchDrag: false },
      handleScale: { mouseWheel: false },
      localization: { priceFormatter: value => price.format(value) },
    })
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: '#0a8461', downColor: '#ce645c', wickUpColor: '#0a8461', wickDownColor: '#ce645c',
      borderVisible: false,
    })
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false,
    }, 1)
    volume.priceScale().applyOptions({ scaleMargins: { top: .15, bottom: 0 } })
    chart.panes()[1].setHeight(94)
    volume.setData(bars.filter(bar => bar.volume != null).map(bar => ({
      time: bar.time, value: bar.volume, color: bar.close >= bar.open ? '#70b6a0' : '#e69991',
    })))
    const ema = chart.addSeries(LineSeries, { color: '#bd8735', lineWidth: 2, priceLineVisible: false, lastValueVisible: false })
    const low = chart.addSeries(LineSeries, { color: '#698caa', lineStyle: LineStyle.Dotted, lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false })
    candle.setData(bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })))
    ema.setData(bars.filter(bar => bar.ema != null).map(bar => ({ time: bar.time, value: bar.ema })))
    low.setData(bars.filter(bar => bar.low12 != null).map(bar => ({ time: bar.time, value: bar.low12 })))
    const signalCandle = signal && (data.week
      ? period === '1W' && bars.find(bar => bar.time === data.week)
      : period === '1M' && bars.find(bar => bar.time === data.month + '-01'))
    if (signalCandle) {
      createSeriesMarkers(candle, [{ time: signalCandle.time, position: 'belowBar', color: '#08785b',
        shape: 'arrowUp', text: data.week ? 'Week ending ' + dateLabel.format(new Date(data.month))
          : 'Signal · ' + signalMonthLabel.format(new Date(signalCandle.time)) }])
    }
    chart.subscribeCrosshairMove(event => {
      const point = event.seriesData.get(candle)
      const time = chartDate(event.time)
      setQuote(point ? { ...point, time, volume: event.seriesData.get(volume)?.value } : null)
    })
    // The chart library groups quick consecutive clicks as a double-click.
    // Handle pointer presses here so two distinct anchors are always available.
    let press = null
    const startPointer = event => {
      if (!event.isPrimary || event.button !== 0) return
      press = { id: event.pointerId, x: event.clientX, y: event.clientY }
    }
    const finishPointer = event => {
      if (press?.id !== event.pointerId) return
      const start = press
      press = null
      const { tool, anchor } = selection.current
      if (tool === 'cursor' || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 6) return
      const rect = container.getBoundingClientRect()
      const x = event.clientX - rect.left
      const y = event.clientY - rect.top
      const pane = chart.paneSize(0)
      if (x < 0 || y < 0 || x >= pane.width || y >= pane.height) return
      const time = chartDate(chart.timeScale().coordinateToTime(x))
      const value = candle.coordinateToPrice(y)
      if (!time || !Number.isFinite(value) || value <= 0) return
      if (tool === 'level') {
        setDrawings(previous => [...previous, { type: tool, value }])
      } else if (!anchor) {
        const start = { time, value }
        selection.current = { tool, anchor: start }
        setAnchor(start)
        return
      } else if (anchor.time !== time) {
        const bars = Math.abs(chart.timeScale().timeToIndex(time) - chart.timeScale().timeToIndex(anchor.time))
        setDrawings(previous => [...previous, {
          type: tool, points: [anchor, { time, value }], change: value - anchor.value, bars,
        }])
      } else {
        return
      }
      choose('cursor')
    }
    const cancelPointer = () => { press = null }
    container.addEventListener('pointerdown', startPointer, true)
    container.addEventListener('pointerup', finishPointer, true)
    container.addEventListener('pointercancel', cancelPointer, true)
    const cancel = event => {
      if (event.key === 'Escape' && selection.current.tool !== 'cursor') choose('cursor')
    }
    window.addEventListener('keydown', cancel)
    chartRef.current = { chart, candle, ema, low }

    return () => {
      window.removeEventListener('keydown', cancel)
      container.removeEventListener('pointerdown', startPointer, true)
      container.removeEventListener('pointerup', finishPointer, true)
      container.removeEventListener('pointercancel', cancelPointer, true)
      chartRef.current = null
      chart.remove()
    }
  }, [bars, data.month, data.week, period, signal])

  useEffect(() => {
    chartRef.current?.ema.applyOptions({ visible: showEma })
    chartRef.current?.low.applyOptions({ visible: showLow && period === '1M' })
  }, [bars, showEma, showLow, period, signal])

  useEffect(() => {
    const scale = chartRef.current?.chart.timeScale()
    if (!scale || !bars?.length) return
    if (range === 'All') scale.fitContent()
    else scale.setVisibleLogicalRange({
      from: Math.max(0, bars.length - RANGES[period][range]),
      to: bars.length + 2,
    })
  }, [bars, period, range, signal])

  useEffect(() => {
    controls.current = {
      reset() {
        const scale = chartRef.current?.chart.timeScale()
        if (!scale) return
        if (range === 'All') scale.fitContent()
        else scale.setVisibleLogicalRange({ from: Math.max(0, bars.length - RANGES[period][range]), to: bars.length + 2 })
      },
      zoom(factor) {
        const scale = chartRef.current?.chart.timeScale()
        const visible = scale?.getVisibleLogicalRange()
        if (!visible) return
        const width = Math.max(5, Math.min(bars.length + 5, (visible.to - visible.from) * factor))
        const center = (visible.from + visible.to) / 2
        scale.setVisibleLogicalRange({ from: center - width / 2, to: center + width / 2 })
      },
    }
    return () => { controls.current = null }
  }, [bars, controls, period, range])

  useEffect(() => {
    const current = chartRef.current
    if (!current) return
    const { chart, candle } = current
    const remove = drawings.map(drawing => {
      if (drawing.type === 'level') {
        const line = candle.createPriceLine({ price: drawing.value, color: '#7164a0', lineWidth: 2,
          lineStyle: LineStyle.Dashed, title: 'Level' })
        return () => candle.removePriceLine(line)
      }
      const line = chart.addSeries(LineSeries, {
        color: drawing.type === 'ruler' ? '#6d57ab' : '#3179ae',
        lineStyle: drawing.type === 'ruler' ? LineStyle.Dashed : LineStyle.Solid,
        lineWidth: 2, pointMarkersVisible: true, pointMarkersRadius: 3,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null,
      })
      line.setData(drawing.points.toSorted((a, b) => a.time.localeCompare(b.time)))
      return () => chart.removeSeries(line)
    })
    return () => {
      if (chartRef.current === current) remove.forEach(erase => erase())
    }
  }, [bars, signal, drawings])

  const latest = quote || bars?.at(-1)
  return <>
    <DrawingTools tool={tool} choose={choose} anchor={anchor} drawings={drawings}
      undo={() => setDrawings(previous => previous.slice(0, -1))}
      clear={() => { setDrawings([]); choose('cursor') }}/>
    {latest && <div className="ohlc"><span>{latest.time}</span>{['open','high','low','close'].map(key =>
      <span key={key}><strong>{key[0].toUpperCase()}</strong> {price.format(latest[key])}</span>)}
      <span><strong>Volume</strong> {Number.isFinite(latest.volume) ? shares.format(latest.volume) : '—'}</span></div>}
    <div ref={node} className={tool === 'cursor' ? 'stock-chart' : 'stock-chart drawing-active'} role="img" aria-label={data.symbol + ' ' + PERIODS[period] + ' candlestick chart'}/>
  </>
}

export default function ChartPreview({ data, ticker, setTicker, load, busy, shortlisted, signal }) {
  const [period, setPeriod] = useState('1M')
  const [range, setRange] = useState(INITIAL_RANGE['1M'])
  const [showEma, setShowEma] = useState(true)
  const [showLow, setShowLow] = useState(true)
  const controls = useRef(null)
  const inputId = useId()
  const [manual, setManual] = useState(false)
  const selected = !manual && shortlisted.includes(ticker) ? ticker : ''
  const showingInput = !shortlisted.length || !selected
  const canDraw = !!data?.series[period]?.length
  const reset = () => controls.current?.reset()
  const weeklyData = data?.timeframe === 'weekly'
  useEffect(() => {
    const view = weeklyData ? '1W' : '1M'
    setPeriod(view)
    setRange(INITIAL_RANGE[view])
  }, [weeklyData])

  return <section className="panel chart-preview" aria-label="Price chart preview">
    <div className="panel-head">
      <div><h2>Price chart preview</h2><p>{data ? [
        data.symbol,
        signal && (data.week ? 'Week ending ' + dateLabel.format(new Date(data.month)) : 'Signal ' + signalMonthLabel.format(new Date(data.month + '-01'))),
        data.asOf && 'Adjusted NSE prices through ' + dateLabel.format(new Date(data.asOf)),
      ].filter(Boolean).join(' · ') : 'Choose an NSE ticker or select one in the screener results.'}</p></div>
      <form className="chart-ticker" onSubmit={event => { event.preventDefault(); load(ticker) }}>
        {!!shortlisted.length && <label className="field"><span>Shortlisted ticker</span>
          <span className="select"><select value={selected} onChange={event => {
            const symbol = event.target.value
            setManual(!symbol)
            setTicker(symbol)
          }}>
            {shortlisted.map(symbol => <option key={symbol} value={symbol}>{symbol}</option>)}
            <option value="">Enter another ticker…</option>
          </select></span>
        </label>}
        {showingInput && <label className="field" htmlFor={inputId}><span>NSE ticker</span>
          <input id={inputId} value={ticker} onChange={event => setTicker(event.target.value)} placeholder="RELIANCE" required/>
        </label>}
        <button className="secondary-button" type="submit" disabled={!!busy}>{busy === 'chart' ? 'Loading…' : 'Load chart'}</button>
      </form>
    </div>
    <div className="chart-tools">
      <div className="segmented" aria-label="Candle timeframe">{Object.entries(PERIODS).map(([key, label]) =>
        <button type="button" className={period === key ? 'active' : ''} aria-pressed={period === key} key={key} onClick={() => {
          setPeriod(key)
          setRange(INITIAL_RANGE[key])
        }}>{label}</button>)}</div>
      <div className="segmented" aria-label="Visible chart range">{['6M','1Y','3Y','All'].map(value =>
        <button type="button" className={range === value ? 'active' : ''} aria-pressed={range === value} key={value} onClick={() => {
          if (range === value) reset()
          else setRange(value)
        }}>{value}</button>)}</div>
      <div className="chart-indicators">
        <label className="check"><input type="checkbox" checked={showEma} onChange={event => setShowEma(event.target.checked)}/>{data?.emaSpan || 12}-period EMA</label>
        {period === '1M' && <label className="check"><input type="checkbox" checked={showLow} onChange={event => setShowLow(event.target.checked)}/>12-month low</label>}
      </div>
      <div className="chart-zoom">
        <button className="icon-button" type="button" aria-label="Zoom in" disabled={!canDraw} onClick={() => controls.current?.zoom(.75)}><Plus size={16}/></button>
        <button className="icon-button" type="button" aria-label="Zoom out" disabled={!canDraw} onClick={() => controls.current?.zoom(1.33)}><Minus size={16}/></button>
        <button className="icon-button" type="button" aria-label="Reset chart zoom" disabled={!canDraw} onClick={reset}><RotateCcw size={15}/></button>
      </div>
    </div>
    {canDraw ? <Candles key={data.symbol + period} data={data} period={period} range={range} showEma={showEma} showLow={showLow} signal={signal} controls={controls}/>
      : <div className="empty chart-empty"><CandlestickChart size={25}/>{data ? 'No candles for this ticker in the selected period.' : 'Load a ticker to preview its candlesticks.'}</div>}
    <div className="chart-credit">TradingView Lightweight Charts™ · Copyright © 2025 <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView, Inc.</a></div>
  </section>
}
