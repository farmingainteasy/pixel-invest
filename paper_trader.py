"""
Paper Trader - Nasdaq-100 weekly momentum (3-month version)
==========================================================

Run once a week, ideally on the weekend after Friday's close:
    double-click run_paper_trader.bat   (or:  python paper_trader.py)

What it does
  1. Downloads recent prices from Yahoo Finance.
  2. Replays the paper account from its start date using the rules:
       - rank Nasdaq-100 stocks by their 3-month (63 trading day) return
       - hold the top 10, equal money in each new position
       - sell a holding when it falls below rank 20
       - safety switch: all cash when QQQ closes below its 200-day average
     Trades happen at the closing price on the last trading day of each
     finished week. Nothing is connected to any real brokerage account.
  3. Writes a dashboard (paper_trading\\dashboard.html) and opens it.

Files it keeps in the "paper_trading" folder
  paper_account.json   start date, starting money, settings (edit to reset)
  trades.csv           every paper trade
  account_value.csv    daily account value vs QQQ
  dashboard.html       the page you look at

To start over: delete paper_trading\\paper_account.json.
"""

import base64
import io
import json
import os
import sys
import webbrowser
from datetime import date, datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from momentum_backtest import NASDAQ_100, BENCHMARK  # same stock list

FOLDER = os.path.join(HERE, "paper_trading")
STATE = os.path.join(FOLDER, "paper_account.json")

DEFAULTS = {"capital": 10_000.0, "top_n": 10, "exit_rank": 20,
            "lookback": 63, "sma_days": 200, "cost_pct": 0.0}


# --------------------------------------------------------------------------
def download(tickers, csv_path=None):
    if csv_path:
        return pd.read_csv(csv_path, index_col=0, parse_dates=True).sort_index()
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance is not installed. Run:  python -m pip install yfinance")
    print(f"Downloading prices for {len(tickers)} tickers...")
    raw = yf.download(tickers, period="2y", auto_adjust=False,
                      progress=False, threads=True)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    return closes.dropna(how="all", axis=1).sort_index()


def clean(prices):
    # Drop unfinished/partial days (no QQQ price or most stocks missing)
    prices = prices[prices[BENCHMARK].notna()]
    prices = prices[prices.notna().mean(axis=1) > 0.8]
    return prices.ffill(limit=5)


def week_end_days(dates, after, today):
    """Last trading day of each week that has finished, after `after`."""
    s = pd.Series(dates, index=dates)
    s = s[s > after]
    if s.empty:
        return []
    periods = s.dt.to_period("W-FRI")
    out = []
    for per, grp in s.groupby(periods):
        if per.end_time.date() < today:       # week is over
            out.append(grp.index[-1])
    return out


# --------------------------------------------------------------------------
def replay(prices, cfg, today):
    stocks = [c for c in prices.columns if c != BENCHMARK]
    bench = prices[BENCHMARK]
    sma = bench.rolling(cfg["sma_days"]).mean()
    mom = prices[stocks] / prices[stocks].shift(cfg["lookback"]) - 1

    start = pd.Timestamp(cfg["start_date"])
    days = prices.index[prices.index >= start]
    rebal = set([days[0]] + week_end_days(days, days[0], today))

    cash, shares, cost_basis = cfg["capital"], {}, {}
    trades, values = [], []

    def ranks_on(day):
        m = mom.loc[day].dropna()
        return m.rank(ascending=False), m

    for day in days:
        px = prices.loc[day]
        if day in rebal:
            risk_on = pd.notna(sma[day]) and bench[day] > sma[day]
            if risk_on:
                ranks, _ = ranks_on(day)
                exits = [t for t in shares if t not in ranks or ranks[t] > cfg["exit_rank"]]
            else:
                ranks, exits = None, list(shares)
            for t in exits:
                val = shares[t] * px[t]
                cash += val * (1 - cfg["cost_pct"])
                gain = px[t] / cost_basis[t] - 1
                why = "Safety switch: QQQ below 200-day avg" if not risk_on else \
                      f"Fell to rank {int(ranks[t]) if t in ranks else '-'} (below {cfg['exit_rank']})"
                trades.append(dict(date=day.date(), action="SELL", ticker=t,
                                   shares=round(shares[t], 4), price=round(px[t], 2),
                                   value=round(val, 2), gain_pct=round(gain * 100, 1), reason=why))
                del shares[t], cost_basis[t]
            if risk_on:
                slots = cfg["top_n"] - len(shares)
                buys = [t for t in ranks.sort_values().index if t not in shares][:max(slots, 0)]
                if buys:
                    each = cash / len(buys)
                    for t in buys:
                        q = each * (1 - cfg["cost_pct"]) / px[t]
                        shares[t], cost_basis[t] = q, px[t]
                        cash -= each
                        trades.append(dict(date=day.date(), action="BUY", ticker=t,
                                           shares=round(q, 4), price=round(px[t], 2),
                                           value=round(each, 2), gain_pct=None,
                                           reason=f"Rank {int(ranks[t])} by 3-month gain"))
        val = cash + sum(q * px[t] for t, q in shares.items())
        values.append((day, val, cash, len(shares)))

    acct = pd.DataFrame(values, columns=["date", "paper_account", "cash", "positions"]).set_index("date")
    acct["qqq_same_money"] = cfg["capital"] * bench.loc[days] / bench.loc[days[0]]
    trades = pd.DataFrame(trades, columns=["date", "action", "ticker", "shares",
                                           "price", "value", "gain_pct", "reason"])
    last = days[-1]
    ranks_now, mom_now = ranks_on(last)
    status = dict(last_day=last, risk_on=bool(pd.notna(sma[last]) and bench[last] > sma[last]),
                  qqq=bench[last], sma=sma[last], cash=cash)
    holdings = []
    for t, q in sorted(shares.items(), key=lambda kv: -kv[1] * prices.loc[last, kv[0]]):
        p = prices.loc[last, t]
        holdings.append(dict(ticker=t, shares=q, buy_price=cost_basis[t], price=p,
                             value=q * p, gain_pct=(p / cost_basis[t] - 1) * 100,
                             rank=int(ranks_now[t]) if t in ranks_now else None))
    watch = mom_now.sort_values(ascending=False).head(cfg["exit_rank"])
    last_rebal = max(d for d in rebal)
    return acct, trades, holdings, status, watch, last_rebal


# --------------------------------------------------------------------------
def chart_png(acct):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.plot(acct.index, acct["paper_account"], color="#1f6feb", lw=2, label="Paper account")
    ax.plot(acct.index, acct["qqq_same_money"], color="#8a8a8a", lw=1.6, label="Same money in QQQ")
    ax.set_ylabel("Value (USD)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def money(x):
    return f"${x:,.2f}"


def pct(x):
    if x is None or pd.isna(x):
        return ""
    cls = "up" if x >= 0 else "down"
    return f'<span class="{cls}">{x:+.1f}%</span>'


def build_dashboard(cfg, acct, trades, holdings, status, watch, last_rebal):
    start_val = cfg["capital"]
    now_val = acct["paper_account"].iloc[-1]
    qqq_val = acct["qqq_same_money"].iloc[-1]
    acct_ret = (now_val / start_val - 1) * 100
    qqq_ret = (qqq_val / start_val - 1) * 100
    lead = acct_ret - qqq_ret

    this_week = trades[trades["date"] == last_rebal.date()]
    held = {h["ticker"] for h in holdings}

    def rows(items, cols):
        return "\n".join("<tr>" + "".join(f"<td>{c(i)}</td>" for c in cols) + "</tr>" for i in items)

    hold_rows = rows(holdings, [
        lambda h: f"<b>{h['ticker']}</b>",
        lambda h: f"{h['shares']:.3f}",
        lambda h: money(h["buy_price"]),
        lambda h: money(h["price"]),
        lambda h: money(h["value"]),
        lambda h: pct(h["gain_pct"]),
        lambda h: h["rank"] or "-"]) or '<tr><td colspan="7" class="muted">No holdings - the account is in cash.</td></tr>'

    week_rows = rows(this_week.to_dict("records"), [
        lambda t: f'<span class="tag {t["action"].lower()}">{t["action"]}</span>',
        lambda t: f"<b>{t['ticker']}</b>",
        lambda t: money(t["price"]),
        lambda t: money(t["value"]),
        lambda t: pct(t["gain_pct"]),
        lambda t: t["reason"]]) or '<tr><td colspan="6" class="muted">No trades this week - holdings stay the same.</td></tr>'

    hist = trades.iloc[::-1]
    hist_rows = rows(hist.to_dict("records"), [
        lambda t: t["date"],
        lambda t: f'<span class="tag {t["action"].lower()}">{t["action"]}</span>',
        lambda t: f"<b>{t['ticker']}</b>",
        lambda t: money(t["price"]),
        lambda t: money(t["value"]),
        lambda t: pct(t["gain_pct"])]) or '<tr><td colspan="6" class="muted">No trades yet.</td></tr>'

    watch_rows = rows(list(enumerate(watch.items(), 1)), [
        lambda w: w[0],
        lambda w: f"<b>{w[1][0]}</b>" + (" <span class='muted'>(held)</span>" if w[1][0] in held else ""),
        lambda w: pct(w[1][1] * 100)])

    switch = ('<span class="up">INVESTED</span> - QQQ is above its 200-day average'
              if status["risk_on"] else
              '<span class="down">CASH</span> - QQQ is below its 200-day average')

    img = chart_png(acct)
    days = (status["last_day"] - pd.Timestamp(cfg["start_date"])).days
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Paper Trader</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;background:#f6f7f9;color:#1d2330;margin:0}}
 .wrap{{max-width:1050px;margin:0 auto;padding:24px}}
 h1{{margin:0 0 4px;font-size:24px}} h2{{font-size:17px;margin:28px 0 8px}}
 .muted{{color:#6b7280}} .up{{color:#0f8a4b;font-weight:600}} .down{{color:#c62828;font-weight:600}}
 .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:16px}}
 .card{{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
 .card .k{{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em}}
 .card .v{{font-size:22px;font-weight:700;margin-top:4px}}
 .panel{{background:#fff;border-radius:10px;padding:8px 16px 14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 th{{text-align:left;color:#6b7280;font-weight:600;border-bottom:1px solid #e5e7eb;padding:8px 6px}}
 td{{padding:7px 6px;border-bottom:1px solid #f0f1f3}}
 .tag{{font-size:12px;font-weight:700;padding:2px 8px;border-radius:10px}}
 .tag.buy{{background:#e3f4ea;color:#0f8a4b}} .tag.sell{{background:#fde8e8;color:#c62828}}
 .two{{display:grid;grid-template-columns:2fr 1fr;gap:16px}}
 .scroll{{max-height:360px;overflow:auto}}
 .note{{font-size:13px;color:#6b7280;margin-top:22px}}
</style></head><body><div class="wrap">
<h1>Paper Trader - Nasdaq-100 momentum</h1>
<div class="muted">Prices as of close {status['last_day'].date()} &middot; started {cfg['start_date']} with {money(start_val)} &middot; pretend money only</div>

<div class="cards">
 <div class="card"><div class="k">Paper account</div><div class="v">{money(now_val)}</div>{pct(acct_ret)}</div>
 <div class="card"><div class="k">Same money in QQQ</div><div class="v">{money(qqq_val)}</div>{pct(qqq_ret)}</div>
 <div class="card"><div class="k">Ahead / behind QQQ</div><div class="v">{pct(lead)}</div><span class="muted">{days} days in</span></div>
 <div class="card"><div class="k">Cash</div><div class="v">{money(status['cash'])}</div><span class="muted">{len(holdings)} of {cfg['top_n']} slots filled</span></div>
</div>

<h2>Safety switch</h2>
<div class="panel" style="padding:12px 16px">{switch} (QQQ {status['qqq']:.2f} vs average {status['sma']:.2f})</div>

<h2>This week's trades <span class="muted">({last_rebal.date()} close)</span></h2>
<div class="panel"><table><tr><th>Action</th><th>Stock</th><th>Price</th><th>Amount</th><th>Gain</th><th>Why</th></tr>
{week_rows}</table></div>

<h2>Account value vs QQQ</h2>
<div class="panel" style="padding:12px"><img src="data:image/png;base64,{img}" style="width:100%"></div>

<div class="two">
<div><h2>Holdings</h2><div class="panel"><table>
<tr><th>Stock</th><th>Shares</th><th>Bought at</th><th>Now</th><th>Value</th><th>Gain</th><th>Rank now</th></tr>
{hold_rows}</table></div></div>
<div><h2>Top {cfg['exit_rank']} by 3-month gain</h2><div class="panel scroll"><table>
<tr><th>#</th><th>Stock</th><th>3-mo</th></tr>{watch_rows}</table></div></div>
</div>

<h2>Trade history</h2>
<div class="panel scroll"><table><tr><th>Date</th><th>Action</th><th>Stock</th><th>Price</th><th>Amount</th><th>Gain</th></tr>
{hist_rows}</table></div>

<p class="note">Trades are pretend, made at each week's final closing price, with no fees (as in a USD account).
Run this once a week after Friday's close. Prices in US dollars. Generated {datetime.now():%Y-%m-%d %H:%M}.
Past and paper results don't guarantee future returns.</p>
</div></body></html>"""
    path = os.path.join(FOLDER, "dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path, now_val, qqq_val, this_week


# --------------------------------------------------------------------------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Weekly paper trader")
    ap.add_argument("--prices-csv", help="use saved prices instead of downloading (testing)")
    ap.add_argument("--today", help="pretend today's date is YYYY-MM-DD (testing)")
    ap.add_argument("--no-open", action="store_true", help="don't open the dashboard")
    a = ap.parse_args()

    os.makedirs(FOLDER, exist_ok=True)
    today = date.fromisoformat(a.today) if a.today else date.today()
    tickers = sorted(set(NASDAQ_100) - {BENCHMARK}) + [BENCHMARK]
    prices = clean(download(tickers, a.prices_csv))
    if a.today:
        prices = prices[prices.index.date <= today]

    if os.path.exists(STATE):
        cfg = {**DEFAULTS, **json.load(open(STATE))}
    else:
        cfg = {**DEFAULTS, "start_date": str(prices.index[-1].date())}
        json.dump(cfg, open(STATE, "w"), indent=2)
        print(f"New paper account: {money(cfg['capital'])} starting {cfg['start_date']}")

    acct, trades, holdings, status, watch, last_rebal = replay(prices, cfg, today)
    trades.to_csv(os.path.join(FOLDER, "trades.csv"), index=False)
    acct.round(2).to_csv(os.path.join(FOLDER, "account_value.csv"))
    path, now_val, qqq_val, this_week = build_dashboard(cfg, acct, trades, holdings,
                                                        status, watch, last_rebal)

    print(f"\nPrices as of {status['last_day'].date()}")
    print(f"Paper account: {money(now_val)}   Same money in QQQ: {money(qqq_val)}")
    print(f"Safety switch: {'INVESTED' if status['risk_on'] else 'CASH'}")
    print(f"Trades on {last_rebal.date()}:")
    if this_week.empty:
        print("  none")
    for t in this_week.itertuples():
        print(f"  {t.action:<4} {t.ticker:<6} at ${t.price:,.2f}")
    print(f"\nDashboard: {path}")
    if not a.no_open:
        webbrowser.open("file:///" + path.replace("\\", "/"))


if __name__ == "__main__":
    main()
