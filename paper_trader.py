"""
Paper Trader - Nasdaq-100 weekly momentum (3-month version)
==========================================================

Run once a week, ideally on the weekend after Friday's close:
    ./run_paper_trader.command        (Mac: double-click it in Finder)
    .venv/bin/python paper_trader.py  (or from a terminal)

What it does
  1. Downloads recent prices from Yahoo Finance.
  2. Moves the paper account forward from where it stopped last time:
       - rank Nasdaq-100 stocks by their 3-month (63 trading day) return
       - hold the top 10, equal money in each new position
       - sell a holding when it falls below rank 20
       - safety switch: all cash when QQQ closes below its 200-day average
     Trades happen at the closing price on the last trading day of each
     finished week. Nothing is connected to any real brokerage account.
  3. Writes a dashboard (paper_trading/dashboard.html) and opens it.

Files it keeps in the "paper_trading" folder
  paper_account.json   settings + the current portfolio (cash, shares)
  trades.csv           every paper trade - only ever added to, never redone
  account_value.csv    daily account value vs QQQ - also only added to
  dashboard.html       the page you look at

Past trades are a permanent record: later price revisions or edits to the
stock list never change them. Stock splits are applied to shares you hold.

To start over: delete paper_trading/paper_account.json.
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
from momentum_backtest import NASDAQ_100, BENCHMARK  # same stock list
import dashboard
import insights

FOLDER = os.path.join(HERE, "paper_trading")
STATE = os.path.join(FOLDER, "paper_account.json")
TRADES = os.path.join(FOLDER, "trades.csv")
VALUES = os.path.join(FOLDER, "account_value.csv")
BACKTEST = os.path.join(FOLDER, "backtest.json")      # monthly backtest cache
PAGE = os.path.join(HERE, "docs", "index.html")      # the public web page (GitHub Pages)

DEFAULTS = {"capital": 10_000.0, "top_n": 10, "exit_rank": 20,
            "lookback": 63, "sma_days": 200, "cost_pct": 0.0}
TRADE_COLS = ["date", "action", "ticker", "shares", "price", "value", "gain_pct", "reason"]
VALUE_COLS = ["date", "paper_account", "cash", "positions", "qqq_same_money"]
HISTORY_DAYS = 450        # calendar days of prices needed before the first day we process
MARKET_CLOSE_ET = time(16, 30)


class DataError(RuntimeError):
    pass


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
    return closes, splits


def load_csv_prices(path):
    """Testing helper: closes CSV, plus optional '<name>_splits.csv' beside it."""
    closes = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    sp = path.replace(".csv", "_splits.csv")
    splits = (pd.read_csv(sp, index_col=0, parse_dates=True).sort_index()
              if os.path.exists(sp) else pd.DataFrame(0.0, index=closes.index, columns=closes.columns))
    return closes, splits.reindex(index=closes.index, columns=closes.columns).fillna(0.0)


def clean(prices):
    """Drop unfinished/partial days (no QQQ price or most stocks missing)."""
    prices = prices[prices[BENCHMARK].notna()]
    prices = prices[prices.notna().mean(axis=1) > 0.8]
    return prices.ffill(limit=5)        # ride over short data gaps


def final_cutoff(now_et):
    """Days strictly before this date have a final closing price."""
    d = now_et.date()
    return d + timedelta(days=1) if now_et.time() >= MARKET_CLOSE_ET else d


def now_eastern():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


# --------------------------------------------------------------------------
# Ledger engine
# --------------------------------------------------------------------------
def new_ledger(start_day, qqq_price, capital):
    return {"start": str(start_day), "qqq_start": float(qqq_price), "cash": float(capital),
            "positions": {}, "last_processed": None, "last_rebalance": None,
            "data_through": None}


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


def advance(prices, splits, cfg, ledger, cutoff):
    """Move the ledger forward through every settled day after last_processed.

    Returns (new_trades, new_values, split_notes). Mutates `ledger`.
    """
    stocks = [c for c in prices.columns if c != BENCHMARK]
    bench = prices[BENCHMARK]
    sma = bench.rolling(cfg["sma_days"]).mean()
    mom = prices[stocks] / prices[stocks].shift(cfg["lookback"]) - 1
    last_px = prices.ffill()            # value held stocks at their last known price

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
        if day in rebal:
            if pd.isna(sma[day]):
                raise DataError(f"Not enough QQQ history for the {cfg['sma_days']}-day average on {day.date()}")
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
                    why = "Safety switch: QQQ below 200-day avg"
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
        values.append(dict(date=str(day.date()), paper_account=round(val, 2),
                           cash=round(ledger["cash"], 2), positions=len(pos),
                           qqq_same_money=round(cfg["capital"] * bench[day] / ledger["qqq_start"], 2)))
        ledger["last_processed"] = str(day.date())
    return trades, values, notes


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def atomic_write(path, text):
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


def save(cfg, ledger, trades, values):
    atomic_write(TRADES, trades.to_csv(index=False))
    atomic_write(VALUES, values.to_csv(index=False))
    atomic_write(STATE, json.dumps({**cfg, "ledger": ledger}, indent=2))   # last: commits the run


# --------------------------------------------------------------------------
def status_for_display(prices, cfg, ledger):
    """What the dashboard needs, from the latest settled prices."""
    stocks = [c for c in prices.columns if c != BENCHMARK]
    bench = prices[BENCHMARK]
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


def get_prices(tickers, start, attempts=3, wait=60):
    """Download with retries; refuse data that's clearly incomplete."""
    import time as _time
    problem = None
    for i in range(attempts):
        try:
            closes, splits = download(tickers, start)
            missing = [t for t in tickers if t not in closes]
            if BENCHMARK in missing:
                problem = "Yahoo Finance returned no QQQ prices"
            elif len(missing) > len(tickers) * 0.1:
                problem = f"Yahoo Finance returned no prices for {len(missing)} of {len(tickers)} stocks"
            else:
                return closes, splits
        except Exception as e:                       # network errors, Yahoo hiccups
            problem = f"price download failed ({type(e).__name__}: {e})"
        if i < attempts - 1:
            print(f"  {problem} - retrying in {wait}s")
            _time.sleep(wait)
    raise DataError(problem)


def run(a):
    os.makedirs(FOLDER, exist_ok=True)
    cutoff = date.fromisoformat(a.today) if a.today else final_cutoff(now_eastern())

    saved = json.load(open(STATE)) if os.path.exists(STATE) else None
    cfg = {**DEFAULTS, **{k: v for k, v in (saved or {}).items() if k != "ledger"}}
    ledger = (saved or {}).get("ledger")
    anchor = date.fromisoformat((ledger or {}).get("last_processed") or cfg.get("start_date") or str(cutoff))

    held = set((ledger or {}).get("positions", {}))
    tickers = sorted((set(NASDAQ_100) | held) - {BENCHMARK}) + [BENCHMARK]
    if a.prices_csv:
        closes, splits = load_csv_prices(a.prices_csv)
    else:
        closes, splits = get_prices(tickers, anchor - timedelta(days=HISTORY_DAYS))
    if BENCHMARK not in closes:
        raise DataError("no QQQ prices")
    gone = sorted(held - set(closes.columns))
    if gone:
        raise DataError(f"no prices at all for stocks you hold: {', '.join(gone)}")
    data_through = str(closes.index[-1].date())
    prices = clean(closes)
    prices = prices[prices.index.date < cutoff]
    if prices.empty:
        raise DataError("no finished trading days in the price data")

    if ledger is None:
        if "start_date" not in cfg:
            cfg["start_date"] = str(prices.index[-1].date())
            print(f"New paper account: ${cfg['capital']:,.2f} starting {cfg['start_date']}")
        else:
            print(f"Building the ledger from {cfg['start_date']} (first run of this version)")
        first = prices.index[prices.index >= pd.Timestamp(cfg["start_date"])][0]
        ledger = new_ledger(first.date(), prices.at[first, BENCHMARK], cfg["capital"])

    old_trades = read_ledger_csv(TRADES, TRADE_COLS, ledger["last_processed"])
    old_values = read_ledger_csv(VALUES, VALUE_COLS, ledger["last_processed"])
    new_trades, new_values, notes = advance(prices, splits, cfg, ledger, cutoff)
    ledger["data_through"] = data_through
    trades = pd.concat([old_trades, pd.DataFrame(new_trades, columns=TRADE_COLS)], ignore_index=True)
    values = pd.concat([old_values, pd.DataFrame(new_values, columns=VALUE_COLS)], ignore_index=True)
    save(cfg, ledger, trades, values)

    # ---- dashboard
    acct = values.assign(date=pd.to_datetime(values["date"])).set_index("date")
    shown = trades.assign(date=pd.to_datetime(trades["date"]).dt.date)
    settled = prices.loc[:ledger["last_processed"]]
    holdings, status, watch = status_for_display(settled, cfg, ledger)
    last_rebal = pd.Timestamp(ledger["last_rebalance"])
    bt = None if a.prices_csv else insights.backtest(cfg, BACKTEST)
    path, now_val, qqq_val, _ = dashboard.build_dashboard(
        cfg, acct, shown, holdings, status, watch, last_rebal, settled, FOLDER, bt)
    os.makedirs(os.path.dirname(PAGE), exist_ok=True)
    shutil.copyfile(path, PAGE)

    print(f"Prices settled through {ledger['last_processed']}")
    for n in notes:
        print(f"  Applied stock split: {n}")
    print(f"Paper account: ${now_val:,.2f}   Same money in QQQ: ${qqq_val:,.2f}")
    print(f"Safety switch: {'INVESTED' if status['risk_on'] else 'CASH'}")
    print(f"New trades this run: {len(new_trades)}")
    for t in new_trades:
        print(f"  {t['date']}  {t['action']:<4} {t['ticker']:<6} at ${t['price']:,.2f}")
    print(f"Dashboard: {path}")
    return dict(path=path, trades=new_trades, now=now_val, qqq=qqq_val,
                risk_on=status["risk_on"], start=cfg["capital"])


def trade_message(r):
    buys = [t["ticker"] for t in r["trades"] if t["action"] == "BUY"]
    sells = [t["ticker"] for t in r["trades"] if t["action"] == "SELL"]
    parts = []
    if sells:
        parts.append(f"Sold {len(sells)}: {', '.join(sells[:5])}{'...' if len(sells) > 5 else ''}")
    if buys:
        parts.append(f"Bought {len(buys)}: {', '.join(buys[:5])}{'...' if len(buys) > 5 else ''}")
    if not r["risk_on"]:
        parts.append("Now in cash (safety switch)")
    ret = (r["now"] / r["start"] - 1) * 100
    parts.append(f"Account ${r['now']:,.0f} ({ret:+.1f}%)")
    return ". ".join(parts)


STALE_DAYS = 8      # only bother you if the account hasn't updated for this long


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Paper trader - safe to run as often as you like")
    ap.add_argument("--prices-csv", help="use saved prices instead of downloading (testing)")
    ap.add_argument("--today", help="pretend today's date is YYYY-MM-DD, before the close (testing)")
    ap.add_argument("--no-open", action="store_true", help="don't open the dashboard")
    ap.add_argument("--auto", action="store_true",
                    help="background mode: no browser, only speaks up after a week-long outage")
    a = ap.parse_args()
    print(f"--- {datetime.now():%Y-%m-%d %H:%M} ---")
    try:
        r = run(a)
    except Exception as e:                            # nothing is saved on any failure
        msg = f"{e}" if isinstance(e, DataError) else f"{type(e).__name__}: {e}"
        print(f"Stopped, nothing was changed: {msg}")
        if a.auto:
            last = None
            if os.path.exists(STATE):
                last = (json.load(open(STATE)).get("ledger") or {}).get("last_processed")
            if last is None or (date.today() - date.fromisoformat(last)).days > STALE_DAYS:
                notify("Paper Trader needs a look",
                       f"Hasn't updated since {last or 'it started'}. Last error: {msg[:120]}")
                sys.exit(1)       # a real outage: fail loudly (GitHub emails you)
            sys.exit(0)           # a blip: tomorrow's run catches up on its own
        raise
    if not a.auto and not a.no_open:
        webbrowser.open("file://" + r["path"])


if __name__ == "__main__":
    main()
