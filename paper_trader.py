"""
Pixel Invest - weekly momentum paper trading, one account per market
====================================================================

GitHub runs this every weekend (see .github/workflows). To run it by hand:
    ./run_paper_trader.command        (Mac: double-click it in Finder)
    .venv/bin/python paper_trader.py  (or from a terminal)

For each market in markets.py it:
  1. Downloads recent prices from Yahoo Finance.
  2. Moves that market's paper account forward from where it stopped:
       - rank the market's stocks by their 3-month (63 trading day) return
       - hold the top N, equal money in each new position
       - sell a holding when it falls below the exit rank
       - safety switch: all cash when the market's index fund closes below
         its 200-day average
     Trades happen at the closing price on the last trading day of each
     finished week. Nothing is connected to any real brokerage account.
     Between weekends, each run also shows the latest day's and the week's plus/minus,
     valued from the shares held. While the market is open (GitHub runs it every 30
     minutes then) that's today so far, at the latest prices, plus a 30-minute chart of
     the week. Those figures are shown only; they never go into the record.
  3. Writes that market's page (docs/<slug>/), then the Home page (docs/).

Files per market, in data/<slug>/
  paper_account.json   start date + the current portfolio (cash, shares)
  trades.csv           every paper trade - only ever added to, never redone
  account_value.csv    daily account value vs the index fund - also only added to
  backtest.json        monthly backtest cache
  summary.json         the numbers the Home page shows

Past trades are a permanent record: later price revisions or edits to the
stock list never change them. Stock splits are applied to shares you hold;
in markets with "dividends": True, dividends are paid into cash (and the
index fund comparison reinvests its own).

To start a market over: delete data/<slug>/paper_account.json.
"""

import json
import os
import shutil
import sys
import webbrowser
from datetime import date, datetime, time, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dashboard
import insights
from markets import MARKETS

DATA = os.path.join(HERE, "data")
FX_PATH = os.path.join(DATA, "fx.json")      # last US$-per-C$ rate, for the Home page total
DOCS = os.path.join(HERE, "docs")
BENCHMARK = "QQQ"         # default index fund (tests and older callers)

DEFAULTS = {"capital": 10_000.0, "top_n": 10, "exit_rank": 20, "lookback": 63,
            "sma_days": 200, "cost_pct": 0.0, "benchmark": BENCHMARK, "bench_label": "QQQ",
            "dividends": False, "symbol": "$", "currency": "US$"}
TRADE_COLS = ["date", "action", "ticker", "shares", "price", "value", "gain_pct", "reason"]
VALUE_COLS = ["date", "paper_account", "cash", "positions", "qqq_same_money"]   # last = index fund
HISTORY_DAYS = 450        # calendar days of prices needed before the first day we process
MARKET_OPEN_ET = time(9, 30)
MARKET_CLOSE_ET = time(16, 30)      # closing prices count as final from here
STALE_DAYS = 8            # only fail loudly if an account hasn't updated for this long


class DataError(RuntimeError):
    pass


def paths(slug):
    folder = os.path.join(DATA, slug)
    return dict(folder=folder, state=os.path.join(folder, "paper_account.json"),
                trades=os.path.join(folder, "trades.csv"), values=os.path.join(folder, "account_value.csv"),
                backtest=os.path.join(folder, "backtest.json"), summary=os.path.join(folder, "summary.json"),
                page=os.path.join(DOCS, slug, "index.html"))


# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------
def download(tickers, start):
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance is not installed. Run:  .venv/bin/pip install -r requirements.txt")
    print(f"Downloading prices for {len(tickers)} tickers since {start}...")
    raw = yf.download(tickers, start=str(start), auto_adjust=False, actions=True,
                      progress=False, threads=True)
    closes = raw["Close"].dropna(how="all", axis=1).sort_index()
    splits = raw["Stock Splits"].reindex(columns=closes.columns).fillna(0.0).sort_index()
    divs = (raw["Dividends"].reindex(columns=closes.columns).fillna(0.0).sort_index()
            if "Dividends" in raw.columns.get_level_values(0) else splits * 0)
    return closes, splits, divs


def load_csv_prices(path):
    """Testing helper: closes CSV, plus optional '<name>_splits.csv' beside it."""
    closes = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    sp = path.replace(".csv", "_splits.csv")
    splits = (pd.read_csv(sp, index_col=0, parse_dates=True).sort_index()
              if os.path.exists(sp) else pd.DataFrame(0.0, index=closes.index, columns=closes.columns))
    splits = splits.reindex(index=closes.index, columns=closes.columns).fillna(0.0)
    return closes, splits, splits * 0


def clean(prices, benchmark=BENCHMARK):
    """Drop unfinished/partial days (no index fund price or most stocks missing)."""
    prices = prices[prices[benchmark].notna()]
    prices = prices[prices.notna().mean(axis=1) > 0.8]
    return prices.ffill(limit=5)        # ride over short data gaps


def final_cutoff(now_et):
    """Days strictly before this date have a final closing price."""
    d = now_et.date()
    return d + timedelta(days=1) if now_et.time() >= MARKET_CLOSE_ET else d


def now_eastern():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def market_open(now_et):
    """A weekday between the open and the time that day's closing prices count as final."""
    return now_et.weekday() < 5 and MARKET_OPEN_ET <= now_et.time() < MARKET_CLOSE_ET


def download_intraday(tickers):
    """The last few days' 30-minute prices (Yahoo; can be up to 15 minutes behind), indexed
    by each bar's start in New York time."""
    import yfinance as yf
    raw = yf.download(tickers, period="5d", interval="30m", auto_adjust=False,
                      progress=False, threads=True)
    bars = raw["Close"].dropna(how="all", axis=1).sort_index()
    bars.index = bars.index.tz_convert("America/New_York")
    return bars


def get_fx(download=True):
    """US$ per C$, for adding the accounts up on the Home page (Yahoo's CAD=X is C$ per US$).
    Saved to data/fx.json, so a failed download falls back to the last rate."""
    saved = None
    if os.path.exists(FX_PATH):
        try:
            saved = json.load(open(FX_PATH))
        except ValueError:
            pass
    if not download:
        return saved
    try:
        import yfinance as yf
        c = yf.download("CAD=X", period="5d", progress=False, auto_adjust=False)["Close"]
        c = (c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c).dropna()
        saved = {"usd_per_cad": round(1 / float(c.iloc[-1]), 5), "date": str(c.index[-1].date())}
        atomic_write(FX_PATH, json.dumps(saved))
    except Exception as e:                    # the total just uses the last saved rate
        print(f"(no new exchange rate: {type(e).__name__}: {e})")
    return saved


def live_prices(closes, bars, now_et, benchmark):
    """Today's latest prices while the market is open, or None. Uses the newest 30-minute
    prices, and today's unfinished daily row for anything those don't have."""
    if not market_open(now_et):
        return None
    today = pd.Timestamp(now_et.date())
    row = closes.loc[today].copy() if today in closes.index else None
    if bars is not None and len(bars):
        tb = bars[(bars.index.tz_localize(None).normalize() == today) & (bars.index < now_et)]
        if len(tb):
            latest = tb.ffill().iloc[-1]
            row = latest if row is None else latest.combine_first(row)
    if row is None or pd.isna(row.get(benchmark)):
        return None
    return row.rename(today)


# --------------------------------------------------------------------------
# Ledger engine
# --------------------------------------------------------------------------
def new_ledger(start_day, bench_price, capital, dividends=False):
    led = {"start": str(start_day), "qqq_start": float(bench_price), "cash": float(capital),
           "positions": {}, "last_processed": None, "last_rebalance": None,
           "data_through": None}
    if dividends:
        led["bench_units"] = float(capital) / float(bench_price)   # index fund, dividends reinvested
        led["dividends_received"] = 0.0
    return led


def rebalance_days(days, cutoff, first_day=None):
    """Which of `days` are rebalance days, and up to which day that's known.

    A day is a rebalance day when it is the last trading day of a week that
    has finished (Friday before `cutoff`). For the last day we have in a week
    that isn't over yet we can't tell, so `known_through` stops before it.
    """
    rebal, known = set(), None
    s = pd.Series(days, index=days)
    for per, grp in s.groupby(s.dt.to_period("W-FRI")):
        week_over = per.end_time.date() < cutoff
        last = grp.index[-1]
        if week_over:
            rebal.add(last)
            known = last
        else:
            known = grp.index[-2] if len(grp) > 1 else known
            break                       # nothing after an unfinished week
    if first_day is not None:
        rebal.add(first_day)
    return rebal, known


def switch_now(px, cfg):
    """The safety switch at the latest prices shown (today's, while markets are open): whether
    the index fund is above its average, and by how much. Shown only; the rules act on it at
    the week's last close."""
    s = px[cfg.get("benchmark", BENCHMARK)].dropna()
    sma = s.rolling(cfg["sma_days"]).mean()
    if s.empty or pd.isna(sma.iloc[-1]):
        return None
    return dict(on=bool(s.iloc[-1] > sma.iloc[-1]), gap=round(float(s.iloc[-1] / sma.iloc[-1] - 1) * 100, 2))


def week_end_gaps(days, after, cutoff):
    """Weekdays that would end a finished week but have no daily close: from the day after the
    last close we have in that week (or its Monday, if none) to its Friday, for weeks after
    `after` whose Friday is before `cutoff`. A holiday looks the same, so these are only suspects."""
    days = pd.DatetimeIndex(days).normalize()
    if days.empty:
        return []
    lo = pd.Timestamp(after) if after else days[0] - pd.Timedelta(days=1)
    gaps, fri = [], lo + pd.offsets.Week(weekday=4)
    while fri.date() < cutoff:
        mon = fri - pd.Timedelta(days=4)
        have = days[(days >= mon) & (days <= fri)]
        start = max(have.max() + pd.Timedelta(days=1) if len(have) else mon, lo + pd.Timedelta(days=1))
        gaps += [d.date() for d in pd.bdate_range(start, fri)]
        fri += pd.Timedelta(days=7)
    return gaps


def check_week_end_closes(days, after, cutoff, benchmark, intraday=None):
    """Wait (DataError: nothing is traded or saved) when a finished week's last close isn't in
    yet. The rules trade at a week's last close, and with Friday missing, Thursday would look
    like the last day, as in a holiday week. The 30-minute prices tell them apart: if the index
    fund traded that day, Yahoo is just late with the daily close."""
    gaps = week_end_gaps(days, after, cutoff)
    if not gaps:
        return
    try:
        bars = (intraday or download_intraday)([benchmark])
        traded = {ts.date() for ts in bars[benchmark].dropna().index}
    except Exception as e:
        raise DataError(f"can't tell whether the market was open on {gaps[-1]:%a %b %d} "
                        f"(no 30-minute prices: {type(e).__name__}), so the week's trades wait")
    late = [d for d in gaps if d in traded]
    if late:
        raise DataError(f"Yahoo hasn't posted the {late[-1]:%a %b %d} closing prices yet, "
                        f"so the week's trades wait for the next run")


def advance(prices, splits, cfg, ledger, cutoff, dividends=None):
    """Move the ledger forward through every settled day after last_processed.

    Returns (new_trades, new_values, notes). Mutates `ledger`.
    """
    b = cfg.get("benchmark", BENCHMARK)
    label = cfg.get("bench_label", b)
    stocks = [c for c in prices.columns if c != b]
    bench = prices[b]
    sma = bench.rolling(cfg["sma_days"]).mean()
    mom = prices[stocks] / prices[stocks].shift(cfg["lookback"]) - 1
    last_px = prices.ffill()            # value held stocks at their last known price
    pay_divs = bool(cfg.get("dividends")) and dividends is not None and "bench_units" in ledger

    # Splits Yahoo has adjusted for since our last download apply to shares we
    # carried in (everything in this download is already in post-split units).
    notes = []
    if ledger["data_through"]:
        since = splits[splits.index > pd.Timestamp(ledger["data_through"])]
        for t, pos in ledger["positions"].items():
            if t not in since:
                continue
            for d, r in since[t][since[t] > 0].items():
                pos["shares"] *= r
                pos["buy_price"] /= r
                notes.append(f"{t} split {r:g}-for-1 on {d.date()}")

    start = pd.Timestamp(ledger["start"])
    after = pd.Timestamp(ledger["last_processed"]) if ledger["last_processed"] else start - pd.Timedelta(days=1)
    days = prices.index[(prices.index > after) & (prices.index.date < cutoff)]
    if len(days) == 0:
        return [], [], notes
    first = start if ledger["last_processed"] is None else None
    rebal, known = rebalance_days(days, cutoff, first)
    if known is None and first is None:
        return [], [], notes
    days = days[days <= (known if known is not None else first)]

    pos, trades, values = ledger["positions"], [], []
    for day in days:
        px = prices.loc[day]
        if pay_divs and day in dividends.index:
            # Paid on the ex-date to whoever held at the previous close.
            dv = dividends.loc[day]
            for t, p in pos.items():
                if t in dv and dv[t] > 0:
                    ledger["cash"] += p["shares"] * dv[t]
                    ledger["dividends_received"] += p["shares"] * dv[t]
            if b in dv and dv[b] > 0 and day != start:
                ledger["bench_units"] *= 1 + dv[b] / bench[day]
        if day in rebal:
            if pd.isna(sma[day]):
                raise DataError(f"Not enough {label} history for the {cfg['sma_days']}-day average on {day.date()}")
            risk_on = bool(bench[day] > sma[day])
            m = mom.loc[day].dropna()
            ranks = m.rank(ascending=False)
            exits = list(pos) if not risk_on else \
                [t for t in pos if t not in ranks or ranks[t] > cfg["exit_rank"]]
            for t in exits:
                p = last_px.at[day, t]
                val = pos[t]["shares"] * p
                ledger["cash"] += val * (1 - cfg["cost_pct"])
                if not risk_on:
                    why = f"Safety switch: {label} below 200-day avg"
                elif t not in ranks:
                    why = "No price data (left the list?)"
                else:
                    why = f"Fell to rank {int(ranks[t])} (below {cfg['exit_rank']})"
                trades.append(dict(date=str(day.date()), action="SELL", ticker=t,
                                   shares=round(pos[t]["shares"], 4), price=round(p, 2),
                                   value=round(val, 2),
                                   gain_pct=round((p / pos[t]["buy_price"] - 1) * 100, 1), reason=why))
                del pos[t]
            if risk_on:
                slots = cfg["top_n"] - len(pos)
                buys = [t for t in ranks.sort_values().index if t not in pos][:max(slots, 0)]
                if buys:
                    each = ledger["cash"] / len(buys)
                    for t in buys:
                        q = each * (1 - cfg["cost_pct"]) / px[t]
                        pos[t] = {"shares": q, "buy_price": float(px[t]), "buy_date": str(day.date())}
                        ledger["cash"] -= each
                        trades.append(dict(date=str(day.date()), action="BUY", ticker=t,
                                           shares=round(q, 4), price=round(px[t], 2),
                                           value=round(each, 2), gain_pct=None,
                                           reason=f"Rank {int(ranks[t])} by 3-month gain"))
            ledger["last_rebalance"] = str(day.date())
        val = ledger["cash"] + sum(p["shares"] * last_px.at[day, t] for t, p in pos.items())
        bench_val = (ledger["bench_units"] * bench[day] if "bench_units" in ledger
                     else cfg["capital"] * bench[day] / ledger["qqq_start"])
        values.append(dict(date=str(day.date()), paper_account=round(val, 2),
                           cash=round(ledger["cash"], 2), positions=len(pos),
                           qqq_same_money=round(bench_val, 2)))
        ledger["last_processed"] = str(day.date())
    return trades, values, notes


def valued_days(prices, cfg, ledger, values, dividends=None):
    """Each recorded day's account and index-fund value, then every later day valued from the
    shares held at that day's price. Columns: acct, bench, cash, units (index-fund units).
    Nothing here is written anywhere: the record moves forward only when a week finishes."""
    b = cfg.get("benchmark", BENCHMARK)
    px = prices.ffill()
    rec = values.assign(date=pd.to_datetime(values["date"])).set_index("date")
    rec = rec[["paper_account", "qqq_same_money", "cash"]].astype(float).set_axis(["acct", "bench", "cash"], axis=1)
    last = pd.Timestamp(ledger["last_processed"])
    pos = ledger["positions"]
    pay = bool(cfg.get("dividends")) and dividends is not None and "bench_units" in ledger
    cash, units = ledger["cash"], ledger.get("bench_units")
    extra = {}
    for day in px.index[px.index > last]:
        if pay and day in dividends.index:
            dv = dividends.loc[day]
            cash += sum(p["shares"] * dv[t] for t, p in pos.items() if t in dv and dv[t] > 0)
            if b in dv and dv[b] > 0:
                units *= 1 + dv[b] / px.at[day, b]
        acct = cash + sum(p["shares"] * px.at[day, t] for t, p in pos.items())
        bench = (units * px.at[day, b] if units is not None
                 else cfg["capital"] * px.at[day, b] / ledger["qqq_start"])
        extra[day] = (acct, bench, cash)
    series = pd.concat([rec, pd.DataFrame.from_dict(extra, orient="index", columns=["acct", "bench", "cash"])]).sort_index()
    series["units"] = series["bench"] / px[b].reindex(series.index)
    return series


def day_change(prices, cfg, ledger, values, dividends=None, series=None):
    """The plus/minus for the latest trading day in `prices` (today's latest prices, while the
    market is open), and for the week so far, for the account and its index fund.
    Shown only, never recorded. Returns None until there are two days."""
    if ledger.get("last_processed") is None:
        return None
    if series is None:
        series = valued_days(prices, cfg, ledger, values, dividends)
    if len(series) < 2:
        return None
    px = prices.ffill()
    pos = ledger["positions"]
    last = pd.Timestamp(ledger["last_processed"])
    d, prev = series.index[-1], series.index[-2]
    a1, b1 = series.at[d, "acct"], series.at[d, "bench"]
    a0, b0 = series.at[prev, "acct"], series.at[prev, "bench"]
    # Each stock held through the whole day (not bought at that day's close).
    movers = [[t, round((px.at[d, t] / px.at[prev, t] - 1) * 100, 2)] for t, p in pos.items()
              if p["buy_date"] < str(d.date()) and t in px and px.at[prev, t] > 0]
    movers.sort(key=lambda m: -m[1])
    out = dict(day=str(d.date()), prev=str(prev.date()), recorded=bool(d <= last),
               value=round(a1, 2), change=round(a1 - a0, 2), pct=round((a1 / a0 - 1) * 100, 3),
               bench_value=round(b1, 2), bench_change=round(b1 - b0, 2), bench_pct=round((b1 / b0 - 1) * 100, 3),
               invested=len(pos), movers=movers)
    # This week so far, against the previous week's last close (skipped when that's `prev`).
    before = series[series.index < d.to_period("W-FRI").start_time]
    if len(before) and before.index[-1] != prev:
        a_w, b_w = before["acct"].iloc[-1], before["bench"].iloc[-1]
        out.update(week_change=round(a1 - a_w, 2), week_pct=round((a1 / a_w - 1) * 100, 3),
                   week_bench_pct=round((b1 / b_w - 1) * 100, 3), since=str(before.index[-1].date()))
    return out


def week_curve(bars, series, prices, cfg, ledger, now_et):
    """This week's account and index-fund value every 30 minutes, valued from the shares held
    now, starting at the previous week's last close. Each day's last point is that day's value
    in `series`, so the curve ends on the same figures as the day's plus/minus.
    None once the week's trades have happened (the holdings changed) or with no 30-minute prices.
    Points are [minutes into the trading week, label, account, index fund]."""
    if bars is None or series is None or len(series) < 2 or ledger.get("last_processed") is None:
        return None
    b = cfg.get("benchmark", BENCHMARK)
    if b not in bars:
        return None
    week0 = series.index[-1].to_period("W-FRI").start_time
    if pd.Timestamp(ledger["last_rebalance"]) >= week0:
        return None
    before = series[series.index < week0]
    if before.empty:
        return None
    base = before.index[-1]
    pos = ledger["positions"]
    cols = list(pos) + [b]
    days = bars.index.tz_localize(None).normalize()
    keep = (days > base) & (days <= series.index[-1]) & (bars.index < now_et)
    bars, days = bars.loc[keep].reindex(columns=cols), days[keep]
    if bars.empty:
        return None
    bars = bars.ffill().fillna(prices.ffill().loc[base].reindex(cols))
    st = series[["cash", "units"]].reindex(days, method="ffill")
    stocks = sum(bars[t] * p["shares"] for t, p in pos.items()) if pos else 0.0
    curve = pd.DataFrame({"acct": st["cash"].values + stocks, "bench": st["units"].values * bars[b].values},
                         index=bars.index)
    for d in days.unique():                       # each day ends on its value in `series`
        if d in series.index:
            curve.loc[curve.index[days == d][-1], ["acct", "bench"]] = series.loc[d, ["acct", "bench"]].values
    pts = [[0, f"{base:%a %b %d} close", round(before["acct"].iloc[-1], 2), round(before["bench"].iloc[-1], 2)]]
    for ts, r in curve.iterrows():
        end = min(ts + pd.Timedelta(minutes=30), ts.normalize() + pd.Timedelta(hours=16), pd.Timestamp(now_et))
        minute = end.hour * 60 + end.minute - (MARKET_OPEN_ET.hour * 60 + MARKET_OPEN_ET.minute)
        label = f"{end:%a} {end.hour % 12 or 12}:{end:%M}{'am' if end.hour < 12 else 'pm'}"
        pts.append([end.weekday() * 390 + minute, label, round(r.acct, 2), round(r.bench, 2)])
    return pts if len(pts) > 1 else None


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_ledger_csv(path, cols, through):
    """Existing rows, minus any written after `through` by a run that crashed."""
    if not os.path.exists(path) or through is None:
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"date": str})
    return df[df["date"] <= through][cols]


def save(p, state, ledger, trades, values):
    atomic_write(p["trades"], trades.to_csv(index=False))
    atomic_write(p["values"], values.to_csv(index=False))
    atomic_write(p["state"], json.dumps({**state, "ledger": ledger}, indent=2))   # last: commits the run


# --------------------------------------------------------------------------
def status_for_display(prices, cfg, ledger):
    """What the dashboard needs, from the latest settled prices."""
    b = cfg.get("benchmark", BENCHMARK)
    stocks = [c for c in prices.columns if c != b]
    bench = prices[b]
    sma = bench.rolling(cfg["sma_days"]).mean()
    mom = prices[stocks] / prices[stocks].shift(cfg["lookback"]) - 1
    last = prices.index[-1]
    m = mom.loc[last].dropna()
    ranks = m.rank(ascending=False)
    last_px = prices.ffill().loc[last]
    holdings = []
    for t, p in ledger["positions"].items():
        price = last_px[t]
        holdings.append(dict(ticker=t, shares=p["shares"], buy_price=p["buy_price"],
                             buy_date=p["buy_date"], price=price, value=p["shares"] * price,
                             gain_pct=(price / p["buy_price"] - 1) * 100,
                             rank=int(ranks[t]) if t in ranks else None,
                             mom=m.get(t)))
    holdings.sort(key=lambda h: -h["value"])
    status = dict(last_day=last, risk_on=bool(bench[last] > sma[last]), qqq=bench[last],
                  sma=sma[last], cash=ledger["cash"], universe=len(m))
    watch = m.sort_values(ascending=False).head(cfg["exit_rank"])
    return holdings, status, watch


def notify(title, message):
    """macOS notification banner (silently skipped elsewhere)."""
    import subprocess
    q = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(["osascript", "-e", f'display notification "{q(message)}" '
                        f'with title "{q(title)}" sound name "Glass"'], check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def get_prices(tickers, start, benchmark=BENCHMARK, attempts=3, wait=60):
    """Download with retries; refuse data that's clearly incomplete."""
    import time as _time
    problem = None
    for i in range(attempts):
        try:
            closes, splits, divs = download(tickers, start)
            missing = [t for t in tickers if t not in closes]
            if benchmark in missing:
                problem = f"Yahoo Finance returned no {benchmark} prices"
            elif len(missing) > len(tickers) * 0.1:
                problem = f"Yahoo Finance returned no prices for {len(missing)} of {len(tickers)} stocks"
            else:
                return closes, splits, divs
        except Exception as e:                       # network errors, Yahoo hiccups
            problem = f"price download failed ({type(e).__name__}: {e})"
        if i < attempts - 1:
            print(f"  {problem} - retrying in {wait}s")
            _time.sleep(wait)
    raise DataError(problem)


def week_line(trades_this_week, risk_on, top_n, started):
    sells = [t for t in trades_this_week if t["action"] == "SELL"]
    buys = [t for t in trades_this_week if t["action"] == "BUY"]
    if started and buys:
        return f"Started: bought {len(buys)}."
    if sells and not buys and not risk_on:
        return f"Sold all {len(sells)} (safety switch)." if len(sells) > 1 else "Sold 1 (safety switch)."
    parts = []
    if buys:
        parts.append(f"bought {len(buys)}")
    if sells:
        parts.append(f"sold {len(sells)}")
    if parts:
        s = " and ".join(parts)
        return s[0].upper() + s[1:] + "."
    return "No trades." if risk_on else "Still in cash."


def run_market(m, a, cutoff, now=None):
    p = paths(m["slug"])
    os.makedirs(p["folder"], exist_ok=True)
    saved = json.load(open(p["state"])) if os.path.exists(p["state"]) else None
    state = {k: v for k, v in (saved or {}).items() if k != "ledger"}
    cfg = {**DEFAULTS, **m, **({"start_date": state["start_date"]} if "start_date" in state else {})}
    ledger = (saved or {}).get("ledger")
    b = cfg["benchmark"]
    anchor = date.fromisoformat((ledger or {}).get("last_processed") or cfg.get("start_date") or str(cutoff))

    held = set((ledger or {}).get("positions", {}))
    tickers = sorted((set(cfg["tickers"]) | held) - {b}) + [b]
    if a.prices_csv:
        closes, splits, divs = load_csv_prices(a.prices_csv)
    else:
        closes, splits, divs = get_prices(tickers, anchor - timedelta(days=HISTORY_DAYS), b)
    if b not in closes:
        raise DataError(f"no {b} prices")
    gone = sorted(held - set(closes.columns))
    if gone:
        raise DataError(f"no prices at all for stocks you hold: {', '.join(gone)}")
    data_through = str(closes.index[-1].date())
    prices = clean(closes, b)
    prices = prices[prices.index.date < cutoff]
    if prices.empty:
        raise DataError("no finished trading days in the price data")

    if ledger is None and cfg.get("start_date") and prices.index[-1] < pd.Timestamp(cfg["start_date"]):
        # Not started yet: show the waiting page until its first trading day closes.
        bt = None if a.prices_csv else insights.backtest(cfg, p["backtest"])
        atomic_write(p["page"], dashboard.build_waiting(cfg, bt, MARKETS))
        print(f"[{m['name']}] starts at the close on {cfg['start_date']}; page shows its backtest")
        return None
    if ledger is None:
        if "start_date" not in cfg:
            cfg["start_date"] = str(prices.index[-1].date())
            print(f"New paper account: {cfg['symbol']}{cfg['capital']:,.2f} starting {cfg['start_date']}")
        else:
            print(f"Building the ledger from {cfg['start_date']}")
        first = prices.index[prices.index >= pd.Timestamp(cfg["start_date"])][0]
        ledger = new_ledger(first.date(), prices.at[first, b], cfg["capital"], cfg["dividends"])
    state["start_date"] = cfg["start_date"]

    if not a.prices_csv:
        check_week_end_closes(prices.index, ledger["last_processed"], cutoff, b)
    old_trades = read_ledger_csv(p["trades"], TRADE_COLS, ledger["last_processed"])
    old_values = read_ledger_csv(p["values"], VALUE_COLS, ledger["last_processed"])
    new_trades, new_values, notes = advance(prices, splits, cfg, ledger, cutoff, divs)
    ledger["data_through"] = data_through
    trades = pd.concat([old_trades, pd.DataFrame(new_trades, columns=TRADE_COLS)], ignore_index=True)
    values = pd.concat([old_values, pd.DataFrame(new_values, columns=VALUE_COLS)], ignore_index=True)
    save(p, state, ledger, trades, values)

    # ---- page and Home summary
    acct = values.assign(date=pd.to_datetime(values["date"])).set_index("date")
    shown = trades.assign(date=pd.to_datetime(trades["date"]).dt.date)
    settled = prices.loc[:ledger["last_processed"]]
    holdings, status, watch = status_for_display(settled, cfg, ledger)
    last_rebal = pd.Timestamp(ledger["last_rebalance"])
    bt = None if a.prices_csv else insights.backtest(cfg, p["backtest"])
    # The day's and week's plus/minus: shown only, never recorded. While the market is open
    # the latest day is today, valued at the latest prices.
    now = now or now_eastern()
    bars = None
    if not a.prices_csv and not a.today and now.weekday() < 5:
        try:
            bars = download_intraday(sorted(ledger["positions"]) + [b])
        except Exception as e:               # the 30-minute prices are a nice-to-have
            print(f"  (no 30-minute prices: {type(e).__name__}: {e})")
    shown_px = prices
    try:
        live = None if a.prices_csv or a.today else live_prices(closes, bars, now, b)
        shown_px = prices if live is None else pd.concat([prices, live.to_frame().T])
        series = valued_days(shown_px, cfg, ledger, values, divs) if ledger["last_processed"] else None
        day = day_change(shown_px, cfg, ledger, values, divs, series)
        curve = week_curve(bars, series, shown_px, cfg, ledger, now)
        if day and live is not None:
            day.update(live=True, as_of=f"{now:%Y-%m-%dT%H:%M}")
        if day:
            day["switch"] = switch_now(shown_px, cfg)
    except Exception as e:                   # shown-only figures must never stop the page
        print(f"  (no plus/minus this run: {type(e).__name__}: {e})")
        day, curve = None, None
    html, now_val, bench_val, week = dashboard.build_dashboard(
        cfg, acct, shown, holdings, status, watch, last_rebal, settled, bt, MARKETS, ledger, day, curve,
        latest=shown_px)
    atomic_write(p["page"], html)
    started = last_rebal.normalize() == pd.Timestamp(cfg["start_date"]).normalize()
    summary = dict(value=now_val, bench_value=bench_val, start=cfg["start_date"], capital=cfg["capital"],
                   risk_on=status["risk_on"], held=len(holdings), last_day=str(status["last_day"].date()),
                   week=week_line(week.to_dict("records"), status["risk_on"], cfg["top_n"], started),
                   spark=[[r.paper_account, r.qqq_same_money] for r in values.tail(60).itertuples()],
                   day=day, updated=str(date.today()))
    atomic_write(p["summary"], json.dumps(summary, default=float))
    dashboard._SUMMARIES[m["slug"]] = json.loads(json.dumps(summary, default=float))

    sym = cfg["symbol"]
    print(f"[{m['name']}] prices settled through {ledger['last_processed']}")
    for n in notes:
        print(f"  Applied stock split: {n}")
    print(f"  Paper account: {sym}{now_val:,.2f}   Same money in {cfg['bench_label']}: {sym}{bench_val:,.2f}")
    if day:
        when = f"{day['day']} so far (live {now:%H:%M})" if day.get("live") else day["day"]
        print(f"  {when}: {day['change']:+,.2f} ({day['pct']:+.2f}%)   {cfg['bench_label']} {day['bench_pct']:+.2f}%"
              f"{'' if day['recorded'] else '   (shown only; not in the record yet)'}")
        if "week_change" in day:
            print(f"  Week so far: {day['week_change']:+,.2f} ({day['week_pct']:+.2f}%)   "
                  f"{cfg['bench_label']} {day['week_bench_pct']:+.2f}%")
    sw = "INVESTED" if status["risk_on"] else "CASH"
    if status["risk_on"] != bool(holdings):      # acted on only at the week's last close
        sw += f" at the week's last close if it stays that way (now {f'holding {len(holdings)}' if holdings else 'in cash'})"
    print(f"  Safety switch: {sw}")
    print(f"  New trades this run: {len(new_trades)}")
    for t in new_trades:
        print(f"    {t['date']}  {t['action']:<4} {t['ticker']:<8} at {sym}{t['price']:,.2f}")
    return p["page"]


def run_waiting_market(m):
    """A market that isn't trading yet: its page shows the backtest so you can decide."""
    p = paths(m["slug"])
    cfg = {**DEFAULTS, **m}
    bt = insights.backtest(cfg, p["backtest"])
    atomic_write(p["page"], dashboard.build_waiting(cfg, bt, MARKETS))
    print(f"[{m['name']}] not started yet; page shows its backtest")


def last_processed(slug):
    p = paths(slug)["state"]
    if not os.path.exists(p):
        return None
    return (json.load(open(p)).get("ledger") or {}).get("last_processed")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Pixel Invest - safe to run as often as you like")
    ap.add_argument("--market", help="only this market (its slug, e.g. tsx-60)")
    ap.add_argument("--prices-csv", help="use saved prices instead of downloading (testing)")
    ap.add_argument("--today", help="pretend today's date is YYYY-MM-DD, before the close (testing)")
    ap.add_argument("--now", help="pretend it's this New York time, e.g. '2026-11-18 14:00', to see the "
                                  "live estimate as it was then (testing; prices after it are ignored)")
    ap.add_argument("--no-open", action="store_true", help="don't open the page")
    ap.add_argument("--auto", action="store_true",
                    help="background mode: no browser; fails only after a week-long outage")
    a = ap.parse_args()
    print(f"--- {datetime.now():%Y-%m-%d %H:%M} ---")
    now = now_eastern()
    if a.now:
        now = datetime.fromisoformat(a.now).replace(tzinfo=now.tzinfo)
    cutoff = date.fromisoformat(a.today) if a.today else final_cutoff(now)

    failed, opened = [], None
    dashboard.load_summaries(MARKETS, lambda slug: paths(slug)["summary"])   # menu statuses
    for m in MARKETS:
        if a.market and m["slug"] != a.market:
            continue
        try:
            if m.get("active", True):
                page = run_market(m, a, cutoff, now)
                opened = opened or page
            else:
                run_waiting_market(m)
        except Exception as e:            # nothing is saved for that market; others carry on
            msg = f"{e}" if isinstance(e, DataError) else f"{type(e).__name__}: {e}"
            print(f"[{m['name']}] stopped, nothing was changed: {msg}")
            if not a.auto:
                raise
            if m.get("active", True):
                last = last_processed(m["slug"])
                if last is None or (date.today() - date.fromisoformat(last)).days > STALE_DAYS:
                    failed.append(f"{m['name']} hasn't updated since {last or 'it started'}: {msg[:100]}")

    fx = get_fx(download=not a.prices_csv)
    home = dashboard.build_home(MARKETS, lambda slug: paths(slug)["summary"], fx)
    atomic_write(os.path.join(DOCS, "index.html"), home)
    print(f"Home page: {os.path.join(DOCS, 'index.html')}")

    if failed:
        notify("Pixel Invest needs a look", " / ".join(failed))
        print("\n".join(failed))
        sys.exit(1)           # a real outage: fail loudly (GitHub emails you)
    if not a.auto and not a.no_open:
        webbrowser.open("file://" + os.path.join(DOCS, "index.html"))


if __name__ == "__main__":
    main()
