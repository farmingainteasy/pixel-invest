"""Numbers behind the dashboard's charts: rank history, the safety switch,
and a backtest of the same rules (cached; refreshed about once a month)."""

import json
import os
from datetime import date

import numpy as np
import pandas as pd

from momentum_backtest import load_prices, run_backtest, stats

BACKTEST_START = "2016-01-01"
BACKTEST_COST = 0.001          # 0.1% per trade: a little friction, to stay honest
REFRESH_DAYS = 28


def week_ends(index, through):
    s = pd.Series(index[index <= through], index=index[index <= through])
    return [g.index[-1] for _, g in s.groupby(s.dt.to_period("W-FRI"))]


def rank_history(prices, cfg, weeks=26):
    """Momentum rank of every stock at the end of each of the last `weeks` weeks."""
    stocks = [c for c in prices.columns if c != cfg["benchmark"]]
    mom = prices[stocks] / prices[stocks].shift(cfg["lookback"]) - 1
    ends = week_ends(prices.index, prices.index[-1])[-weeks:]
    ranks = pd.DataFrame({d: mom.loc[d].dropna().rank(ascending=False) for d in ends}).T
    return ranks, mom.loc[prices.index[-1]]


def held_by_week(trades, ends):
    """{week end -> set of tickers held at that close}, from the trade ledger."""
    t = trades.copy()
    t["date"] = pd.to_datetime(t["date"])
    t = t.sort_values("date", kind="stable")
    held, out, i, rows = set(), {}, 0, t.to_dict("records")
    for d in ends:
        while i < len(rows) and rows[i]["date"] <= d:
            (held.add if rows[i]["action"] == "BUY" else held.discard)(rows[i]["ticker"])
            i += 1
        out[d] = set(held)
    return out


def switch_series(prices, cfg, days=260):
    bench = prices[cfg["benchmark"]]
    sma = bench.rolling(cfg["sma_days"]).mean()
    df = pd.DataFrame({"qqq": bench, "sma": sma}).dropna().iloc[-days:]
    return df


# --------------------------------------------------------------------------
def _round_trips(trades):
    open_px, gains = {}, []
    for r in trades.itertuples():
        if r.action == "BUY":
            open_px[r.ticker] = r.price
        elif r.ticker in open_px:
            gains.append(r.price / open_px.pop(r.ticker) - 1)
    return gains


def load_backtest_prices(cfg):
    """Dividend-adjusted closes since BACKTEST_START (plus a year of warm-up)."""
    b = cfg["benchmark"]
    tickers = sorted(set(cfg["tickers"]) - {b}) + [b]
    prices = load_prices(tickers, BACKTEST_START, None)
    return prices[prices[b].notna()].ffill(limit=5)


def compute_backtest(cfg, prices=None):
    b = cfg["benchmark"]
    if prices is None:
        prices = load_backtest_prices(cfg)
    eq, trades, _, _ = run_backtest(prices, BACKTEST_START, cfg["top_n"], cfg["exit_rank"],
                                    cfg["lookback"], cfg["sma_days"], BACKTEST_COST, 10_000.0,
                                    benchmark=b)
    weekly = eq[["strategy", "qqq_buy_hold"]].resample("W-FRI").last().dropna()
    s, q = stats(eq["strategy"]), stats(eq["qqq_buy_hold"])
    gains = _round_trips(trades)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cash_weeks = (eq["positions"].resample("W-FRI").last() == 0).mean()
    return {
        "computed_on": str(date.today()),
        "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
        "curve": [[str(d.date()), round(a, 2), round(b, 2)] for d, (a, b) in weekly.iterrows()],
        "rules": {"cagr": s["Yearly return (CAGR)"], "drop": s["Worst drop (max drawdown)"],
                  "total": s["Total return"]},
        "qqq": {"cagr": q["Yearly return (CAGR)"], "drop": q["Worst drop (max drawdown)"],
                "total": q["Total return"]},
        "win_rate": float(np.mean([g > 0 for g in gains])) if gains else None,
        "avg_win": float(np.mean([g for g in gains if g > 0])) if gains else None,
        "avg_loss": float(np.mean([g for g in gains if g <= 0])) if gains else None,
        "trades_per_year": len(trades) / years if years else None,
        "cash_share": float(cash_weeks),
        "cost_pct": BACKTEST_COST,
    }


def backtest(cfg, path, today=None, refresh_days=REFRESH_DAYS):
    """Cached backtest; recomputed when missing or older than `refresh_days`.
    Any failure returns the old copy (or None) - the dashboard just skips it."""
    today = today or date.today()
    old = None
    if os.path.exists(path):
        try:
            old = json.load(open(path))
        except ValueError:
            old = None
    params = [cfg["top_n"], cfg["exit_rank"], cfg["lookback"], cfg["sma_days"], cfg["benchmark"]]
    if (old and old.get("params") == params
            and (today - date.fromisoformat(old["computed_on"])).days < refresh_days):
        return old
    try:
        print("Refreshing the backtest (about once a month)...")
        new = {**compute_backtest(cfg), "params": params}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(new, f)
        os.replace(tmp, path)
        return new
    except Exception as e:                       # never let this stop the weekly run
        print(f"  Backtest skipped: {type(e).__name__}: {e}")
        return old
