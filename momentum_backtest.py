"""
Nasdaq-100 Weekly Momentum Backtest
===================================

Rules being tested (agreed starting strategy):
  1. Universe: Nasdaq-100 companies.
  2. Every week (last trading day of the week): rank them by their return
     over the past ~3 months (63 trading days), as the paper trader does.
  3. Hold the top 10, equal money in each new position.
  4. Sell any holding that falls out of the top 20; replace it with the
     highest-ranked stock not already held.
  5. Safety switch: if QQQ (the Nasdaq-100 fund) closes below its 200-day
     average, sell everything and sit in cash until it closes back above.

Compared against simply buying and holding QQQ.

Usage (Windows, in a terminal opened in this folder):
    pip install yfinance pandas matplotlib
    python momentum_backtest.py
    python momentum_backtest.py --start 2012-01-01 --top 10 --exit-rank 20

Results go to the "results" folder: summary.txt, equity_curve.png,
yearly_returns.csv, trades.csv, current_picks.csv.

IMPORTANT LIMITS
  * Survivorship bias: the ticker list is TODAY's Nasdaq-100. Companies
    that were in the index years ago and then fell out (or went bust) are
    missing, so the backtest looks better than reality would have been.
  * Past results do not predict future results. Use this to compare
    rule ideas, then paper trade before risking real money.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

# Approximate current Nasdaq-100 members. Edit freely, or pass --tickers-file.
NASDAQ_100 = """
AAPL MSFT NVDA AMZN META AVGO GOOGL TSLA COST NFLX PLTR AMD ASML CSCO TMUS
AZN LIN INTU ISRG PEP TXN BKNG QCOM AMGN ADBE AMAT SHOP HON ARM PDD GILD MU
PANW CMCSA ADP LRCX KLAC APP ADI CRWD SBUX MELI VRTX INTC DASH CEG MSTR ORLY
CTAS SNPS CDNS MDLZ ABNB MAR FTNT PYPL WDAY REGN ADSK MNST CSX ROP AEP CHTR
AXON NXPI FAST PAYX PCAR DDOG KDP ZS IDXX EXC TTWO CPRT ROST VRSK BKR XEL
CCEP FANG EA TTD GEHC CTSH KHC ODFL DXCM MCHP CSGP LULU ON BIIB WBD GFS CDW
TEAM MDB SPCX
""".split()

BENCHMARK = "QQQ"


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def load_prices(tickers, start, end, csv_path=None):
    """Return a DataFrame of daily adjusted closes (dates x tickers)."""
    if csv_path:
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return df.sort_index()

    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance is not installed. Run:  pip install yfinance")

    # Download extra history before the start date so the momentum and
    # 200-day lookbacks are ready on day one.
    fetch_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    print(f"Downloading {len(tickers)} tickers from Yahoo Finance...")
    raw = yf.download(tickers, start=fetch_start, end=end, auto_adjust=True,
                      progress=False, threads=True)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    closes = closes.dropna(how="all", axis=1).sort_index()
    missing = sorted(set(tickers) - set(closes.columns))
    if missing:
        print(f"  No data for: {', '.join(missing)} (skipped)")
    return closes


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------
def run_backtest(prices, start, top_n=10, exit_rank=20, lookback=63,
                 sma_days=200, cost_pct=0.001, capital=10_000.0,
                 use_safety_switch=True):
    stocks = [c for c in prices.columns if c != BENCHMARK]
    bench = prices[BENCHMARK]
    sma = bench.rolling(sma_days).mean()
    momentum = prices[stocks] / prices[stocks].shift(lookback) - 1

    dates = prices.index[prices.index >= pd.Timestamp(start)]
    # Rebalance on the last trading day of each week.
    week_id = dates.to_series().dt.to_period("W-FRI")
    rebal_days = set(week_id.groupby(week_id).apply(lambda s: s.index[-1]))

    cash = capital
    shares = {}          # ticker -> number of shares
    trades = []
    equity = []

    for day in dates:
        px = prices.loc[day]

        if day in rebal_days:
            risk_on = (not use_safety_switch) or (
                pd.notna(sma[day]) and bench[day] > sma[day])

            if not risk_on:
                targets_exit = list(shares)
                buy_list = []
            else:
                mom = momentum.loc[day].dropna()
                mom = mom[px[mom.index].notna()]
                ranks = mom.rank(ascending=False)
                targets_exit = [t for t in shares
                                if t not in ranks or ranks[t] > exit_rank]
                keep = [t for t in shares if t not in targets_exit]
                open_slots = top_n - len(keep)
                candidates = [t for t in ranks.sort_values().index
                              if t not in keep][:max(open_slots, 0)]
                buy_list = candidates

            # Sells
            for t in targets_exit:
                price = px.get(t)
                if pd.isna(price):          # no price today: use last known
                    price = prices[t].loc[:day].dropna().iloc[-1]
                value = shares[t] * price
                cash += value * (1 - cost_pct)
                trades.append((day, "SELL", t, shares[t], price, value))
                del shares[t]

            # Buys: split available cash evenly across the open slots
            if buy_list:
                per_stock = cash / len(buy_list)
                for t in buy_list:
                    price = px[t]
                    qty = per_stock * (1 - cost_pct) / price
                    shares[t] = qty
                    cash -= per_stock
                    trades.append((day, "BUY", t, qty, price, per_stock))

        value = cash + sum(q * (px[t] if pd.notna(px[t])
                                else prices[t].loc[:day].dropna().iloc[-1])
                           for t, q in shares.items())
        equity.append((day, value, len(shares)))

    eq = pd.DataFrame(equity, columns=["date", "strategy", "positions"]).set_index("date")
    eq["qqq_buy_hold"] = capital * bench.loc[dates] / bench.loc[dates[0]]
    trades = pd.DataFrame(trades, columns=["date", "action", "ticker",
                                           "shares", "price", "value"])
    return eq, trades, momentum, sma


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------
def stats(series):
    s = series.dropna()
    if len(s) < 2:
        return {}
    daily = s.pct_change().dropna()
    years = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    dd = (s / s.cummax() - 1).min()
    vol = daily.std() * np.sqrt(252)
    sharpe = (daily.mean() * 252) / vol if vol > 0 else np.nan
    return {"Total return": s.iloc[-1] / s.iloc[0] - 1,
            "Yearly return (CAGR)": cagr,
            "Worst drop (max drawdown)": dd,
            "Volatility": vol,
            "Sharpe ratio": sharpe}


def fmt_stats(name, d):
    lines = [f"  {name}"]
    for k, v in d.items():
        lines.append(f"    {k:<28}{v:>8.2f}" if k == "Sharpe ratio"
                     else f"    {k:<28}{v:>8.1%}")
    return "\n".join(lines)


def period_report(eq, label, start=None, end=None):
    part = eq.loc[start:end]
    if len(part) < 2:
        return ""
    head = f"{label}: {part.index[0].date()} to {part.index[-1].date()}"
    return "\n".join([head,
                      fmt_stats("Momentum strategy", stats(part["strategy"])),
                      fmt_stats("QQQ buy & hold", stats(part["qqq_buy_hold"]))])


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Nasdaq-100 weekly momentum backtest")
    ap.add_argument("--start", default="2012-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--top", type=int, default=10, help="stocks to hold")
    ap.add_argument("--exit-rank", type=int, default=20, help="sell when rank falls below this")
    ap.add_argument("--lookback", type=int, default=63, help="momentum lookback, trading days")
    ap.add_argument("--sma", type=int, default=200, help="safety switch moving average, days")
    ap.add_argument("--no-safety", action="store_true", help="turn off the safety switch")
    ap.add_argument("--cost", type=float, default=0.001, help="cost per trade (0.001 = 0.1%%)")
    ap.add_argument("--capital", type=float, default=10_000)
    ap.add_argument("--holdout-years", type=float, default=2,
                    help="last N years reported separately as an untouched test")
    ap.add_argument("--tickers-file", help="text file of tickers, space or line separated")
    ap.add_argument("--prices-csv", help="use a saved price CSV instead of downloading")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    tickers = NASDAQ_100
    if a.tickers_file:
        tickers = open(a.tickers_file).read().split()
    tickers = sorted(set(tickers) - {BENCHMARK})

    prices = load_prices(tickers + [BENCHMARK], a.start, a.end, a.prices_csv)
    if BENCHMARK not in prices:
        sys.exit("Could not get QQQ prices; cannot run.")
    os.makedirs(a.out, exist_ok=True)
    if not a.prices_csv:
        prices.to_csv(os.path.join(a.out, "prices.csv"))
    # Drop days with no QQQ price (e.g. today's unfinished session) and
    # fill short gaps in individual stocks with their last known price.
    prices = prices[prices[BENCHMARK].notna()].ffill(limit=5)

    eq, trades, momentum, sma = run_backtest(
        prices, a.start, a.top, a.exit_rank, a.lookback, a.sma,
        a.cost, a.capital, not a.no_safety)

    split = eq.index[-1] - pd.DateOffset(years=a.holdout_years)
    in_cash = (eq["positions"] == 0).mean()
    sells = (trades["action"] == "SELL").sum()
    years = (eq.index[-1] - eq.index[0]).days / 365.25

    report = [
        "NASDAQ-100 WEEKLY MOMENTUM BACKTEST",
        "=" * 50,
        f"Rules: top {a.top} by {a.lookback}-day return, sell below rank {a.exit_rank}, "
        f"safety switch {'OFF' if a.no_safety else f'QQQ {a.sma}-day average'}, "
        f"cost {a.cost:.2%} per trade",
        f"Starting money: ${a.capital:,.0f}   Stocks with data: {prices.shape[1] - 1}",
        "",
        f"Final value:  strategy ${eq['strategy'].iloc[-1]:,.0f}   "
        f"QQQ ${eq['qqq_buy_hold'].iloc[-1]:,.0f}",
        f"Trades: {len(trades)} ({len(trades) / years:.0f} per year), "
        f"about {sells / years:.0f} sells a year",
        f"Time spent fully in cash: {in_cash:.0%}",
        "",
        period_report(eq, "FULL PERIOD"),
        "",
        period_report(eq, "TEST PERIOD (before holdout)", end=split),
        "",
        period_report(eq, f"HOLDOUT (last {a.holdout_years:g} years - judge the rules here)",
                      start=split),
        "",
        "WARNING: uses today's Nasdaq-100 list (survivorship bias), so results",
        "are likely better than real life. Past results don't predict the future.",
    ]
    text = "\n".join(report)
    print("\n" + text)
    with open(os.path.join(a.out, "summary.txt"), "w") as f:
        f.write(text)

    # Yearly returns
    yearly = eq[["strategy", "qqq_buy_hold"]].resample("YE").last()
    first = eq[["strategy", "qqq_buy_hold"]].iloc[[0]]
    yearly = pd.concat([first, yearly]).pct_change().dropna()
    yearly.index = yearly.index.year
    yearly.round(4).to_csv(os.path.join(a.out, "yearly_returns.csv"))
    print("\nYearly returns:")
    print((yearly * 100).round(1).astype(str).add("%").to_string())

    trades.to_csv(os.path.join(a.out, "trades.csv"), index=False)

    # What the rules would hold right now (for paper trading)
    last = prices.index[-1]
    bench = prices[BENCHMARK]
    risk_on = a.no_safety or bench[last] > sma[last]
    picks = momentum.loc[last].dropna().sort_values(ascending=False).head(a.exit_rank)
    cur = pd.DataFrame({"rank": range(1, len(picks) + 1),
                        "momentum_return": picks.round(4)})
    cur.to_csv(os.path.join(a.out, "current_picks.csv"))
    print(f"\nAs of {last.date()}: safety switch says "
          f"{'INVESTED' if risk_on else 'CASH'} "
          f"(QQQ {bench[last]:.2f} vs {a.sma}-day avg {sma[last]:.2f})")
    print(f"Current top {a.top}: {', '.join(picks.index[:a.top])}")

    # Chart
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                       gridspec_kw={"height_ratios": [3, 1]})
        ax1.plot(eq.index, eq["strategy"], label="Momentum strategy", color="#1f6feb")
        ax1.plot(eq.index, eq["qqq_buy_hold"], label="QQQ buy & hold", color="#888888")
        ax1.axvline(split, color="#d97706", ls="--", lw=1, label="Holdout starts")
        ax1.set_yscale("log")
        ax1.set_ylabel("Account value (USD, log scale)")
        ax1.set_title(f"Nasdaq-100 momentum vs QQQ, ${a.capital:,.0f} start")
        ax1.legend(loc="upper left")
        ax1.grid(alpha=0.3)
        for col, c in (("strategy", "#1f6feb"), ("qqq_buy_hold", "#888888")):
            ax2.plot(eq.index, (eq[col] / eq[col].cummax() - 1) * 100, color=c)
        ax2.set_ylabel("Drop from peak %")
        ax2.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(a.out, "equity_curve.png"), dpi=130)
        print(f"\nSaved results to the '{a.out}' folder.")
    except ImportError:
        print("matplotlib not installed; skipped the chart.")


if __name__ == "__main__":
    main()
