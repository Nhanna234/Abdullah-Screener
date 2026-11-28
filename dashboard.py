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
import engine_weekly as WK


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
def benchmarks(start, end, kind, freq="M"):
    return D.fetch_benchmarks(start, end, kind, freq)


ALL_DEFAULTS = {**E.DEFAULTS, **WK.W_DEFAULTS}
WEEKLY_LABELS = {"Longest DD (months)": "Longest DD (weeks)", "Best month": "Best week",
                 "Worst month": "Worst week", "Positive months": "Positive weeks",
                 "Months beating": "Weeks beating"}


def latest_week(day):
    week = day.to_period(WK.FREQ)
    if (day + pd.offsets.BDay(1)).to_period(WK.FREQ) == week:
        week -= 1
    return week


def dashboard_config():
    day = pd.Timestamp(D.last_cutoff().date())
    latest = day.to_period("M")
    if (day + pd.offsets.BDay(1)).month == day.month:
        latest -= 1
    return {"indices": list(D.INDEX_FILES), "latest": str(latest),
            "latestWeek": WK.week_label(latest_week(day)), "params": ALL_DEFAULTS}


def universe_prices(body):
    """Constituents and cleaned daily prices of the selected universe (shared by both timeframes)."""
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
    names = frame.set_index("Symbol")["Company Name"].to_dict()
    industries = frame.set_index("Symbol")["Industry"].to_dict()
    notices = []
    if count / len(symbols) < .9:
        notices.append(f"{count} of {len(symbols)} stocks have prices. Yahoo Finance may be throttling downloads.")
    meta = {"index": index, "source": source, "covered": count, "constituents": len(symbols),
            "notices": notices, "spikes": removed}
    return daily, cutoff, names, industries, meta


def market_data(body, params):
    daily, cutoff, names, industries, meta = universe_prices(body)
    monthly = E.to_monthly(daily, pd.Timestamp(cutoff.date()))
    indicators = E.compute_indicators(monthly, params)
    months = list(indicators["C"].index)
    if not months or (monthly["partial"] and len(months) < 2):
        raise ValueError("The selected universe has insufficient monthly data.")
    latest = months[-2] if monthly["partial"] else months[-1]
    return indicators, months, latest, names, industries, {**meta, "asOf": str(monthly["last_day"].date())}


def dated_rows(frame, label="Month"):
    return records(frame.rename_axis(label).set_axis(frame.index.astype(str)).reset_index(names=label))


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
    result = build_price_chart(store.panel([symbol]), symbol, month, ema_span)
    if body.get("timeframe") == "weekly" and body.get("week"):
        result.update(weekly_chart_marker(pd.Period(body["week"], WK.FREQ)))
    return result


def weekly_chart_marker(week):
    """Chart metadata that marks the signal week on the weekly candles."""
    return {"timeframe": "weekly", "month": WK.week_label(week), "week": str(week.start_time.date())}


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


def weekly_data(body, params):
    daily, cutoff, names, industries, meta = universe_prices(body)
    W = WK.prepare(daily, params, pd.Timestamp(cutoff.date()))
    weeks = list(W["ind"]["weeks"])
    if len(weeks) < 60:
        raise ValueError("The selected universe has insufficient weekly data.")
    latest = weeks[-2] if W["w"]["partial"] else weeks[-1]
    return W, weeks, latest, names, industries, {**meta, "asOf": str(W["w"]["last_day"].date())}


def weekly_rows(frame):
    return dated_rows(frame.set_axis([WK.week_label(p) for p in frame.index]), "Week")


def weekly_screener(body, params, W, weeks, latest, names, industries):
    week = pd.Period(body.get("week") or latest.end_time.date(), WK.FREQ)
    if week not in weeks:
        raise ValueError("The signal week is outside the available price history.")
    wi = weeks.index(week)
    ind = W["ind"]
    candidates = WK.screen_week(W, params, wi, names, industries)
    funnel = WK.funnel(W)
    status = candidates["Status"].value_counts().to_dict()
    counts = {**funnel.loc[week].to_dict(), **{key: status.get(key, 0) for key in WK.STATUS_ORDER}}
    symbols = sorted(ind["C"].columns)
    actionable = candidates[candidates["Status"] != "Rejected"]
    preferred = actionable.iloc[0]["Symbol"] if not actionable.empty else (
        candidates.iloc[0]["Symbol"] if not candidates.empty else ("RELIANCE" if "RELIANCE" in symbols else symbols[0]))
    preview = build_price_chart(price_store().panel([preferred]), preferred, WK.week_label(week), params["w_ema"])
    preview.update(weekly_chart_marker(week))
    exits = []
    for symbol in dict.fromkeys(D._clean(item) for item in body.get("holdings", "").split(",") if item.strip()):
        price = ind["C"].iloc[wi].get(symbol)
        ema = ind["E"].iloc[wi].get(symbol)
        if pd.isna(price) or pd.isna(ema):
            exits.append({"Symbol": symbol, "Decision": "No price data for this symbol"})
        else:
            exits.append({"Symbol": symbol, "Close": price, "10W EMA": ema, "Close vs EMA": price / ema - 1,
                          "Decision": "Exit · Weekly close < 10W EMA" if price < ema else "Hold"})
    return {"timeframe": "weekly", "month": WK.week_label(week), "week": WK.week_label(week),
            "provisional": week > latest, "counts": counts,
            "funnel": weekly_rows(funnel.loc[:week].tail(52)),
            "candidates": records(candidates), "symbols": symbols, "preview": preview,
            "exits": records(pd.DataFrame(exits)), "settings": params}


def weekly_backtest(body, params, W, weeks, latest, names, industries):
    start = pd.Period(body["first"], "M")
    end = pd.Period(body["last"], "M")
    dp = W["dp"]
    days = dp["days"]
    cap = min(end.end_time, latest.end_time)
    week_ends = [dp["last"][wi] for wi, week in enumerate(weeks)
                 if week <= latest and dp["last"][wi] >= dp["first"][wi] and days[dp["last"][wi]] <= cap]
    s_idx = int(days.searchsorted(start.start_time))
    e_idx = int(week_ends[-1]) if week_ends else -1
    if not start < end or s_idx >= len(days) or dp["day_w"][s_idx] < 1 or e_idx <= s_idx \
            or end > latest.end_time.to_period("M"):
        raise ValueError(f"Choose a first signal month before the valuation month. "
                         f"The last completed week ends {WK.week_label(latest)}.")
    res = WK.run_backtest(W, params, s_idx, e_idx, names, industries)
    vals = res["vals"]
    first_week, last_week = vals.index[0], vals.index[-1]
    reference, sources = benchmarks(first_week.start_time.date(), (last_week.end_time + pd.Timedelta(days=1)).date(),
                                    body.get("bench", "TRI"), WK.FREQ)
    curves = pd.DataFrame({"Strategy": vals["Value"]})
    for name in reference.columns:
        curves[name] = reference[name].reindex(curves.index)
    curves["Equal-weight universe"] = res["ew"].reindex(curves.index)
    curves = curves.dropna(axis=1, how="all")
    for name in curves:
        curves[name] = curves[name] / curves[name].dropna().iloc[0] * 100
    stats = pd.DataFrame({name: E.perf_stats(curves[name], params["cash_yield"], 52) for name in curves})
    relative = pd.DataFrame({name: E.relative_stats(curves["Strategy"], curves[name], 52)
                             for name in curves if name != "Strategy"})
    metrics = pd.concat([stats, relative]).rename(index=WEEKLY_LABELS).rename_axis("Metric").reset_index()
    weekly = vals.assign(Return=vals.Value.pct_change(), Exposure=1 - vals.Cash / vals.Value)
    year = [week.end_time.year for week in vals.index]
    year_end = vals.Value.groupby(year).last()
    annual = year_end.pct_change()
    annual.iloc[0] = year_end.iloc[0] / vals.Value.iloc[0] - 1
    returns = annual.rename("Return").rename_axis("Year").reset_index()
    return {
        "timeframe": "weekly", "first": str(start), "last": str(end),
        "firstWeek": WK.week_label(first_week), "lastWeek": WK.week_label(last_week),
        "growth": weekly_rows(curves), "drawdown": weekly_rows(curves / curves.cummax() - 1),
        "rolling": weekly_rows(curves.pct_change(52, fill_method=None).dropna(how="all")),
        "monthly": weekly_rows(weekly), "annual": records(returns), "metrics": records(metrics),
        "trades": records(res["trades"]), "allSignals": records(res["allsig"]),
        "allocation": records(res["snap"]), "funnel": weekly_rows(res["funnel"]),
        "tradeMetrics": records(pd.Series(WK.trade_stats(res["trades"]), name="Value").rename_axis("Metric").reset_index()),
        "sources": sources, "settings": params,
    }


def run_weekly(body, params):
    W, weeks, latest, names, industries, meta = weekly_data(body, params)
    if body.get("mode") == "backtest":
        meta["notices"].append("This backtest uses one constituent list across the entire period, which can introduce survivorship bias.")
        result = weekly_backtest(body, params, W, weeks, latest, names, industries)
    else:
        result = weekly_screener(body, params, W, weeks, latest, names, industries)
        if result["provisional"]:
            meta["notices"].append("The selected week is in progress. Its setups are provisional until Friday's close.")
    return {**meta, **result}


def run(body):
    chosen = {key: value for key, value in body.get("params", {}).items() if key in ALL_DEFAULTS}
    if body.get("timeframe") == "weekly":
        return run_weekly(body, {**ALL_DEFAULTS, **chosen})
    params = {**E.DEFAULTS, **{key: value for key, value in chosen.items() if key in E.DEFAULTS}}
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
    """Excel file with the signal stocks only (stocks that passed every rule)."""
    candidates = pd.DataFrame(data.get("candidates") or [])
    signals = candidates[candidates["Status"] != "Rejected"] if "Status" in candidates else candidates
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        signals.to_excel(writer, sheet_name="Signals", index=False)
    return {"xlsx": base64.b64encode(output.getvalue()).decode("ascii")}


def export_report(body):
    data = body["report"]
    if body.get("kind") == "screener":
        return export_screener(data)
    period = "Weekly" if data.get("timeframe") == "weekly" else "Monthly"
    sheets = {"Performance": "metrics", "Growth of 100": "growth", f"{period} values": "monthly",
              "Trades": "trades", "All signals": "allSignals", "Signal funnel": "funnel",
              "Annual returns": "annual", "Allocation": "allocation"}
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, key in sheets.items():
            pd.DataFrame(data[key]).to_excel(writer, sheet_name=name, index=False)
        pd.DataFrame(data["settings"].items(), columns=["Setting", "Value"]).to_excel(
            writer, sheet_name="Settings", index=False)
    return {"xlsx": base64.b64encode(output.getvalue()).decode("ascii")}
