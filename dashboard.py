"""Prepare screener and backtest reports for the React dashboard."""
import base64
import io
import json
import os
import re
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st

import data as D
import engine as E


def records(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))


@st.cache_resource
def price_store():
    fallback = os.path.join(tempfile.gettempdir(), "ema_reclaim_prices")
    return D.PriceStore(os.environ.get("EMA_APP_CACHE", fallback))


@st.cache_data(ttl=86400, show_spinner=False)
def constituents(index):
    return D.fetch_constituents(index)


@st.cache_data(ttl=43200, show_spinner=False)
def benchmarks(start, end, kind):
    return D.fetch_benchmarks(start, end, kind)


def dashboard_config():
    day = pd.Timestamp(D.last_cutoff().date())
    latest = day.to_period("M")
    if (day + pd.offsets.BDay(1)).month == day.month:
        latest -= 1
    return {"indices": list(D.INDEX_FILES), "latest": str(latest), "params": E.DEFAULTS}


def market_data(body, params):
    index = body.get("index")
    if index not in D.INDEX_FILES:
        raise ValueError("Select an NSE index.")
    if body.get("constituents"):
        frame = D.parse_constituents(pd.read_csv(io.StringIO(body["constituents"].lstrip("\ufeff"))))
        source = "Uploaded constituents"
    else:
        frame, source = constituents(index)
    symbols = sorted(frame["Symbol"].dropna().unique())
    if not symbols:
        raise ValueError("The selected universe has no stocks.")
    store = price_store()
    cutoff = D.last_cutoff()
    store.update(symbols, datetime.now(D.IST) if body.get("refresh") else cutoff)
    count = store.coverage(symbols)
    if not count:
        raise ValueError("Yahoo Finance returned no prices. Try again later or use Refresh prices in Settings.")
    daily, removed = E.clean_daily(store.panel(symbols))
    monthly = E.to_monthly(daily, pd.Timestamp(cutoff.date()))
    indicators = E.compute_indicators(monthly, params)
    months = list(indicators["C"].index)
    if not months or (monthly["partial"] and len(months) < 2):
        raise ValueError("The selected universe has insufficient monthly data.")
    latest = months[-2] if monthly["partial"] else months[-1]
    names = frame.set_index("Symbol")["Company Name"].to_dict()
    industries = frame.set_index("Symbol")["Industry"].to_dict()
    notices = []
    if count / len(symbols) < .9:
        notices.append(f"{count} of {len(symbols)} stocks have prices. Yahoo Finance may be throttling downloads.")
    return indicators, months, latest, names, industries, {
        "index": index, "source": source, "covered": count, "constituents": len(symbols),
        "asOf": str(monthly["last_day"].date()), "notices": notices, "spikes": removed,
    }


def dated_rows(frame):
    return records(frame.rename_axis("Month").set_axis(frame.index.astype(str)).reset_index(names="Month"))


def build_price_chart(daily, symbol, month, ema_span):
    """Build adjusted OHLC candles through the latest price cutoff."""
    daily, _ = E.clean_daily(daily)
    cutoff = pd.Timestamp(D.last_cutoff().date())
    daily = {field: frame.loc[:cutoff] for field, frame in daily.items()}
    fields = ("Open", "High", "Low", "Close", "Volume")
    frame = pd.DataFrame({name.lower(): daily[name][symbol] for name in fields})
    frame = frame.loc[frame["close"].notna()]
    metadata = {"symbol": symbol, "month": str(month), "emaSpan": ema_span,
                "asOf": str(frame.index[-1].date()) if not frame.empty else None}
    if frame.empty:
        return {**metadata, "series": {}}

    series = {}
    for label, frequency, limit in (("1D", None, 2000), ("1W", "W-FRI", 520), ("1M", "M", None)):
        if frequency is None:
            candles = frame.copy()
        else:
            periods = frame.index.to_period(frequency)
            candles = frame.groupby(periods).agg(
                open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"),
                volume=("volume", lambda values: values.sum(min_count=1)),
            )
            if label == "1M":
                candles = candles.reindex(pd.period_range(candles.index[0], candles.index[-1], freq="M"))
            candles.index = candles.index.start_time
        candles["ema"] = candles["close"].ewm(span=ema_span, adjust=False).mean()
        if label == "1M":
            candles["low12"] = candles["low"].rolling(12).min()
        candles = candles.dropna(subset=["open", "high", "low", "close"])
        if limit is not None:
            candles = candles.tail(limit)
        candles.index = candles.index.strftime("%Y-%m-%d")
        series[label] = records(candles.rename_axis("time").reset_index())
    return {**metadata, "series": series}


def chart(body):
    symbol = D._clean(body.get("symbol", ""))
    if not re.fullmatch(r"[A-Z0-9&.-]{1,25}", symbol):
        raise ValueError("Enter a valid NSE ticker.")
    month = pd.Period(body.get("signal") or D.last_cutoff().date(), "M")
    ema_span = int(body.get("emaSpan", E.DEFAULTS["ema_span"]))
    if not 3 <= ema_span <= 36:
        raise ValueError("EMA span must be from 3 to 36.")
    store = price_store()
    store.update([symbol], D.last_cutoff())
    if not store.coverage([symbol]):
        raise ValueError(f"No Yahoo Finance price data for {symbol}.")
    return build_price_chart(store.panel([symbol]), symbol, month, ema_span)


def screener(body, params, ind, months, latest, names, industries):
    month = pd.Period(body.get("signal") or latest, "M")
    if month not in months:
        raise ValueError("The signal month is outside the available price history.")
    _, _, funnel = E.compute_signals(ind, params)
    candidates = E.screen_month(ind, params, month, names, industries)
    symbols = sorted(ind["C"].columns)
    preferred = candidates.iloc[0]["Symbol"] if not candidates.empty else (
        "RELIANCE" if "RELIANCE" in symbols else symbols[0]
    )
    preview = build_price_chart(price_store().panel([preferred]), preferred, month, params["ema_span"])
    exits = []
    for symbol in dict.fromkeys(D._clean(item) for item in body.get("holdings", "").split(",") if item.strip()):
        price = ind["C"].loc[month].get(symbol)
        ema = ind["E"].loc[month].get(symbol)
        if pd.isna(price) or pd.isna(ema):
            exits.append({"Symbol": symbol, "Decision": "No price data for this symbol"})
        else:
            reason = "Exit · Close < EMA" if price < ema else (
                "Exit · Extension limit" if params["exit_ext"] and price / ema > params["exit_ext"] else "Hold"
            )
            exits.append({"Symbol": symbol, "Close": price, "EMA": ema, "Close/EMA": price/ema, "Decision": reason})
    counts = funnel.loc[month].to_dict()
    return {"month": str(month), "provisional": month > latest,
            "counts": counts, "funnel": dated_rows(funnel.loc[:month].tail(36)),
            "candidates": records(candidates), "symbols": symbols, "preview": preview,
            "exits": records(pd.DataFrame(exits)), "settings": params}


def backtest(body, params, ind, months, latest, names, industries):
    start = pd.Period(body["first"], "M")
    end = pd.Period(body["last"], "M")
    if start not in months or end not in months or not start < end <= latest:
        raise ValueError(f"Choose a first signal month before the valuation month. The last completed month is {latest}.")
    res = E.run_backtest(ind, params, months, months.index(start), months.index(end), names, industries)
    vals = res["vals"]
    reference, sources = benchmarks((start - 1).start_time.date(), (end + 1).start_time.date(), body.get("bench", "TRI"))
    curves = pd.DataFrame({"Strategy": vals["Value"]})
    for name in reference.columns:
        curves[name] = reference[name].reindex(curves.index)
    curves["Equal-weight universe"] = res["ew"].reindex(curves.index)
    curves = curves.dropna(axis=1, how="all")
    for name in curves:
        curves[name] = curves[name] / curves[name].dropna().iloc[0] * 100
    stats = pd.DataFrame({name: E.perf_stats(curves[name], params["cash_yield"]) for name in curves})
    relative = pd.DataFrame({name: E.relative_stats(curves["Strategy"], curves[name])
                             for name in curves if name != "Strategy"})
    metrics = pd.concat([stats, relative]).rename_axis("Metric").reset_index()
    monthly = vals.assign(Return=vals.Value.pct_change(), Exposure=1 - vals.Cash / vals.Value)
    annual = vals.Value.groupby(vals.index.year).last().pct_change()
    annual.iloc[0] = vals.Value.groupby(vals.index.year).last().iloc[0] / vals.Value.iloc[0] - 1
    returns = annual.rename("Return").rename_axis("Year").reset_index()
    return {
        "first": str(start), "last": str(end), "growth": dated_rows(curves),
        "drawdown": dated_rows(curves / curves.cummax() - 1),
        "rolling": dated_rows(curves.pct_change(12, fill_method=None).dropna(how="all")),
        "monthly": dated_rows(monthly), "annual": records(returns), "metrics": records(metrics),
        "trades": records(res["trades"]), "allSignals": records(res["allsig"]),
        "allocation": records(res["snap"]), "funnel": dated_rows(res["funnel"]),
        "tradeMetrics": records(pd.Series(E.trade_stats(res["trades"]), name="Value").rename_axis("Metric").reset_index()),
        "sources": sources, "settings": params,
    }


def run(body):
    params = {**E.DEFAULTS, **{key: value for key, value in body.get("params", {}).items() if key in E.DEFAULTS}}
    ind, months, latest, names, industries, meta = market_data(body, params)
    if body.get("mode") == "backtest":
        meta["notices"].append("This backtest uses one constituent list across the entire period, which can introduce survivorship bias.")
        result = backtest(body, params, ind, months, latest, names, industries)
    else:
        result = screener(body, params, ind, months, latest, names, industries)
        if result["provisional"]:
            meta["notices"].append("The selected month is in progress. Its signals are provisional until month-end.")
    return {**meta, **result}


def export_screener(data):
    candidates = pd.DataFrame(data.get("candidates") or [])
    signals = candidates[candidates["Status"] == "SIGNAL"] if "Status" in candidates else candidates
    counts = data.get("counts") or {}
    info = [("Index", data.get("index")), ("Signal month", data.get("month")),
            ("Provisional (month in progress)", "Yes" if data.get("provisional") else "No"),
            ("Prices up to", data.get("asOf")), ("Constituent source", data.get("source")),
            ("Stocks with price data", f"{data.get('covered')} of {data.get('constituents')}")]
    info += [(f"Count · {name}", value) for name, value in counts.items()]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        signals.to_excel(writer, sheet_name="Signals", index=False)
        candidates.to_excel(writer, sheet_name="All candidates", index=False)
        if data.get("exits"):
            pd.DataFrame(data["exits"]).to_excel(writer, sheet_name="Holdings check", index=False)
        pd.DataFrame(data.get("funnel") or []).to_excel(writer, sheet_name="Signal frequency", index=False)
        pd.DataFrame(info, columns=["Item", "Value"]).to_excel(writer, sheet_name="Screen info", index=False)
        pd.DataFrame((data.get("settings") or {}).items(), columns=["Setting", "Value"]).to_excel(
            writer, sheet_name="Settings", index=False)
    return {"xlsx": base64.b64encode(output.getvalue()).decode("ascii")}


def export_report(body):
    data = body["report"]
    if body.get("kind") == "screener":
        return export_screener(data)
    sheets = {"Performance": "metrics", "Growth of 100": "growth", "Monthly values": "monthly",
              "Trades": "trades", "All signals": "allSignals", "Signal funnel": "funnel",
              "Annual returns": "annual", "Allocation": "allocation"}
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, key in sheets.items():
            pd.DataFrame(data[key]).to_excel(writer, sheet_name=name, index=False)
        pd.DataFrame(data["settings"].items(), columns=["Setting", "Value"]).to_excel(
            writer, sheet_name="Settings", index=False)
    return {"xlsx": base64.b64encode(output.getvalue()).decode("ascii")}
