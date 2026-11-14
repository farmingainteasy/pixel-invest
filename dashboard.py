"""Builds the Pixel Invest dashboard: one self-contained HTML page with
hand-drawn SVG charts (works offline, no extra packages)."""

import html
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from urllib.parse import quote

import logo
from company_info import name, sector

GOLD_BAR = "var(--gold)"
# Colours come from the page's CSS variables, so the charts follow light and dark mode.
BLUE, GREY, GREEN, RED, AMBER = "var(--acct)", "var(--bench)", "var(--green)", "var(--red)", "var(--amber)"
SECTOR_COLORS = ["#2F8A2A", "#C8A951", "#4C7FB8", "#B0562F", "#6B7F3A", "#3E7F7A",
                 "#7A4F7F", "#B7791F", "#5C6B7A", "#8A6A48", "#9FB98A", "#D8C08A"]


def money(x, cents=True):
    return f"${x:,.2f}" if cents else f"${x:,.0f}"


def esc(s):
    return html.escape(str(s))


def pill(x, big=False):
    if x is None or pd.isna(x):
        return ""
    cls = "pos" if x >= 0 else "neg"
    arrow = "&#9650;" if x >= 0 else "&#9660;"
    return f'<span class="pill {cls}{" big" if big else ""}">{arrow} {abs(x):.1f}%</span>'


# --------------------------------------------------------------------------
# SVG helpers
# --------------------------------------------------------------------------
def line_chart(acct, w=960, h=300):
    pad_l, pad_r, pad_t, pad_b = 70, 20, 20, 36
    a, q = acct["paper_account"], acct["qqq_same_money"]
    lo, hi = min(a.min(), q.min()), max(a.max(), q.max())
    span = (hi - lo) or hi * 0.02 or 1
    lo, hi = lo - span * 0.15, hi + span * 0.15
    n = len(a)
    xs = [pad_l + (i / max(n - 1, 1)) * (w - pad_l - pad_r) for i in range(n)]

    def y(v):
        return pad_t + (1 - (v - lo) / (hi - lo)) * (h - pad_t - pad_b)

    def path(series):
        return " ".join(f"{'M' if i == 0 else 'L'}{xs[i]:.1f},{y(v):.1f}" for i, v in enumerate(series))

    grid = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        grid.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                    f'<text x="{pad_l - 8}" y="{y(v) + 4:.1f}" class="axis" text-anchor="end">{money(v, False)}</text>')
    # x labels: up to 6 dates
    step = max(1, n // 6)
    for i in list(range(0, n, step))[:6] + [n - 1]:
        grid.append(f'<text x="{xs[i]:.1f}" y="{h - 10}" class="axis" text-anchor="middle">'
                    f'{a.index[i]:%b %d}</text>')
    area = path(a) + f" L{xs[-1]:.1f},{h - pad_b} L{xs[0]:.1f},{h - pad_b} Z"
    start = a.iloc[0]
    base = f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(start):.1f}" y2="{y(start):.1f}" class="base"/>'
    dots = "".join(
        f'<circle cx="{xs[i]:.1f}" cy="{y(a.iloc[i]):.1f}" r="7" class="hot">'
        f'<title>{a.index[i]:%a %b %d}\nYour account: {money(a.iloc[i])}\nQQQ: {money(q.iloc[i])}</title></circle>'
        for i in range(n))
    end = (f'<circle cx="{xs[-1]:.1f}" cy="{y(a.iloc[-1]):.1f}" r="4.5" style="fill:{BLUE}"/>'
           f'<circle cx="{xs[-1]:.1f}" cy="{y(q.iloc[-1]):.1f}" r="3.5" style="fill:{GREY}"/>')
    return f'''<svg viewBox="0 0 {w} {h}" class="chart">
<defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1">
<stop offset="0" style="stop-color:{BLUE}" stop-opacity=".18"/><stop offset="1" style="stop-color:{BLUE}" stop-opacity="0"/></linearGradient></defs>
{''.join(grid)}{base}
<path d="{area}" fill="url(#fill)"/>
<path d="{path(q)}" fill="none" style="stroke:{GREY}" stroke-width="2" stroke-dasharray="5 4"/>
<path d="{path(a)}" fill="none" style="stroke:{BLUE}" stroke-width="3" stroke-linejoin="round"/>
{end}{dots}</svg>'''


def sparkline(series, w=150, h=40):
    s = series.dropna()
    if len(s) < 2:
        return f'<svg viewBox="0 0 {w} {h}" class="spark"><line x1="0" x2="{w}" y1="{h/2}" y2="{h/2}" style="stroke:{GREY}" stroke-dasharray="3 3"/></svg>'
    lo, hi = s.min(), s.max()
    span = (hi - lo) or 1
    pts = [(i / (len(s) - 1) * w, 3 + (1 - (v - lo) / span) * (h - 6)) for i, v in enumerate(s)]
    color = GREEN if s.iloc[-1] >= s.iloc[0] else RED
    base_y = 3 + (1 - (s.iloc[0] - lo) / span) * (h - 6)
    d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    return (f'<svg viewBox="0 0 {w} {h}" class="spark"><line x1="0" x2="{w}" y1="{base_y:.1f}" '
            f'y2="{base_y:.1f}" style="stroke:var(--line)" stroke-dasharray="3 3"/>'
            f'<path d="{d}" fill="none" style="stroke:{color}" stroke-width="2"/>'
            f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="3" style="fill:{color}"/></svg>')


def rank_meter(rank, top_n, exit_rank):
    """Bar showing where the stock ranks: green buy zone, amber hold zone, red = will be sold."""
    if rank is None:
        return ""
    scale = exit_rank + 10
    pos = min(rank, scale) / scale * 100
    zone_buy = top_n / scale * 100
    zone_hold = (exit_rank - top_n) / scale * 100
    if rank <= top_n:
        label, cls = f"Rank {rank} &middot; in the top {top_n}", "ok"
    elif rank <= exit_rank:
        label, cls = f"Rank {rank} &middot; still safe (sold below {exit_rank})", "warn"
    else:
        label, cls = f"Rank {rank} &middot; will be sold next week", "bad"
    return f'''<div class="meter"><div class="track">
<span style="width:{zone_buy:.1f}%" class="z1"></span><span style="width:{zone_hold:.1f}%" class="z2"></span><span class="z3"></span>
<i style="left:{pos:.1f}%"></i></div><div class="mlabel {cls}">{label}</div></div>'''


def watch_bars(watch, held, top_n):
    rows = []
    mx = max(watch.max(), 0.01)
    for i, (t, m) in enumerate(watch.items(), 1):
        width = max(m, 0) / mx * 100
        color = GOLD_BAR if t in held else "var(--t6)"
        tag = '<b class="held">HELD</b>' if t in held else ""
        sep = '<div class="cut">&#8593; buy zone (top %d) &middot; hold zone &#8595;</div>' % top_n if i == top_n + 1 else ""
        rows.append(f'''{sep}<div class="wrow"><span class="wn">{i}</span>
<span class="wname"><b>{esc(name(t))}</b> <small>{t}</small> {tag}</span>
<span class="wbar"><span style="width:{width:.1f}%;background:{color}"></span></span>
<span class="wpct {'pos' if m >= 0 else 'neg'}">{m * 100:+.0f}%</span></div>''')
    return "\n".join(rows)


def sector_bar(holdings, cash):
    if not holdings:
        return '<div class="muted">All cash right now.</div>'
    by = {}
    for h in holdings:
        by[sector(h["ticker"])] = by.get(sector(h["ticker"]), 0) + h["value"]
    if cash > 1:
        by["Cash"] = cash
    total = sum(by.values())
    items = sorted(by.items(), key=lambda kv: -kv[1])
    segs, legend = [], []
    for i, (sec, v) in enumerate(items):
        c = "var(--panel3)" if sec == "Cash" else SECTOR_COLORS[i % len(SECTOR_COLORS)]
        segs.append(f'<span style="width:{v / total * 100:.2f}%;background:{c}" title="{esc(sec)}: {v / total:.0%}"></span>')
        legend.append(f'<span class="lg"><i style="background:{c}"></i>{esc(sec)} <b>{v / total:.0%}</b></span>')
    return f'<div class="stack">{"".join(segs)}</div><div class="legend">{"".join(legend)}</div>'


# --------------------------------------------------------------------------
def build_dashboard(cfg, acct, trades, holdings, status, watch, last_rebal, prices, folder):
    start_val = cfg["capital"]
    now_val = acct["paper_account"].iloc[-1]
    qqq_val = acct["qqq_same_money"].iloc[-1]
    acct_ret = (now_val / start_val - 1) * 100
    qqq_ret = (qqq_val / start_val - 1) * 100
    diff = now_val - qqq_val
    days = (status["last_day"] - pd.Timestamp(cfg["start_date"])).days
    held = {h["ticker"] for h in holdings}
    top_n, exit_rank = cfg["top_n"], cfg["exit_rank"]

    vs = (f'<span class="pos">{money(abs(diff))} ahead</span> of QQQ' if diff >= 0
          else f'<span class="neg">{money(abs(diff))} behind</span> QQQ')
    status_pill = ('<span class="status on">&#9679; Invested</span>' if status["risk_on"]
                   else '<span class="status off">&#9679; In cash (safety switch)</span>')
    switch_text = (f"The Nasdaq-100 (QQQ {status['qqq']:.2f}) is above its 200-day average "
                   f"({status['sma']:.2f}), so the rules say stay invested."
                   if status["risk_on"] else
                   f"The Nasdaq-100 (QQQ {status['qqq']:.2f}) is below its 200-day average "
                   f"({status['sma']:.2f}), so the rules say sit in cash until it climbs back above.")

    # This week's action cards
    week = trades[trades["date"] == last_rebal.date()]
    cards = []
    for t in week.to_dict("records"):
        buy = t["action"] == "BUY"
        gain = "" if buy else f'<div class="ac-gain">Result: {pill(t["gain_pct"])}</div>'
        why = t["reason"].replace("Rank", "Ranked #").replace("by 3-month gain", "for 3-month gain")
        cards.append(f'''<div class="ac {'buy' if buy else 'sell'}">
<div class="ac-top"><span class="ac-act">{t["action"]}</span><span class="ac-amt">{money(t["value"], False)}</span></div>
<div class="ac-name">{esc(name(t["ticker"]))}</div><div class="ac-sub">{t["ticker"]} &middot; {esc(sector(t["ticker"]))} &middot; at {money(t["price"])}</div>
<div class="ac-why">{esc(why)}</div>{gain}</div>''')
    week_html = ("".join(cards) if cards else
                 '<div class="empty">No trades this week &mdash; every holding is still ranked high enough to keep.</div>')
    week_title = "Started the account" if last_rebal.date().isoformat() == cfg["start_date"] else "This week's trades"

    # Holding cards
    hcards = []
    for h in holdings:
        series = prices[h["ticker"]].loc[h["buy_date"]:]
        mom = f"{h['mom'] * 100:+.0f}% over 3 months" if h.get("mom") is not None and pd.notna(h["mom"]) else ""
        hcards.append(f'''<div class="hc">
<div class="hc-head"><div><div class="hc-name">{esc(name(h["ticker"]))}</div>
<div class="hc-sub">{h["ticker"]} &middot; {esc(sector(h["ticker"]))}</div></div>{pill(h["gain_pct"])}</div>
<div class="hc-mid"><div><div class="hc-val">{money(h["value"])}</div>
<div class="hc-small">{h["shares"]:.3f} sh &middot; bought {money(h["buy_price"])} &rarr; now {money(h["price"])}</div></div>
{sparkline(series)}</div>
{rank_meter(h["rank"], top_n, exit_rank)}
<div class="hc-small">{mom}</div></div>''')
    hold_html = ("".join(hcards) if hcards else
                 '<div class="empty">No holdings &mdash; the account is all cash.</div>')

    # History
    hist = []
    for t in trades.iloc[::-1].to_dict("records"):
        buy = t["action"] == "BUY"
        hist.append(f'''<tr><td>{pd.Timestamp(t["date"]):%b %d, %Y}</td>
<td><span class="tag {'buy' if buy else 'sell'}">{t["action"]}</span></td>
<td><b>{esc(name(t["ticker"]))}</b> <small class="muted">{t["ticker"]}</small></td>
<td>{money(t["price"])}</td><td>{money(t["value"], False)}</td><td>{pill(t["gain_pct"])}</td></tr>''')

    closed = trades[trades["action"] == "SELL"]
    wins = int((closed["gain_pct"] > 0).sum())
    record = (f"{wins} of {len(closed)} sells were winners" if len(closed) else "No completed trades yet")

    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#1C1A14">
<meta name="apple-mobile-web-app-title" content="Pixel Invest">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<title>Pixel Invest &mdash; {money(now_val, False)}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{quote(logo.svg())}">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="site.webmanifest">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
/* Pixel Ag's colours (farm-app src/theme.css): cream page, white cards, ink header
   with the gold rule. Green means growth, red means danger, gold means brand. */
:root{{color-scheme:light;
 --sans:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
 --page:#F4F1E9;--panel:#FFFFFF;--panel2:#FBF9F4;--panel3:#F0EDE6;--line:#E3DDD0;--line2:#F0EDE6;
 --shadow:0 2px 10px rgba(28,26,20,.05);--ink:#1C1A14;--on-ink:#F7F4EE;--on-ink-dim:rgba(247,244,238,.6);
 --t1:#1C1A14;--t3:#5C5748;--t4:#7A7466;--t5:#7F786A;--t6:#C2BCAC;
 --gold:#C8A951;--gold-text:#9A7A1E;--gold-soft:rgba(200,169,81,.12);
 --green:#2F8A2A;--green-bg:#E6F1E2;--red:#C0392B;--red-bg:#FBEAEA;--amber:#B7791F;--amber-bg:#FBF3D8;
 --acct:#9A7A1E;--bench:#A39D8E;
 --r:16px;--r2:12px;--r3:8px}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;
 --page:#0B0A07;--panel:#1B1812;--panel2:#14120D;--panel3:#262219;--line:rgba(255,255,255,.10);--line2:rgba(255,255,255,.06);
 --shadow:none;--on-ink:#F4F0E6;--t1:#F4F0E6;--t3:#C4BDAA;--t4:#A39D8E;--t5:#7E7866;--t6:#4A463C;
 --gold-text:#E2C067;--gold-soft:rgba(226,192,103,.14);--green:#6BD35A;--green-bg:rgba(107,211,90,.14);
 --red:#FF5A4E;--red-bg:rgba(255,90,78,.14);--amber:#E2B04A;--amber-bg:rgba(226,176,74,.16);
 --acct:#E2C067;--bench:#7E7866}}}}
:root[data-theme="dark"]{{color-scheme:dark;
 --page:#0B0A07;--panel:#1B1812;--panel2:#14120D;--panel3:#262219;--line:rgba(255,255,255,.10);--line2:rgba(255,255,255,.06);
 --shadow:none;--on-ink:#F4F0E6;--t1:#F4F0E6;--t3:#C4BDAA;--t4:#A39D8E;--t5:#7E7866;--t6:#4A463C;
 --gold-text:#E2C067;--gold-soft:rgba(226,192,103,.14);--green:#6BD35A;--green-bg:rgba(107,211,90,.14);
 --red:#FF5A4E;--red-bg:rgba(255,90,78,.14);--amber:#E2B04A;--amber-bg:rgba(226,176,74,.16);
 --acct:#E2C067;--bench:#7E7866}}
*{{box-sizing:border-box}}
body{{font-family:var(--sans);background:var(--page);color:var(--t1);margin:0;-webkit-font-smoothing:antialiased}}
header{{background:var(--ink);color:var(--on-ink);border-bottom:4px solid var(--gold);padding:18px 0;padding-top:max(18px,env(safe-area-inset-top))}}
.hin{{max-width:1120px;margin:0 auto;padding:0 22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
.hin svg{{width:46px;height:46px;flex:none}}
.htext{{flex:1 1 220px;min-width:0}}
.brand{{font-size:13px;font-weight:800;letter-spacing:-.01em;color:#fff;margin-bottom:3px}}
.htitle{{margin:0;font-size:21px;font-weight:700;letter-spacing:-.03em;line-height:1.2;color:var(--gold)}}
.hsub{{font-size:13px;color:var(--on-ink-dim);margin-top:3px}}
.hin .status{{margin-left:auto}}
.wrap{{max-width:1120px;margin:0 auto;padding:22px 22px 40px}}
h2{{font-size:18px;font-weight:700;letter-spacing:-.02em;margin:34px 0 12px}} h2 small{{font-weight:400;color:var(--t4);font-size:14px;letter-spacing:0}}
.muted{{color:var(--t4)}} .pos{{color:var(--green)}} .neg{{color:var(--red)}}
.box{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow)}}
.hero{{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:14px}}
.hero .box{{padding:20px 22px}}
.k{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--gold-text);font-weight:700}}
.big{{font-size:38px;font-weight:800;letter-spacing:-.03em;margin:6px 0}}
.mid{{font-size:26px;font-weight:700;letter-spacing:-.02em;margin:6px 0}}
.status{{font-weight:700;padding:7px 14px;border-radius:999px;font-size:13px;white-space:nowrap}}
.status.on{{background:rgba(107,211,90,.16);color:#8BE07C}} .status.off{{background:rgba(255,90,78,.16);color:#FF8A80}}
.pill{{display:inline-block;font-weight:700;font-size:13px;padding:3px 9px;border-radius:var(--r3);white-space:nowrap}}
.pill.pos{{background:var(--green-bg);color:var(--green)}} .pill.neg{{background:var(--red-bg);color:var(--red)}}
.pill.big{{font-size:15px}}
.switch{{padding:14px 18px;margin-top:14px;display:flex;gap:12px;align-items:center;color:var(--t3)}}
.dot{{width:12px;height:12px;border-radius:50%;flex:none}} .dot.on{{background:var(--green)}} .dot.off{{background:var(--red)}}
.switch b{{color:var(--t1)}}
.acts{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}}
.ac{{border-radius:var(--r);padding:14px 16px;background:var(--panel);border:1px solid var(--line);border-left:6px solid}}
.ac.buy{{border-left-color:var(--green)}} .ac.sell{{border-left-color:var(--red)}}
.ac-top{{display:flex;justify-content:space-between;font-weight:800;font-size:13px}}
.ac.buy .ac-act{{color:var(--green)}} .ac.sell .ac-act{{color:var(--red)}}
.ac-name{{font-size:17px;font-weight:700;margin-top:6px}} .ac-sub{{color:var(--t4);font-size:13px}}
.ac-why{{font-size:13px;margin-top:8px;color:var(--t3)}} .ac-gain{{margin-top:6px;font-size:13px}}
.empty{{padding:18px;color:var(--t4);background:var(--panel);border:1px solid var(--line);border-radius:var(--r)}}
.chartbox{{padding:16px 16px 6px}}
.chart{{width:100%;height:auto;display:block}}
.chart .grid{{stroke:var(--line2)}} .chart .axis{{fill:var(--t5);font-size:12px;font-family:var(--sans)}}
.chart .base{{stroke:var(--line);stroke-dasharray:2 4}}
.chart .hot{{fill:transparent;cursor:crosshair}} .chart .hot:hover{{fill:var(--acct);fill-opacity:.3}}
.key{{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--t3);padding:4px 6px 8px}}
.key i{{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:6px;border-radius:2px}}
.hgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}}
.hc{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px;box-shadow:var(--shadow)}}
.hc-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.hc-name{{font-weight:700;font-size:16px}} .hc-sub{{color:var(--t4);font-size:13px}}
.hc-mid{{display:flex;justify-content:space-between;align-items:center;margin:12px 0 10px}}
.hc-val{{font-size:20px;font-weight:700}} .hc-small{{color:var(--t4);font-size:12px;margin-top:2px}}
.spark{{width:130px;height:40px}}
.meter .track{{position:relative;display:flex;height:8px;border-radius:5px;overflow:visible}}
.meter .track span{{height:8px}} .z1{{background:var(--green-bg);border-radius:5px 0 0 5px}} .z2{{background:var(--amber-bg)}}
.z3{{flex:1;background:var(--red-bg);border-radius:0 5px 5px 0}}
.meter i{{position:absolute;top:-4px;width:4px;height:16px;background:var(--t1);border-radius:2px;transform:translateX(-2px)}}
.mlabel{{font-size:12px;margin-top:6px;font-weight:600}} .mlabel.ok{{color:var(--green)}} .mlabel.warn{{color:var(--amber)}} .mlabel.bad{{color:var(--red)}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.pad{{padding:16px 18px}}
.stack{{display:flex;height:22px;border-radius:var(--r3);overflow:hidden;margin:6px 0 12px}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--t3)}} .lg i{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}}
.wrow{{display:grid;grid-template-columns:24px 1.6fr 1fr 48px;gap:8px;align-items:center;padding:5px 0;font-size:13px}}
.wn{{color:var(--t5);text-align:right}} .wname small{{color:var(--t5)}}
.held{{font-size:10px;font-weight:700;background:var(--gold-soft);color:var(--gold-text);padding:1px 6px;border-radius:var(--r3);margin-left:4px}}
.wbar{{background:var(--panel3);height:10px;border-radius:5px;overflow:hidden}} .wbar span{{display:block;height:10px;border-radius:5px}}
.wpct{{text-align:right;font-weight:700}}
.cut{{font-size:11px;color:var(--gold-text);border-top:2px dashed var(--gold);margin:6px 0;padding-top:4px;text-align:center}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;color:var(--gold-text);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.08em;padding:10px 8px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel)}}
td{{padding:9px 8px;border-bottom:1px solid var(--line2);white-space:nowrap}}
.tag{{font-size:11px;font-weight:800;padding:3px 9px;border-radius:var(--r3)}}
.tag.buy{{background:var(--green-bg);color:var(--green)}} .tag.sell{{background:var(--red-bg);color:var(--red)}}
.scroll{{max-height:420px;overflow:auto}}
.note{{font-size:12px;color:var(--t5);margin-top:26px;line-height:1.6}}
@media(max-width:800px){{.hero,.two{{grid-template-columns:1fr}} .big{{font-size:34px}}}}
@media(max-width:520px){{.hin .status{{margin-left:60px}} .wrap{{padding:16px 16px 32px}} .hin{{padding:0 16px}}
 .hide-sm{{display:none}} .hgrid{{grid-template-columns:1fr}}}}
</style></head><body>

<header><div class="hin">{logo.svg()}
<div class="htext"><div class="brand">Pixel Invest</div><h1 class="htitle">Nasdaq-100 Momentum</h1>
<div class="hsub">Pretend money &middot; prices as of {status['last_day']:%a, %b %d, %Y}</div></div>
{status_pill}</div></header>

<div class="wrap">
<div class="hero">
 <div class="box"><div class="k">Your paper account</div><div class="big">{money(now_val)}</div>
  {pill(acct_ret, True)} <span class="muted">since {pd.Timestamp(cfg['start_date']):%b %d} ({days} days) &middot; started with {money(start_val, False)}</span></div>
 <div class="box"><div class="k">Same money in QQQ</div><div class="mid">{money(qqq_val)}</div>{pill(qqq_ret)}
  <div class="muted" style="margin-top:8px">You're {vs}</div></div>
 <div class="box"><div class="k">Cash &amp; positions</div><div class="mid">{len(holdings)} of {top_n} stocks</div>
  <div class="muted">{money(status['cash'])} in cash</div><div class="muted" style="margin-top:8px">{record}</div></div>
</div>

<div class="box switch"><span class="dot {'on' if status['risk_on'] else 'off'}"></span><div><b>Safety switch: {'Invested' if status['risk_on'] else 'Cash'}</b><br>{switch_text}</div></div>

<h2>{week_title} <small>&middot; at the close on {last_rebal:%A, %B %d}</small></h2>
<div class="acts">{week_html}</div>

<h2>Your account vs. just holding QQQ</h2>
<div class="box chartbox"><div class="key"><span><i style="background:{BLUE}"></i>Your paper account</span>
<span><i style="background:{GREY}"></i>Same money in QQQ</span><span class="muted hide-sm">Hover the line for daily values</span></div>
{line_chart(acct)}</div>

<h2>What you own <small>&middot; the bar shows each stock's rank: green = top {top_n}, yellow = keep, red = sell</small></h2>
<div class="hgrid">{hold_html}</div>

<div class="two">
 <div><h2>Where your money is</h2><div class="box pad">{sector_bar(holdings, status['cash'])}</div></div>
 <div><h2>Biggest 3-month gainers <small>&middot; top {exit_rank} of {status.get('universe', '')}</small></h2>
 <div class="box pad">{watch_bars(watch, held, top_n)}</div></div>
</div>

<h2>Trade history</h2>
<div class="box scroll"><table><tr><th>Date</th><th>Action</th><th>Company</th><th>Price</th><th>Amount</th><th>Result</th></tr>
{''.join(hist) or '<tr><td colspan="6" class="muted">No trades yet.</td></tr>'}</table></div>

<p class="note">Pixel Invest &middot; pretend trades only, made at each week's last closing price with no fees. Prices in US dollars.
Updates itself automatically every weekend. Last updated {datetime.now(ZoneInfo('America/New_York')):%b %d, %Y %I:%M %p} New York time. Past and paper results don't guarantee future returns.</p>
</div></body></html>"""
    path = os.path.join(folder, "dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path, now_val, qqq_val, week
