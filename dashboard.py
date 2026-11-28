"""Builds the Pixel Invest pages: Home (all the accounts together, then one card per market) and
a page per market. Every page leads with how far the account is ahead of its index fund. Self-contained, phone-first HTML with hand-drawn SVG charts. Dark in every theme, in
Pixel Ag's colours (farm-app src/theme.css, dark set)."""

import html
import json
import math
import os
import re
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd

import logo
from company_info import name, sector

# Pixel Ag dark palette
# Pixel Ag's dark palette: near-black page, cream writing, the header an ink band trimmed in gold.
# Gold is your account (lines, dots, links) and the index fund dashed grey. Green is ahead/up,
# red behind/down, amber a heads-up. Gold is the brand, never a "chosen" state.
PAGE, PANEL, PANEL2, PANEL3 = "#0B0A07", "#1B1812", "#14120D", "#262219"
INK, GOLD = "#1C1A14", "#C8A951"            # the header band and its rule
LINE, LINE2 = "rgba(255,255,255,.10)", "rgba(255,255,255,.06)"
T1, T3, T4, T5 = "#F4F0E6", "#C4BDAA", "#A39D8E", "#7E7866"
YOU, FUND = "#E2C067", "#A39D8E"
GREEN, RED, AMBER = "#6BD35A", "#FF6B5F", "#E2B04A"
SECTOR_COLORS = ["#6BD35A", "#E2C067", "#6FA0D8", "#E0875A", "#A8B86A", "#5FB3AC",
                 "#B98BC0", "#E2B04A", "#8E9BAA", "#B89470", "#9FD08A", "#D8C08A"]


SYM, LABEL = "$", "QQQ"      # set per market by set_market()


def money(x, cents=True):
    return f"{SYM}{x:,.2f}" if cents else f"{SYM}{x:,.0f}"


def esc(s):
    return html.escape(str(s))


def signed_money(x):
    if abs(x) < 0.005:
        return money(0)
    return f'{"+" if x > 0 else "&minus;"}{money(abs(x))}'


def signed_pct(x, d=2):
    if abs(x) < 0.5 * 10 ** -d:
        return f"{0:.{d}f}%"
    return f'{"+" if x > 0 else "&minus;"}{abs(x):.{d}f}%'


def tone(x):
    return "pos" if x >= 0.005 else "neg" if x <= -0.005 else ""


def clock(t):
    return f"{t.hour % 12 or 12}:{t:%M}{'am' if t.hour < 12 else 'pm'}"


def short_date(d):
    return f"{d:%b} {d.day}"


def week_closed(day):
    """Friday's close has happened, and the weekend update hasn't traded on it yet."""
    if not day or pd.Timestamp(day["day"]).weekday() != 4:
        return False
    return not day.get("live") or pd.Timestamp(day["as_of"]).hour >= 16


def day_name(day, short=False):
    """'Today' while the market is open or on the day itself; otherwise that day's date. Once
    Friday has closed, the date: the page stays up until the weekend update replaces it."""
    d = pd.Timestamp(day["day"])
    today = d.date() == datetime.now(ZoneInfo("America/New_York")).date()
    if not week_closed(day) and (day.get("live") or today):
        return "Today"
    return f"{d:%a}" if short else f"{d:%a} {short_date(d)}"


def standing(diff, label, fmt):
    """The headline on every page: how far the account is ahead of (or behind) its index fund."""
    if abs(diff) < 0.5:
        return f'<p class="verdict">Level with {label}</p>'
    return (f'<p class="verdict {"pos" if diff > 0 else "neg"}">{fmt(abs(diff))} '
            f'{"ahead of" if diff > 0 else "behind"} {label}</p>')


def spans(day, capital, val, bench, start, signed):
    """Rows for vs_table: the latest day, the week so far and since the start, each as
    (label, change, change as text, change %, the index fund's %)."""
    rows = []
    if day:
        rows.append((day_name(day), day["change"], signed(day["change"]), day["pct"], day["bench_pct"]))
        if "week_change" in day:
            rows.append(("This week", day["week_change"], signed(day["week_change"]), day["week_pct"], day["week_bench_pct"]))
    ch = val - capital
    if day and "week_change" in day and abs(day["week_change"] - ch) < 0.005:
        return rows                    # the account's first week: since the start is this week
    rows.append((f"Since {short_date(start)}" if start is not None else "Since start", ch, signed(ch),
                 ch / capital * 100, (bench / capital - 1) * 100))
    return rows


def vs_table(rows, fund):
    """You against the index fund, one row per time span. The column heads double as the
    charts' key: a gold line is you, a grey dashed line is the fund."""
    body = "".join(f'<tr><th scope="row">{lab}</th><td><b class="{tone(ch)}">{txt}</b><small>{signed_pct(p)}</small></td>'
                   f'<td>{signed_pct(fp)}</td></tr>' for lab, ch, txt, p, fp in rows)
    return (f'<table class="vs"><thead><tr><th></th><th scope="col"><span class="you">You</span></th>'
            f'<th scope="col"><span class="fund">{fund}</span></th></tr></thead><tbody>{body}</tbody></table>')


def as_of(day, every=False):
    """When the numbers are from: live, or a day's close."""
    if not day:
        return ""
    if day.get("live") and pd.Timestamp(day["as_of"]).hour < 16:
        more = " Updates every 30 minutes." if every else ""
        return (f'<p class="asof"><i class="dot"></i>Live at {clock(pd.Timestamp(day["as_of"]))} '
                f'New York time.{more}</p>')
    d = pd.Timestamp(day["day"])
    return f'<p class="asof">At the close on {d:%a} {short_date(d)}.</p>'


def switch_heads_up(s, gap=None, closed=False):
    """A warning when the safety switch points away from what's held. It's acted on only at
    the week's last close, so mid-week it's a heads-up for Friday; once Friday has `closed`,
    it says what the weekend update will do."""
    held = bool(s.get("held"))
    if s["risk_on"] == held:
        return ""
    by = f" {abs(gap):.1f}%" if gap is not None else ""
    if closed and held:
        return (f'<div class="heads"><b>Selling everything at Friday\'s close</b><span>{LABEL} ended the week{by} '
                f"under its 200-day average, so the weekend update sells everything at Friday's closing prices "
                f"and the account waits in cash.</span></div>")
    if closed:
        return (f'<div class="heads"><b>Buying back in at Friday\'s close</b><span>{LABEL} ended the week{by} '
                f"above its 200-day average, so the weekend update buys back in at Friday's closing prices.</span></div>")
    if held:
        return (f'<div class="heads"><b>Friday: may sell everything</b><span>{LABEL} is{by} under its 200-day average. '
                f"If it's still under at Friday's close, everything is sold and the account waits in cash.</span></div>")
    return (f'<div class="heads"><b>Friday: may buy back in</b><span>{LABEL} is{by} back above its 200-day average. '
            f"If it's still above at Friday's close, the rules buy back in.</span></div>")


def week_chart(curve):
    """This week every 30 minutes, as % since the previous week's close; drag to read."""
    if not curve or len(curve) < 2:
        return ""
    data = json.dumps({"pts": curve, "sym": SYM, "bench": LABEL})
    return (f'<div class="wkc">{key()}<div class="wk-plot"></div><div class="wk-read" aria-live="polite"></div>'
            f'<script type="application/json" class="wk-data">{data}</script>{WK_SCRIPT}</div>')


def key(you="Your account", fund=None, solid=False):
    return (f'<div class="key"><span class="you">{you}</span>'
            f'<span class="fund{" solid" if solid else ""}">{fund or "Same money in " + LABEL}</span></div>')


# This week's chart: Monday to Friday across the page, so the line fills in as the week goes.
WK_SCRIPT = """<script>
(function(){
var box=document.currentScript.closest('.wkc'); if(!box) return;
var D=JSON.parse(box.querySelector('.wk-data').textContent), P=D.pts, NS='http://www.w3.org/2000/svg';
var GOLD='#E2C067', GREY='#A39D8E', GRID='rgba(255,255,255,.07)', ZERO='rgba(244,240,230,.28)', T4='#A39D8E', T5='#7E7866';
var a0=P[0][2], b0=P[0][3], DAY=390, X=5*DAY, n=P.length;
function el(t,a,p){var e=document.createElementNS(NS,t);for(var k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e;}
function pa(r){return (r[2]/a0-1)*100;} function pb(r){return (r[3]/b0-1)*100;}
function sign(v,z){return v>z?'+':v<-z?'−':'';}
function money(v){return sign(v,.005)+D.sym+Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});}
function pct(v){return sign(v,.005)+Math.abs(v).toFixed(2)+'%';}
function read(r){ var c=r[2]-a0, cls=c>=.005?'pos':c<=-.005?'neg':'';
  box.querySelector('.wk-read').innerHTML='<span class="rv '+cls+'">'+money(c)+'</span><span class="rs">'+pct(pa(r))+
  ' &middot; '+D.bench+' '+pct(pb(r))+' &middot; '+r[1]+'</span>'; }
function hint(){ box.querySelector('.wk-read').innerHTML='<span class="rs">Drag across the chart to see any half hour.</span>'; }
function draw(){
  var host=box.querySelector('.wk-plot'), w=host.clientWidth; if(!w) return;
  var h=Math.round(Math.min(220,Math.max(170,w*.45))), pl=4, pr=48, pt=10, pb_=22;
  var vals=[0]; P.forEach(function(r){vals.push(pa(r),pb(r));});
  var lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals), mid=(lo+hi)/2, half=Math.max((hi-lo)/2*1.25,.5);
  lo=mid-half; hi=mid+half;
  var st=[.25,.5,1,2,5,10,20,50].filter(function(s){return (hi-lo)/s<=5;})[0]||100;
  function x(m){return pl+m/X*(w-pl-pr);} function y(v){return pt+(1-(v-lo)/(hi-lo))*(h-pt-pb_);}
  host.innerHTML=''; var svg=el('svg',{viewBox:'0 0 '+w+' '+h,width:w,height:h,role:'img',
    'aria-label':'This week so far: your account '+pct(pa(P[n-1]))+', '+D.bench+' '+pct(pb(P[n-1]))},host);
  for(var v=Math.ceil(lo/st)*st; v<=hi+1e-9; v+=st){ var z=Math.abs(v)<1e-9;
    el('line',{x1:pl,x2:w-pr,y1:y(v),y2:y(v),stroke:z?ZERO:GRID,'stroke-dasharray':z?'3 3':'none'},svg);
    el('text',{x:w-pr+6,y:y(v)+4,'font-size':11,fill:T4},svg).textContent=(z?'0':sign(v,0)+(+Math.abs(v).toFixed(2)))+'%'; }
  var cur=Math.floor((P[n-1][0]-1)/DAY);
  ['Mon','Tue','Wed','Thu','Fri'].forEach(function(name,k){
    if(k) el('line',{x1:x(k*DAY),x2:x(k*DAY),y1:pt,y2:h-pb_,stroke:GRID},svg);
    el('text',{x:x(k*DAY+DAY/2),y:h-6,'font-size':11,'text-anchor':'middle',fill:k>cur?T5:T4,'font-weight':k===cur?700:400},svg).textContent=name; });
  function path(f){return P.map(function(r,i){return (i?'L':'M')+x(r[0]).toFixed(1)+','+y(f(r)).toFixed(1);}).join('');}
  el('path',{d:path(pb),fill:'none',stroke:GREY,'stroke-width':1.5,'stroke-dasharray':'4 3','stroke-linejoin':'round'},svg);
  el('path',{d:path(pa),fill:'none',stroke:GOLD,'stroke-width':2,'stroke-linejoin':'round'},svg);
  el('circle',{cx:x(P[n-1][0]),cy:y(pa(P[n-1])),r:4.5,fill:GOLD},svg);
  var vl=el('line',{y1:pt,y2:h-pb_,stroke:'rgba(244,240,230,.35)',visibility:'hidden'},svg),
      d1=el('circle',{r:4.5,fill:GOLD,stroke:'#14120D','stroke-width':2,visibility:'hidden'},svg),
      d2=el('circle',{r:3.5,fill:GREY,stroke:'#14120D','stroke-width':2,visibility:'hidden'},svg);
  function at(e){var b=svg.getBoundingClientRect(),px=(e.clientX-b.left)/b.width*w,best=0;
    P.forEach(function(r,i){if(Math.abs(x(r[0])-px)<Math.abs(x(P[best][0])-px))best=i;}); return best;}
  function show(i){var r=P[i];[vl,d1,d2].forEach(function(e){e.setAttribute('visibility','visible');});
    vl.setAttribute('x1',x(r[0]));vl.setAttribute('x2',x(r[0]));d1.setAttribute('cx',x(r[0]));d1.setAttribute('cy',y(pa(r)));
    d2.setAttribute('cx',x(r[0]));d2.setAttribute('cy',y(pb(r)));read(r);}
  host.onpointermove=function(e){show(at(e));}; host.onpointerdown=function(e){show(at(e));};
  host.onpointerleave=function(){[vl,d1,d2].forEach(function(e){e.setAttribute('visibility','hidden');});hint();};
  hint();
}
var t; window.addEventListener('resize',function(){clearTimeout(t);t=setTimeout(draw,150);});
draw();
})();
</script>"""


def year_range(px, ticker):
    """(52-week low, high, latest) for a stock from daily closes, which Yahoo adjusts for
    splits. None without most of a year of prices."""
    if px is None or ticker not in px.columns:
        return None
    s = px[ticker].dropna()
    if s.empty:
        return None
    s = s[s.index > s.index[-1] - pd.Timedelta(days=365)]
    if len(s) < 200:
        return None
    return float(s.min()), float(s.max()), float(s.iloc[-1])


def range_bar(r):
    """Where a stock sits in its 52-week range: low on the left, high on the right, a dot for now."""
    if not r:
        return ""
    lo, hi, now = r
    pos = 50.0 if hi <= lo else min(100.0, max(0.0, (now - lo) / (hi - lo) * 100))
    off = (1 - now / hi) * 100 if hi > 0 else 0.0
    note = "At its high" if off < 0.5 else f"{off:.0f}% below high"
    label = (f"52-week range {money(lo, False)} to {money(hi, False)}; "
             f"now {money(now, False)}, {note[0].lower() + note[1:]}")
    return (f'<div class="yr" role="img" aria-label="{label}"><span aria-hidden="true">{money(lo, False)}</span>'
            f'<span class="track" aria-hidden="true"><i style="left:{pos:.1f}%"></i></span>'
            f'<span aria-hidden="true">{money(hi, False)}</span><span class="off" aria-hidden="true">{note}</span></div>')


def bar_note(bars):
    """Says once, under a list, what the bars are."""
    return ('<p class="note">The bar under each stock runs from its 52-week low to its 52-week high, '
            'from closing prices. The dot is the latest price.</p>' if any(bars.values()) else "")


def chip(x):
    if x is None or pd.isna(x):
        return ""
    cls = "pos" if x > 0.05 else "neg" if x < -0.05 else "flat"
    sign = "+" if x > 0.05 else "&minus;" if x < -0.05 else ""
    return f'<span class="chip {cls}">{sign}{abs(x):.1f}%</span>'


# --------------------------------------------------------------------------
# Small graphics
# --------------------------------------------------------------------------
def nice_ticks(lo, hi, most=5):
    """Round numbers for a chart's side (like 9,800 / 10,000 / 10,200): the smallest step of
    1, 2, 2.5 or 5 times a power of ten that puts no more than `most` marks between lo and hi."""
    if hi <= lo:
        return [lo]
    for e in range(-3, 10):
        for m in (1, 2, 2.5, 5):
            step = m * 10 ** e
            first, last = math.ceil(lo / step), math.floor(hi / step)
            if last - first + 1 <= most:
                return [k * step for k in range(first, last + 1)]
    return [lo, hi]


def even_spots(n, most=5):
    """Up to `most` evenly spread positions out of n, always the first and the last."""
    m = min(n, most)
    return sorted({round(k * (n - 1) / (m - 1)) for k in range(m)}) if m > 1 else [0]


def _scale(lo, hi, top, bottom):
    return lambda v: bottom - (v - lo) / (hi - lo) * (bottom - top)


def account_chart(acct, w=380, h=200):
    a, q = acct["paper_account"], acct["qqq_same_money"]
    n = len(a)
    pl, pr, pt_, pb = 52, 8, 10, 24
    lo, hi = min(a.min(), q.min()), max(a.max(), q.max())
    span = (hi - lo) or hi * 0.02
    lo, hi = lo - span * .2, hi + span * .2
    y = _scale(lo, hi, pt_, h - pb)
    xs = [pl + (i / max(n - 1, 1)) * (w - pl - pr) for i in range(n)]
    pts = lambda s: " ".join(f"{xs[i]:.1f},{y(v):.1f}" for i, v in enumerate(s))
    grid = "".join(f'<line x1="{pl}" x2="{w - pr}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{LINE2}"/>'
                   f'<text x="{pl - 8}" y="{y(v) + 4:.1f}" text-anchor="end" class="ax">{money(v, False)}</text>'
                   for v in nice_ticks(lo, hi, 5))
    xl = "".join(f'<text x="{xs[i]:.1f}" y="{h - 8}" text-anchor="{"start" if i == 0 else "end" if i == n - 1 else "middle"}" '
                 f'class="ax">{a.index[i]:%b} {a.index[i].day}</text>' for i in even_spots(n, 5))
    dots = "".join(f'<circle cx="{xs[i]:.1f}" cy="{y(a.iloc[i]):.1f}" r="8" class="hot"><title>{a.index[i]:%a %b %d}'
                   f'\nYour account: {money(a.iloc[i])}\n{LABEL}: {money(q.iloc[i])}</title></circle>' for i in range(n))
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Your account against the same money in {LABEL}, '
            f'{money(a.iloc[-1])} vs {money(q.iloc[-1])}">{grid}{xl}'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(q)}" fill="none" stroke="{T4}" stroke-width="1.5" stroke-dasharray="4 3"/>'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(a)}" fill="none" stroke="{YOU}" stroke-width="2" stroke-linejoin="round"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{y(a.iloc[-1]):.1f}" r="4.5" fill="{YOU}"/>{dots}</svg>')


def switch_chart(sw, w=380, h=200):
    """QQQ vs its 200-day average, shaded where it closed below the line."""
    n = len(sw)
    pl, pr, pt_, pb = 40, 8, 10, 24
    lo, hi = sw.min().min(), sw.max().max()
    span = hi - lo
    lo, hi = lo - span * .08, hi + span * .08
    y = _scale(lo, hi, pt_, h - pb)
    xs = [pl + (i / max(n - 1, 1)) * (w - pl - pr) for i in range(n)]
    pts = lambda s: " ".join(f"{xs[i]:.1f},{y(v):.1f}" for i, v in enumerate(s))
    shade, run = [], None
    below = (sw["qqq"] < sw["sma"]).tolist()
    for i, b in enumerate(below + [False]):
        if b and run is None:
            run = i
        if not b and run is not None:
            x0 = xs[run] - (xs[1] - xs[0]) / 2 if n > 1 else xs[run]
            x1 = xs[i - 1] + (xs[1] - xs[0]) / 2 if n > 1 else xs[run] + 4
            shade.append(f'<rect x="{max(pl, x0):.1f}" y="{pt_}" width="{max(3, min(w - pr, x1) - max(pl, x0)):.1f}" '
                         f'height="{h - pb - pt_}" fill="rgba(255,90,78,.16)"/>')
            run = None
    grid = "".join(f'<line x1="{pl}" x2="{w - pr}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{LINE2}"/>'
                   f'<text x="{pl - 8}" y="{y(v) + 4:.1f}" text-anchor="end" class="ax">{v:,.0f}</text>'
                   for v in nice_ticks(lo, hi, 5))
    months, prev = [], None
    for i, d in enumerate(sw.index):
        if d.month != prev and i < n - 12:
            months.append(f'<text x="{xs[i]:.1f}" y="{h - 8}" class="ax">{d:%b}</text>')
        prev = d.month
    last_q, last_s = sw["qqq"].iloc[-1], sw["sma"].iloc[-1]
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{LABEL} {last_q:.2f} against its '
            f'200-day average {last_s:.2f}; shaded where it closed below the line">{grid}{"".join(shade)}{"".join(months)}'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(sw["sma"])}" fill="none" stroke="{AMBER}" stroke-width="1.5" stroke-dasharray="5 3"/>'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(sw["qqq"])}" fill="none" stroke="{T1}" stroke-width="1.5" stroke-linejoin="round"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{y(last_q):.1f}" r="4" fill="{T1}"/></svg>')


def backtest_chart(bt, w=380, h=210):
    c = bt["curve"]
    c = c[::2] + ([c[-1]] if (len(c) - 1) % 2 else [])     # every 2 weeks: detail without jitter
    n = len(c)
    pl, pr, pt_, pb = 42, 8, 10, 24
    vals = [v for r in c for v in r[1:]]
    l0, l1 = math.log(min(vals)), math.log(max(vals))
    y = lambda v: (h - pb) - (math.log(v) - l0) / (l1 - l0) * (h - pb - pt_)
    xs = [pl + (i / max(n - 1, 1)) * (w - pl - pr) for i in range(n)]
    pts = lambda k: " ".join(f"{xs[i]:.1f},{y(r[k]):.1f}" for i, r in enumerate(c))
    ticks = [v for v in (10_000, 30_000, 100_000, 300_000, 1_000_000) if min(vals) <= v <= max(vals)]
    grid = "".join(f'<line x1="{pl}" x2="{w - pr}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{LINE2}"/>'
                   f'<text x="{pl - 8}" y="{y(v) + 4:.1f}" text-anchor="end" class="ax">${v // 1000:,}k</text>' for v in ticks)
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Backtest: $10,000 grew to '
            f'{money(c[-1][1], False)} with the rules and {money(c[-1][2], False)} in {LABEL}">{grid}'
            f'<text x="{pl}" y="{h - 8}" class="ax">{c[0][0][:4]}</text>'
            f'<text x="{w - pr}" y="{h - 8}" text-anchor="end" class="ax">{c[-1][0][:4]}</text>'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(2)}" fill="none" stroke="{T4}" stroke-width="1.25" stroke-linejoin="round"/>'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(1)}" fill="none" stroke="{YOU}" stroke-width="1.5" stroke-linejoin="round"/></svg>')


def sector_bar(holdings, cash):
    by = {}
    for h in holdings:
        by[sector(h["ticker"])] = by.get(sector(h["ticker"]), 0) + h["value"]
    if cash > 1:
        by["Cash"] = cash
    total = sum(by.values())
    items = sorted(by.items(), key=lambda kv: -kv[1])
    segs, legend = [], []
    for i, (sec, v) in enumerate(items):
        c = PANEL3 if sec == "Cash" else SECTOR_COLORS[i % len(SECTOR_COLORS)]
        segs.append(f'<span style="width:{v / total * 100:.2f}%;background:{c}"></span>')
        legend.append(f'<span><i style="background:{c}"></i>{esc(sec)} <b>{v / total:.0%}</b></span>')
    return f'<div class="stack">{"".join(segs)}</div><div class="legend">{"".join(legend)}</div>'


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Page shell: shared head, styles and navigation for every page
# --------------------------------------------------------------------------
def standing_of(m):
    """A market's place in the race against its index fund, for the menus and the board:
    txt '+$662', cls 'pos'/'neg'/'flat' (or its status class before it trades), words for
    screen readers, frac = the gap as a share of the money put in (sets the bar's length),
    and state ('In cash', 'Invested', 'Starts Nov 30')."""
    s = _SUMMARIES.get(m["slug"])
    label, cls = summary_state(m)
    state = "In cash" if label == "Cash" else label
    if not m.get("active", True) or not s or "value" not in s:
        return dict(txt="Soon", cls="wait", words=state, frac=None, state=state)
    if "lead" in s:                       # set by the market's own page as it's built
        diff = s["lead"]
    else:
        day = s.get("day")
        diff = (day["value"] - day["bench_value"]) if day else (s["value"] - s["bench_value"])
    fund, sym = m.get("bench_label", ""), m.get("symbol", "$")
    frac = diff / s["capital"] if s.get("capital") else None
    amt = f"{sym}{abs(diff):,.0f}"
    if abs(diff) < 0.5:
        return dict(txt="Level", cls="flat", words=f"level with {fund}", frac=0.0, state=state)
    if diff > 0:
        return dict(txt=f"+{amt}", cls="pos", words=f"{amt} ahead of {fund}", frac=frac, state=state)
    return dict(txt=f"&minus;{amt}", cls="neg", words=f"{amt} behind {fund}", frac=frac, state=state)


def lanes(markets, rel, current, sub=""):
    """One lane per market: its name, how far it's ahead of (+) or behind (-) its index fund,
    and a bar running right of the centre line when ahead, left when behind. The longest bar
    is the market furthest from its fund, measured as a share of the money put in, so a C$
    and a US$ account compare fairly. `sub` (section links) goes under the current market."""
    st = [(m, standing_of(m)) for m in markets]
    widest = max([abs(x["frac"]) for _, x in st if x["frac"]] + [1e-9])
    out = []
    for m, x in st:
        here = m["slug"] == current
        w = 0 if not x["frac"] else max(abs(x["frac"]) / widest * 100, 3)
        bar = f'<i class="{x["cls"]}" style="--w:{w:.1f}%"></i>' if x["frac"] is not None else ""
        out.append(f'<a class="lane{" on" if here else ""}" href="{rel}{m["slug"]}/"'
                   f'{" aria-current=page" if here else ""} aria-label="{esc(m["name"])}, {x["words"]}">'
                   f'<span class="ln"><span class="full">{esc(m["name"])}</span>'
                   f'<span class="short">{esc(m.get("short", m["name"]))}</span></span>'
                   f'<span class="st">{esc(x["state"])}</span>'
                   f'<span class="amt fig {x["cls"]}">{x["txt"]}</span>'
                   f'<span class="gauge" aria-hidden="true">{bar}</span></a>'
                   f'{sub if here else ""}')
    return "".join(out)


def board(markets, rel=""):
    """Home's race board: every market against its own index fund, tap a lane to open it."""
    return (f'<nav class="board" aria-label="Each market against its index fund">'
            f'<div class="bkey" aria-hidden="true"><span>Behind</span><span>Ahead</span></div>'
            f'{lanes(markets, rel, None)}</nav>')


def menus(rel, markets, current, sections=()):
    """The side menu (computer) and the pinned strip (phone, market pages only), each between
    markers so refresh_menus can swap in newer scores once every market has updated."""
    links = "".join(f'<a href="#{i}" data-sec="{i}">{esc(lab)}</a>' for i, lab in sections)
    sub = f'<div class="sub" aria-label="On this page">{links}</div>' if links else ""
    here = ' aria-current="page"' if current is None else ""
    side = (f'<a class="home{" on" if current is None else ""}" href="{rel or "./"}"{here}>All markets</a>'
            f'{lanes(markets, rel, current, sub)}')
    strip = ""
    if current is not None:
        n = len(markets) + 1
        strip = (f'<nav class="strip" aria-label="Markets"><div class="wrap"><div class="lanes" style="--n:{n}">'
                 f'<a class="lane all" href="{rel}"><span class="ln">All</span>'
                 f'<span class="amt">{len(markets)} markets</span></a>{lanes(markets, rel, current)}</div>'
                 f'{f"<div class=secs>{links}</div>" if links else ""}</div></nav>')
    return f"<!--side-->{side}<!--/side-->", f"<!--bar-->{strip}<!--/bar-->"


def refresh_menus(page, rel, markets, current):
    """A built page with its menus rebuilt from the latest scores (its section links kept)."""
    seen = {}
    for i, lab in re.findall(r'<a href="#([\w-]+)" data-sec="\1">([^<]*)</a>', page):
        seen.setdefault(i, html.unescape(lab))
    side, bar = menus(rel, markets, current, list(seen.items()))
    page = re.sub(r"<!--side-->.*?<!--/side-->", lambda _: side, page, count=1, flags=re.S)
    return re.sub(r"<!--bar-->.*?<!--/bar-->", lambda _: bar, page, count=1, flags=re.S)


def page_shell(title, rel, markets, current, header, main, sections=()):
    """rel = path back to the site root ('' for Home, '../' for a market page).
    sections = (id, label) pairs for the 'jump to' links on a market page."""
    side, bar = menus(rel, markets, current, sections)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="{INK}">
<meta name="apple-mobile-web-app-title" content="Pixel Invest">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<title>{title}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{quote(logo.svg())}">
<link rel="apple-touch-icon" href="{rel}apple-touch-icon.png">
<link rel="manifest" href="{rel}site.webmanifest">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
/* Pixel Invest. Inter throughout, like Pixel Ag: heavy and tightly set for the figures
   that answer "am I ahead?". Ultramarine is always your account,
   dashed grey its index fund; green ahead, red behind, amber a heads-up for Friday.
   The race board (each market a lane, its bar left or right of the centre line) is the one
   loud thing; it is the Home hero, the side menu on a computer and the pinned strip on a phone. */
:root{{color-scheme:dark;--sans:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
*{{box-sizing:border-box}}
html{{background:{PAGE}}}
body{{margin:0;background:{PAGE};color:{T1};font-family:var(--sans);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}}
a{{color:{YOU}}}
a:focus-visible,summary:focus-visible,button:focus-visible{{outline:2px solid {YOU};outline-offset:2px;border-radius:4px}}
.wrap{{max-width:680px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left));padding-right:max(16px,env(safe-area-inset-right))}}
.pos{{color:{GREEN}}} .neg{{color:{RED}}} .amb{{color:{AMBER}}} .flat{{color:{T3}}} .wait{{color:{AMBER}}}
.fig{{font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.02em}}

/* Header: the scoreboard, closed by a heavy rule */
.hdr{{background:{INK};border-bottom:4px solid {GOLD};padding-top:max(22px,env(safe-area-inset-top))}}
.hin{{display:flex;flex-direction:column;padding-bottom:22px}}
.brandrow{{display:flex;align-items:center;gap:12px;margin-bottom:24px;color:{T1};text-decoration:none}}
.brandrow svg{{width:34px;height:34px;flex:none}}
/* Market pages on a phone: the logo above the pinned strip, a link back to Home */
.topbrand{{background:{INK};padding-top:max(16px,env(safe-area-inset-top))}}
.topbrand .brandrow{{margin:0;padding-bottom:6px;align-self:flex-start;display:inline-flex}}
.topbrand + .strip{{padding-top:0}}
.topbrand ~ .hdr{{padding-top:20px}}
.brand{{margin:0;font-size:16px;font-weight:800;letter-spacing:-.01em;color:#fff}}
.tagline{{margin:1px 0 0;font-size:13px;color:{T4}}}
.mtop{{display:flex;justify-content:space-between;align-items:baseline;gap:12px}}
.mname{{margin:0;font-size:19px;font-weight:700;letter-spacing:-.015em}}
.msub{{margin:2px 0 0;font-size:14px;color:{T4}}}
.verdict{{margin:12px 0 0;font-size:34px;font-weight:800;letter-spacing:-.035em;line-height:1.08;font-variant-numeric:tabular-nums;text-wrap:balance}}
.worth{{margin:10px 0 0;font-size:15px;color:{T3};font-variant-numeric:tabular-nums}}
.state{{font-size:12px;font-weight:600;padding:3px 8px;border-radius:4px;white-space:nowrap;background:{PANEL2};color:{T3}}}
.state.on{{color:{GREEN};background:rgba(107,211,90,.14)}} .state.off{{color:#FF9A90;background:rgba(255,90,78,.16)}} .state.wait{{color:{AMBER};background:rgba(226,176,74,.14)}}
.asof{{margin:12px 0 0;font-size:13px;color:{T4};display:flex;align-items:baseline;gap:7px}}
.dot{{width:7px;height:7px;border-radius:50%;background:{GREEN};flex:none;align-self:flex-start;margin-top:6px;animation:blink 2s ease-in-out infinite}}
@keyframes blink{{50%{{opacity:.25}}}}
@media(prefers-reduced-motion:reduce){{.dot{{animation:none}}}}
.fxnote{{margin:4px 0 0;font-size:12px;color:{T5}}}

/* You against the index fund */
table.vs{{width:100%;border-collapse:collapse;margin-top:18px;font-variant-numeric:tabular-nums}}
.vs th,.vs td{{padding:9px 0;border-top:1px solid {LINE};white-space:nowrap;text-align:right;font-size:15px}}
.vs thead th{{border-top:0;padding:0 0 6px;font-size:12px;font-weight:600}}
.vs tbody th{{text-align:left;font-weight:500;color:{T3};font-size:14px}}
.vs td b{{font-weight:700}} .vs td small{{font-size:12px;color:{T4};margin-left:6px}}
.vs td:last-child,.vs thead th:last-child{{padding-left:18px;width:1%}} .vs td:last-child{{color:{T3}}}
.you,.fund{{white-space:nowrap}}
.you{{color:{YOU}}} .fund{{color:{T4}}}
.you::before,.fund::before{{content:"";display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:3px}}
.you::before{{background:{YOU}}}
.fund::before{{background:repeating-linear-gradient(90deg,{FUND} 0 4px,transparent 4px 7px)}} .fund.solid::before{{background:{FUND}}}
.sw{{color:{T3};white-space:nowrap}} .sw::before{{content:"";display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:3px;background:var(--c)}}
.sw.dash::before{{background:repeating-linear-gradient(90deg,var(--c) 0 4px,transparent 4px 7px)}}

/* The race: a lane per market, a bar from the centre line (right = ahead of its fund) */
.lane{{text-decoration:none;color:{T3}}}
.lane .short,.lane .st{{display:none}}
.gauge{{position:relative;display:block}}
.gauge::before{{content:"";position:absolute;left:50%;top:0;bottom:0;width:2px;margin-left:-1px;background:{T1};opacity:.5}}
.gauge i{{position:absolute;top:0;bottom:0;width:calc(var(--w) / 2)}}
.gauge i.pos{{left:50%;background:{GREEN};border-radius:0 2px 2px 0}}
.gauge i.neg{{right:50%;background:{RED};border-radius:2px 0 0 2px}}
.gauge i.flat{{left:50%;width:0}}

/* Home: the board */
.board{{margin-top:22px}}
.bkey{{display:grid;grid-template-columns:7.4rem minmax(0,1fr) 4.8rem;column-gap:12px;font-size:12px;color:{T5};padding-bottom:6px}}
.bkey span{{grid-column:2;grid-row:1;justify-self:end;padding-right:calc(50% + 8px)}}
.bkey span + span{{justify-self:start;padding:0 0 0 calc(50% + 8px)}}
.board .lane{{display:grid;grid-template-columns:7.4rem minmax(0,1fr) 4.8rem;grid-template-rows:auto auto;column-gap:12px;align-items:center;min-height:62px;padding:9px 0;border-top:1px solid {LINE};color:{T1}}}
.board .lane:last-child{{border-bottom:1px solid {LINE}}}
.board .ln{{grid-column:1;grid-row:1;font-size:15px;font-weight:600;align-self:end}}
.board .st{{display:block;grid-column:1;grid-row:2;font-size:12px;color:{T4};align-self:start}}
.board .gauge{{grid-column:2;grid-row:1/3;align-self:stretch;margin-block:-9px}}
.board .gauge i{{top:50%;bottom:auto;height:20px;margin-top:-10px}}
.board .amt{{grid-column:3;grid-row:1/3;text-align:right;font-size:19px;line-height:1}}
.board .lane:hover .ln{{text-decoration:underline;text-underline-offset:3px}}

/* Phone: the pinned strip on a market page, with links to the page's sections */
.side{{display:none}}
.strip{{position:sticky;top:0;z-index:20;background:{INK};border-bottom:1px solid {LINE};padding-top:env(safe-area-inset-top)}}
.strip .lanes{{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr))}}
.strip .lane{{position:relative;display:flex;flex-direction:column;align-items:center;gap:1px;min-height:54px;padding:8px 2px 9px;color:{T4}}}
.strip .ln .full{{display:none}} .strip .ln .short{{display:inline}}
.strip .ln{{font-size:13px;font-weight:600}}
.strip .amt{{font-size:12px;line-height:1.3;white-space:nowrap}}
.strip .all .amt{{font-size:12px;font-weight:500;color:{T5};line-height:1.4}}
.strip .gauge{{width:72%;height:5px;margin-top:4px}}
.strip .gauge::before{{top:-2px;bottom:-2px}}
.strip .lane.on{{color:{T1}}}
.strip .lane.on::after{{content:"";position:absolute;left:12%;right:12%;bottom:0;height:3px;background:{T1};border-radius:2px 2px 0 0}}
.secs{{display:flex;gap:20px;overflow-x:auto;scrollbar-width:none;border-top:1px solid {LINE2}}}
.secs::-webkit-scrollbar{{display:none}}
.secs a{{flex:none;font-size:13px;font-weight:500;color:{T4};text-decoration:none;padding:9px 0 7px;border-bottom:2px solid transparent}}
.secs a.on{{color:{T1};border-bottom-color:{T1}}}
main [id]{{scroll-margin-top:104px}}

/* Computer: the board becomes the side menu */
@media(min-width:900px){{
 .app{{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:100vh}}
 .side{{display:flex;flex-direction:column;background:{INK};border-right:1px solid {LINE};padding:22px 14px;position:sticky;top:0;height:100vh;overflow-y:auto}}
 .side .brandrow{{margin:0 6px 14px;padding-bottom:16px;border-bottom:4px solid {GOLD}}}
 .side .home{{font-size:14px;font-weight:600;color:{T3};text-decoration:none;padding:9px 10px;border-radius:6px}}
 .side .home.on{{color:{T1};background:{PANEL}}}
 .side .home:hover,.side .lane:hover{{background:{PANEL}}}
 .side .lane{{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:baseline;row-gap:8px;padding:11px 10px 12px;border-radius:6px;margin-top:2px}}
 .side .ln{{font-size:14px;font-weight:600}}
 .side .amt{{font-size:14px}}
 .side .gauge{{grid-column:1/-1;height:6px}}
 .side .gauge::before{{top:-3px;bottom:-3px}}
 .side .lane.on{{background:{PANEL};color:{T1};box-shadow:inset 3px 0 0 {T1}}}
 .side .sub{{display:flex;flex-direction:column;margin:4px 0 10px 13px;border-left:1px solid {LINE}}}
 .side .sub a{{font-size:13px;color:{T4};text-decoration:none;padding:6px 12px;margin-left:-1px;border-left:2px solid transparent}}
 .side .sub a:hover{{color:{T1}}}
 .side .sub a.on{{color:{T1};font-weight:600;border-left-color:{T1}}}
 .strip,.hdr .brandrow,.topbrand{{display:none}}
 .hdr{{padding-top:34px}}
 main [id]{{scroll-margin-top:24px}}
}}

/* Page body */
main.wrap{{padding-top:28px;padding-bottom:56px;display:flex;flex-direction:column;gap:40px}}
.sec{{display:flex;flex-direction:column;gap:12px}}
h2{{margin:0;font-size:19px;font-weight:700;letter-spacing:-.02em;text-wrap:balance}}
h3{{margin:10px 0 0;font-size:15px;font-weight:600}}
.lead{{margin:0;font-size:15px;color:{T3};max-width:62ch}}
.heads{{display:flex;flex-direction:column;gap:3px;background:rgba(226,176,74,.10);border:1px solid rgba(226,176,74,.32);border-radius:8px;padding:12px 14px;font-size:14px;color:{T1}}}
.heads b{{color:{AMBER};font-size:15px}}
.key{{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px}}
.chart{{width:100%;height:auto;display:block;shape-rendering:geometricPrecision}} .chart .ax{{fill:{T4};font-size:11px;font-family:var(--sans)}}
.chart .hot{{fill:transparent;cursor:crosshair}} .chart .hot:hover{{fill:{YOU};fill-opacity:.25}}
.empty{{margin:0;color:{T3};font-size:14px}}
.note{{font-size:12px;color:{T5};line-height:1.6;margin:0;max-width:72ch}}

/* Stock rows */
.rows{{display:flex;flex-direction:column}}
.row{{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:baseline;gap:3px 12px;padding:12px 0;border-top:1px solid {LINE2}}}
.row:last-child{{border-bottom:1px solid {LINE2}}}
.row .nm{{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.row .nm b{{font-weight:700;margin-right:8px}} .row .nm span{{color:{T4};font-size:13px}}
.row .r{{text-align:right;font-size:13px;color:{T4};white-space:nowrap;font-variant-numeric:tabular-nums}}
.row .st{{font-size:13px;color:{T3}}} .st.ok{{color:{GREEN}}} .st.keep{{color:{AMBER}}} .st.sell{{color:{RED}}}
.yr{{grid-column:1/-1;display:flex;align-items:center;gap:8px;margin-top:7px;font-size:12px;color:{T4};white-space:nowrap;font-variant-numeric:tabular-nums}}
.yr .track{{flex:1;min-width:60px;position:relative;height:4px;border-radius:2px;background:{PANEL3}}}
.yr .track i{{position:absolute;top:50%;width:10px;height:10px;margin:-5px 0 0 -5px;border-radius:50%;background:{T1};box-shadow:0 0 0 2px {PAGE}}}
.yr > span:first-child{{min-width:3.4em}} .yr > span:nth-child(3){{min-width:3.4em;text-align:right}}
.yr .off{{min-width:7.6em;text-align:right;color:{T3}}}
.chip{{font-size:14px;font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}}
.chip.pos{{color:{GREEN}}} .chip.neg{{color:{RED}}} .chip.flat{{color:{T3}}}
.tag{{font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px;vertical-align:1px}}
.tag.buy{{background:rgba(107,211,90,.14);color:{GREEN}}} .tag.sell{{background:rgba(255,90,78,.14);color:{RED}}}
.stack{{display:flex;height:14px;border-radius:3px;overflow:hidden;gap:2px}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;color:{T3}}} .legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}}
.rules{{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:12px;font-size:15px}} .rules b{{display:block;font-size:13px;font-weight:700;color:{YOU}}}

/* Folded extras */
.folds{{display:flex;flex-direction:column;border-top:1px solid {LINE}}}
details.fold{{border-bottom:1px solid {LINE}}}
details.fold summary{{cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:56px;font-weight:600;font-size:16px}}
details.fold summary::-webkit-details-marker{{display:none}}
details.fold summary::after{{content:"+";color:{T1};font-size:22px;font-weight:400;line-height:1}}
details.fold[open] summary::after{{content:"\\2212"}}
.fold .in{{display:flex;flex-direction:column;gap:12px;padding-bottom:22px}}
.stats{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
.stat{{background:{PANEL};border:1px solid {LINE2};border-radius:6px;padding:10px 12px;display:flex;flex-direction:column;gap:2px}}
.stat .k{{font-size:12px;color:{T4}}} .stat .v{{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}} .stat small{{color:{T4};font-weight:500;font-size:12px}}
.warn{{margin:0;font-size:13px;line-height:1.5;color:{T3}}}
.trades{{overflow-x:auto}}
.trades table{{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}}
.trades th{{text-align:left;color:{T4};font-weight:600;font-size:12px;padding:8px;border-bottom:1px solid {LINE}}}
.trades td{{padding:9px 8px;border-bottom:1px solid {LINE2};white-space:nowrap;font-variant-numeric:tabular-nums}}
.trades .chip{{font-size:13px}}
.bt-read,.wk-read{{display:flex;flex-direction:column;gap:2px}}
.bt-read{{min-height:56px}}
.bt-read .rv,.wk-read .rv{{font-size:24px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1.05}}
.bt-read .rs,.wk-read .rs{{font-size:13px;color:{T3};font-variant-numeric:tabular-nums}}
.wk-read{{min-height:46px}} .wk-read .rv{{font-size:20px}}
.pill{{font-weight:700;font-size:12px;padding:2px 7px;border-radius:4px;margin-left:6px;white-space:nowrap}}
.pill.pos{{background:rgba(107,211,90,.14);color:{GREEN}}} .pill.neg{{background:rgba(255,90,78,.14);color:{RED}}}
.bt-plot,.wk-plot{{touch-action:pan-y;user-select:none;-webkit-user-select:none}}
.bt-plot svg,.wk-plot svg{{display:block;width:100%;height:auto;overflow:visible}}
.bt-plot text,.wk-plot text{{font-family:var(--sans);font-variant-numeric:tabular-nums}}
.bt-chips{{display:flex;gap:6px}}
.bt-chips button{{font:inherit;font-size:13px;font-weight:600;color:{T3};background:transparent;border:1px solid {LINE};border-radius:6px;padding:7px 14px;min-height:36px;cursor:pointer}}
.bt-chips button[aria-pressed="true"]{{background:{T1};color:{PAGE};border-color:{T1}}}

/* Home: one block per market under the board */
.mcards{{display:flex;flex-direction:column}}
.mcard{{display:block;text-decoration:none;color:inherit;padding:26px 0 28px;border-top:1px solid {LINE}}}
.mcard:first-child{{border-top:0;padding-top:4px}}
.mcard .verdict{{font-size:24px;margin-top:10px}}
.mcard .worth{{margin-top:6px}}
.mcard table.vs{{margin-top:14px}}
.mcard .heads{{margin-top:16px}}
.mcard .last{{margin:12px 0 0;font-size:14px;color:{T4}}}
.mcard .go{{display:inline-block;margin-top:12px;font-size:14px;font-weight:600;color:{YOU}}}
.mcard:hover .go{{text-decoration:underline;text-underline-offset:3px}}
</style></head><body><div class="app">
<nav class="side" aria-label="Markets"><a class="brandrow" href="{rel or './'}">{logo.svg()}<span class="brand">Pixel Invest</span></a>
{side}</nav>
<div class="content">
{"" if current is None else f'<div class="topbrand"><div class="wrap"><a class="brandrow" href="{rel}">{logo.svg()}<span class="brand">Pixel Invest</span></a></div></div>'}
{bar}
{header}
{main}
</div></div>
<script>(function(){{var t=Date.now();document.addEventListener('visibilitychange',function(){{
if(document.visibilityState==='visible'&&Date.now()-t>5*60*1000)location.reload();}});}})();
/* Light up the section you're reading, in the side menu and the phone strip */
(function(){{var links=[].slice.call(document.querySelectorAll('[data-sec]'));if(!links.length)return;
var ids=[];links.forEach(function(a){{var i=a.getAttribute('data-sec');if(ids.indexOf(i)<0)ids.push(i);}});
var secs=ids.map(function(i){{return document.getElementById(i);}}).filter(Boolean),cur=null,busy=false,want=null;
links.forEach(function(a){{a.addEventListener('click',function(){{want=a.getAttribute('data-sec');}});}});
['wheel','touchstart','keydown'].forEach(function(e){{window.addEventListener(e,function(){{want=null;}},{{passive:true}});}});
function pick(){{busy=false;var bar=document.querySelector('.strip'),off=(bar&&bar.offsetHeight?bar.offsetHeight:0)+40,id=null;
var done=window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-4;
secs.forEach(function(s){{if(s.getBoundingClientRect().top<=off)id=s.id;}});if(done&&id)id=secs[secs.length-1].id;if(want)id=want;
if(id===cur)return;cur=id;links.forEach(function(a){{var hit=a.getAttribute('data-sec')===id;a.classList.toggle('on',hit);
if(hit&&a.parentNode.classList.contains('secs')){{var r=a.parentNode;r.scrollLeft=Math.max(0,a.offsetLeft-r.offsetWidth/2+a.offsetWidth/2);}}}});}}
window.addEventListener('scroll',function(){{if(!busy){{busy=true;requestAnimationFrame(pick);}}}},{{passive:true}});pick();}})();</script>
</body></html>"""


_SUMMARIES = {}


def summary_state(m):
    """(label, css class) for a market's status, from its summary."""
    if not m.get("active", True):
        return ("Not started", "wait")
    s = _SUMMARIES.get(m["slug"])
    if not s:
        return (f"Starts {pd.Timestamp(m['start_date']):%b %d}" if m.get("start_date") else "Waiting", "wait")
    held = s.get("held", 1 if s["risk_on"] else 0)      # what's held, not where the switch points
    return ("Invested", "on") if held else ("Cash", "off")


def load_summaries(markets, summary_path):
    for m in markets:
        p = summary_path(m["slug"])
        try:
            _SUMMARIES[m["slug"]] = json.load(open(p)) if os.path.exists(p) else None
        except ValueError:
            _SUMMARIES[m["slug"]] = None


def updated_note(cfg):
    updated = datetime.now(ZoneInfo("America/New_York"))
    divs = ("Dividends are paid into the account's cash, and the index fund comparison includes its dividends."
            if cfg.get("dividends") else "Prices only: dividends aren't counted, for the account or the index fund.")
    return (f'<p class="note">Pretend money only. Trades happen at each Friday\'s closing price, with no fees. {divs} '
            f'Prices in {cfg["currency"]}. Today and this week are worked out from the shares held; nothing is traded or '
            f'recorded until the week\'s last close. Updated {updated:%b} {updated.day}, {updated:%Y} at {clock(updated)} '
            f'New York time. Past and paper results don\'t guarantee future returns.</p>')


def backtest_block(cfg, bt, open_=True):
    """How the rules did since 2016. Folded away on a trading market's page; open on a waiting one."""
    if not bt:
        return ""
    r, q = bt["rules"], bt["qqq"]
    data = json.dumps({"curve": [[d, round(a, 2), round(b, 2)] for d, a, b in bt["curve"]],
                       "sym": SYM, "bench": LABEL})
    inner = f'''<p class="lead">{money(10_000, False)} run through the same rules every week since {bt["start"][:4]}, with {bt["cost_pct"] * 100:.1f}% trading costs
and dividends included. Drag across the chart to read any date.</p>
{key("The rules", "Just holding " + LABEL, solid=True)}
<div class="bt-read" aria-live="polite"></div>
<div class="bt-plot">{backtest_chart(bt)}</div>
<div class="bt-chips" role="group" aria-label="Time range">
<button type="button" data-y="1" aria-pressed="false">1Y</button><button type="button" data-y="3" aria-pressed="false">3Y</button>
<button type="button" data-y="5" aria-pressed="false">5Y</button><button type="button" data-y="0" aria-pressed="true">All</button></div>
<p class="lead bt-note" hidden>Both lines restart at {money(10_000, False)} at the start of the range.</p>
<div class="stats">
<div class="stat"><span class="k">Yearly return</span><span class="v">{r["cagr"] * 100:.1f}% <small>vs {q["cagr"] * 100:.1f}%</small></span></div>
<div class="stat"><span class="k">Worst drop</span><span class="v">&minus;{abs(r["drop"]) * 100:.1f}% <small>vs &minus;{abs(q["drop"]) * 100:.1f}%</small></span></div>
<div class="stat"><span class="k">Winning trades</span><span class="v">{(bt["win_rate"] or 0) * 100:.0f}%</span></div>
<div class="stat"><span class="k">Trades a year</span><span class="v">{bt["trades_per_year"]:.0f}</span></div></div>
<p class="warn">This test uses today's {esc(cfg["name"])} list, so it includes later winners and leaves out companies that dropped out of the index.
Real results would very likely have been lower. Refreshed {pd.Timestamp(bt["computed_on"]):%b %d, %Y}.</p>
<script type="application/json" class="bt-data">{data}</script>{BT_SCRIPT}'''
    title = f"How the rules did since {bt['start'][:4]}"
    return (f'<details class="fold bt"{" open" if open_ else ""}><summary>{title}</summary>'
            f'<div class="in">{inner}</div></details>')


# Interactive backtest chart ("stock app" style): drag to read, 1Y/3Y/5Y/All.
# The static SVG inside .bt-plot is the fallback if scripts don't run.
BT_SCRIPT = """<script>
(function(){
var box=document.currentScript.closest('.bt'); if(!box) return;
var D=JSON.parse(box.querySelector('.bt-data').textContent), C=D.curve, NS='http://www.w3.org/2000/svg';
var GOLD='#E2C067', GREY='#A39D8E', GRID='rgba(255,255,255,.07)', T4='#A39D8E', T5='#7E7866', years=0;
function el(t,a,p){var e=document.createElementNS(NS,t);for(var k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e;}
function day(s){return new Date(s+'T12:00:00');}
function money(v){return D.sym+Math.round(v).toLocaleString('en-US');}
function short(v){return v>=1e5?D.sym+Math.round(v/1e3)+'k':v>=1e4?D.sym+(v/1e3).toFixed(0)+'k':D.sym+(v/1e3).toFixed(1).replace(/\.0$/,'')+'k';}
function slice(){ if(!years) return C;
  var end=day(C[C.length-1][0]); end.setFullYear(end.getFullYear()-years);
  var p=C.filter(function(r){return day(r[0])>=end;}), a0=p[0][1], b0=p[0][2];
  return p.map(function(r){return [r[0], r[1]/a0*1e4, r[2]/b0*1e4];}); }
function ticks(lo,hi){ var c=[5e3,7e3,1e4,1.2e4,1.5e4,2e4,3e4,5e4,1e5,2e5,3e5,5e5,1e6], t=c.filter(function(v){return v>=lo&&v<=hi;});
  return t.length>4?[t[0],t[Math.floor(t.length/3)],t[Math.floor(2*t.length/3)],t[t.length-1]]:t; }
function read(r){ var d=r[1]-r[2];
  box.querySelector('.bt-read').innerHTML='<span class="rv">'+money(r[1])+'</span><span class="rs">'+day(r[0]).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})+
  ' &middot; '+D.bench+' '+money(r[2])+'<span class="pill '+(d>=0?'pos':'neg')+'">'+(d>=0?'+':'&minus;')+money(Math.abs(d))+' vs '+D.bench+'</span></span>'; }
function draw(){
  var host=box.querySelector('.bt-plot'), w=host.clientWidth; if(!w) return;
  var data=slice(), h=Math.round(Math.min(260,Math.max(200,w*.55))), pl=4, pr=46, pt=10, pb=22, n=data.length;
  var vals=[]; data.forEach(function(r){vals.push(r[1],r[2]);});
  var lo=Math.min.apply(null,vals)*.97, hi=Math.max.apply(null,vals)*1.03, L=Math.log;
  function x(i){return pl+i/(n-1)*(w-pl-pr);} function y(v){return pt+(1-(L(v)-L(lo))/(L(hi)-L(lo)))*(h-pt-pb);}
  host.innerHTML=''; var svg=el('svg',{viewBox:'0 0 '+w+' '+h,width:w,height:h,role:'img','aria-label':'The rules against '+D.bench},host);
  ticks(lo,hi).forEach(function(v){ el('line',{x1:pl,x2:w-pr,y1:y(v),y2:y(v),stroke:GRID},svg);
    el('text',{x:w-pr+6,y:y(v)+4,'font-size':11,fill:T4},svg).textContent=short(v); });
  var seen={}, yrs=[]; data.forEach(function(r,i){var k=r[0].slice(0,4); if(!seen[k]){seen[k]=1;yrs.push([k,i]);}});
  var step=Math.ceil(yrs.length/5); yrs.forEach(function(p,k){ if(k%step||x(p[1])>w-pr-26) return;
    el('text',{x:x(p[1]),y:h-6,'font-size':11,fill:T5},svg).textContent = n<70 ? day(data[p[1]][0]).toLocaleDateString('en-US',{month:'short'})+" '"+p[0].slice(2) : p[0]; });
  function path(k){return data.map(function(r,i){return (i?'L':'M')+x(i).toFixed(1)+','+y(r[k]).toFixed(1);}).join('');}
  el('path',{d:path(2),fill:'none',stroke:GREY,'stroke-width':1.25,'stroke-opacity':.85,'stroke-linejoin':'round'},svg);
  el('path',{d:path(1),fill:'none',stroke:GOLD,'stroke-width':1.75,'stroke-linejoin':'round'},svg);
  var v=el('line',{y1:pt,y2:h-pb,stroke:'rgba(244,240,230,.35)',visibility:'hidden'},svg),
      d1=el('circle',{r:4.5,fill:GOLD,stroke:'#14120D','stroke-width':2,visibility:'hidden'},svg),
      d2=el('circle',{r:3.5,fill:GREY,stroke:'#14120D','stroke-width':2,visibility:'hidden'},svg);
  function at(e){var b=svg.getBoundingClientRect(),px=(e.clientX-b.left)/b.width*w;return Math.max(0,Math.min(n-1,Math.round((px-pl)/(w-pl-pr)*(n-1))));}
  function show(i){var r=data[i];[v,d1,d2].forEach(function(e){e.setAttribute('visibility','visible');});
    v.setAttribute('x1',x(i));v.setAttribute('x2',x(i));d1.setAttribute('cx',x(i));d1.setAttribute('cy',y(r[1]));d2.setAttribute('cx',x(i));d2.setAttribute('cy',y(r[2]));read(r);}
  host.onpointermove=function(e){show(at(e));}; host.onpointerdown=function(e){show(at(e));};
  host.onpointerleave=function(){[v,d1,d2].forEach(function(e){e.setAttribute('visibility','hidden');});read(data[n-1]);};
  read(data[n-1]);
}
box.querySelectorAll('.bt-chips button').forEach(function(b){ b.addEventListener('click',function(){ years=+b.dataset.y;
  box.querySelectorAll('.bt-chips button').forEach(function(o){o.setAttribute('aria-pressed',String(o===b));});
  box.querySelector('.bt-note').hidden=!years; draw(); }); });
var t; window.addEventListener('resize',function(){clearTimeout(t);t=setTimeout(draw,150);});
box.addEventListener('toggle',function(){ if(box.open) draw(); });
draw();
})();
</script>"""


def rules_block(cfg):
    return f'''<section class="sec" id="rules"><h2>The rules</h2><ul class="rules">
<li><b>Every Friday</b>Rank the {esc(cfg["name"])} by 3-month gain.</li>
<li><b>Buy</b>Own the top {cfg["top_n"]}, with equal money in each new stock.</li>
<li><b>Sell</b>When a stock falls below rank {cfg["exit_rank"]}.</li>
<li><b>Safety switch</b>When {LABEL} closes below its {cfg["sma_days"]}-day average, sell everything and wait in cash.</li></ul></section>'''


def set_market(cfg):
    global SYM, LABEL
    SYM, LABEL = cfg.get("symbol", "$"), cfg.get("bench_label", "QQQ")


def market_header(cfg, state, body):
    """A market page's header: back link, name and badge, then `body` (the standing and table)."""
    label, cls = state
    return f'''<header class="hdr"><div class="wrap hin">
<div class="mtop"><h1 class="mname">{esc(cfg["name"])}</h1><span class="state {cls}">{label}</span></div>
<p class="msub">{esc(cfg["about"])}, up against {LABEL}</p>
{body}
</div></header>'''


# --------------------------------------------------------------------------
# A market's page
# --------------------------------------------------------------------------
def build_dashboard(cfg, acct, trades, holdings, status, watch, last_rebal, prices, bt, markets, ledger=None, day_info=None,
                    curve=None, latest=None):
    """`latest`: every price downloaded, today's live row included, for the 52-week bars
    (`prices` stops at the last day in the record)."""
    set_market(cfg)
    start_val = cfg["capital"]
    now_val = acct["paper_account"].iloc[-1]
    qqq_val = acct["qqq_same_money"].iloc[-1]
    # The headline uses the latest prices (today so far, while the market is open)
    val = day_info["value"] if day_info else now_val
    bench = day_info["bench_value"] if day_info else qqq_val
    top_n, exit_rank = cfg["top_n"], cfg["exit_rank"]
    start = pd.Timestamp(cfg["start_date"])
    held = {h["ticker"] for h in holdings}
    on = status["risk_on"]
    invested = bool(holdings)          # what the account holds now (changes only at a week's last close)
    gap = (status["qqq"] / status["sma"] - 1) * 100

    closed = trades[trades["action"] == "SELL"]
    wins = int((closed["gain_pct"] > 0).sum())
    record = [f"{wins} of {len(closed)} sells made money"] if len(closed) else []
    if ledger and ledger.get("dividends_received"):
        record.append(f"{money(ledger['dividends_received'], False)} in dividends so far")

    # Your stocks: one list of what's held, plus anything sold at the last close
    week = trades[trades["date"] == last_rebal.date()]
    started = last_rebal.normalize() == start.normalize()
    buys, sells = week[week["action"] == "BUY"], week[week["action"] == "SELL"]
    when = f"{last_rebal:%a} {short_date(last_rebal)}"
    moves = dict(day_info["movers"]) if day_info and day_info.get("movers") else {}
    today = day_name(day_info, short=True) if day_info else ""

    def status_of(rank):
        if rank is None:
            return "", "Keeping"
        if rank <= top_n:
            return "ok", f"Ranked {rank}, keeping"
        if rank <= exit_rank:
            return "keep", f"Ranked {rank}, sold if it drops below {exit_rank}"
        return "sell", f"Ranked {rank}, sells at Friday's close"

    px = latest if latest is not None else prices
    bars = {}

    def bar(t):
        bars[t] = year_range(px, t)
        return range_bar(bars[t])

    def row(ticker, company, right, left2="", right2="", extra=""):
        return (f'<div class="row"><div class="nm"><b>{ticker}</b><span>{company}</span></div><div class="r">{right}</div>'
                f'<div class="st-l">{left2}</div><div class="r">{right2}</div>{extra}</div>')

    rank_note = (f"Each Friday the rules rank every {esc(cfg['name'])} stock by its 3-month gain. "
                 f"A stock stays while it's in the top {exit_rank}.")
    parts = []
    if holdings:
        title = f"Your {len(holdings)} stock{'s' if len(holdings) != 1 else ''}"
        if started:
            lead = f"Bought at the close on {when}, with equal money in each. {rank_note}"
        elif week.empty:
            lead = f"No trades at the close on {when}: every stock still ranked high enough to keep. {rank_note}"
        else:
            did = " and ".join(x for x in [f"bought {len(buys)}" if len(buys) else "", f"sold {len(sells)}" if len(sells) else ""] if x)
            lead = f"At the close on {when} the rules {did}. {rank_note}"
        hrows = []
        for h in holdings:
            cls, st = status_of(h["rank"])
            new = ' <span class="tag buy">New</span>' if (not started and h["buy_date"] == str(last_rebal.date())) else ""
            mv = moves.get(h["ticker"])
            hrows.append(row(f'{esc(h["ticker"])}{new}', esc(name(h["ticker"])), chip(h["gain_pct"]),
                             f'<span class="st {cls}">{st}</span>',
                             f'{today} {signed_pct(mv, 1)}' if mv is not None else "", bar(h["ticker"])))
        parts.append(f'<div class="rows">{"".join(hrows)}</div>{bar_note(bars)}')
    else:
        title = "Your stocks"
        lead = f"None right now: the account is in cash because {LABEL} is under its 200-day average (the safety switch)."
    if len(sells):
        srows = "".join(row(esc(t["ticker"]), esc(name(t["ticker"])), chip(t["gain_pct"]),
                            f'<span class="st">Sold at {money(t["price"])}</span>')
                        for t in sells.to_dict("records"))
        parts.append(f'<h3>Sold at the close on {when}</h3><div class="rows">{srows}</div>')
    if holdings:
        parts.append(f'<h3>Where your money is</h3>{sector_bar(holdings, status["cash"])}')
    else:
        nxt = [t for t in watch.index if t not in held][:top_n]
        nrows = "".join(row(esc(t), esc(name(t)), f'<span class="chip pos">+{watch[t] * 100:.0f}%</span>',
                            '<span class="st">3-month gain</span>', extra=bar(t)) for t in nxt)
        parts.append(f'<h3>Next buys if the switch turns back on</h3>'
                     f'<p class="lead">If {LABEL} closes above {money(status["sma"])} on a Friday, '
                     f'today\'s top {top_n} by 3-month gain are bought at that close.</p>'
                     f'<div class="rows">{nrows}</div>'
                     f'{bar_note(bars)}')
    if record:
        parts.append(f'<p class="lead">{". ".join(record)}.</p>')
    stocks_html = f'<section class="sec" id="stocks"><h2>{title}</h2><p class="lead">{lead}</p>{"".join(parts)}</section>'

    # The safety switch chart
    b = cfg["benchmark"]
    sw = pd.DataFrame({"qqq": prices[b], "sma": prices[b].rolling(cfg["sma_days"]).mean()}
                      ).dropna().loc[:status["last_day"]].iloc[-130:]
    if on == invested:
        switch_lead = (f"{LABEL} is above the dashed line, so the rules stay invested." if on else
                       f"{LABEL} is below the dashed line, so the rules sit in cash. Shaded days are below the line.")
    elif invested:
        switch_lead = (f"{LABEL} has dipped below the dashed line. The rules act on it only at the week's last close, "
                       f"so the stocks are kept until then. Shaded days are below the line.")
    else:
        switch_lead = (f"{LABEL} is back above the dashed line. The rules act on it only at the week's last close. "
                       f"Shaded days are below the line.")
    acct_html = (account_chart(acct) if len(acct) > 1 else
                 '<p class="empty">The chart starts after the first full day.</p>')

    hist = "".join(f'''<tr><td>{pd.Timestamp(t["date"]):%b %d, %Y}</td>
<td><span class="tag {'buy' if t["action"] == "BUY" else 'sell'}">{t["action"].title()}</span></td>
<td><b>{esc(t["ticker"])}</b></td><td>{money(t["price"])}</td><td>{money(t["value"], False)}</td><td>{chip(t["gain_pct"])}</td></tr>'''
                   for t in trades.iloc[::-1].to_dict("records"))

    body = (f'{standing(val - bench, LABEL, lambda x: money(x, False))}'
            f'<p class="worth">Worth {money(val, False)}, from {money(start_val, False)} on {short_date(start)}</p>'
            f'{vs_table(spans(day_info, start_val, val, bench, start, signed_money), LABEL)}{as_of(day_info)}')
    header = market_header(cfg, ("Invested", "on") if invested else ("In cash", "off"), body)
    latest_sw = (day_info or {}).get("switch")         # the switch at the latest prices
    heads = switch_heads_up({"risk_on": latest_sw["on"] if latest_sw else on, "held": len(holdings)},
                            latest_sw["gap"] if latest_sw else gap, week_closed(day_info))
    week_html = (f'<section class="sec" id="week"><h2>This week</h2>{week_chart(curve)}</section>' if curve and len(curve) > 1 else "")
    main = f'''<main class="wrap">
{heads}
{week_html}
{stocks_html}
<section class="sec" id="since"><h2>Since you started</h2><p class="lead">Started {short_date(start)} with {money(start_val, False)}. Tap a point for that day.</p>
{key()}{acct_html}</section>
{rules_block(cfg)}
<div class="folds" id="more">
<details class="fold"><summary>Safety switch chart</summary><div class="in">
<p class="lead">{switch_lead} Today {LABEL} is {status["qqq"]:.2f} and its {cfg["sma_days"]}-day average is {status["sma"]:.2f}.</p>
<div class="key"><span class="sw" style="--c:{T1}">{LABEL}</span><span class="sw dash" style="--c:{AMBER}">{cfg["sma_days"]}-day average</span></div>
{switch_chart(sw)}</div></details>
{backtest_block(cfg, bt, open_=False)}
<details class="fold"><summary>Every trade ({len(trades)})</summary><div class="in"><div class="trades"><table>
<tr><th>Date</th><th>Action</th><th>Stock</th><th>Price</th><th>Amount</th><th>Result</th></tr>{hist}</table></div></div></details>
</div>
{updated_note(cfg)}
</main>'''
    _SUMMARIES[cfg["slug"]] = {**(_SUMMARIES.get(cfg["slug"]) or {}), "risk_on": on, "held": len(holdings),
                               "value": val, "bench_value": bench, "lead": val - bench}
    sections = [("week", "This week")] if week_html else []
    sections += [("stocks", "Stocks"), ("since", "Since start"), ("rules", "Rules"), ("more", "More")]
    page = page_shell(f"{esc(cfg['name'])} &middot; Pixel Invest", "../", markets, cfg["slug"], header, main, sections)
    return page, now_val, qqq_val, week


def build_waiting(cfg, bt, markets):
    """Page for a market that hasn't started trading: its rules and backtest."""
    set_market(cfg)
    if cfg.get("active", True) and cfg.get("start_date"):
        d = pd.Timestamp(cfg["start_date"])
        starts = f"Starts {short_date(d)}"
        when = (f"The account makes its first trades at the close on {d:%A, %B} {d.day}: the top {cfg['top_n']} by "
                f"3-month gain, unless {LABEL} is below its 200-day average. This page fills in after that.")
    else:
        starts = "Not started"
        when = "This market starts once its portfolio size is picked from the test below."
    body = (f'<p class="verdict">Not trading yet</p>'
            f'<p class="worth">A {money(cfg["capital"], False)} pretend account in {cfg["currency"]}</p>')
    header = market_header(cfg, (starts, "wait"), body)
    main = f'''<main class="wrap">
<p class="lead">{when}</p>
{rules_block(cfg)}
<div class="folds">{backtest_block(cfg, bt, open_=True)}</div>
{updated_note(cfg)}
</main>'''
    return page_shell(f"{esc(cfg['name'])} &middot; Pixel Invest", "../", markets, cfg["slug"], header, main)


# --------------------------------------------------------------------------
# Home: all the accounts together, then one card per market
# --------------------------------------------------------------------------
def usd(x):
    return f"${x:,.0f}"


def signed_usd(x):
    return "$0.00" if abs(x) < 0.005 else f'{"+" if x > 0 else "&minus;"}${abs(x):,.2f}'


def home_total(markets, fx=None, race=""):
    """All the accounts added up in US$ (C$ converted at `fx`, today's rate): how far ahead of
    the index funds, what they're worth, and today, this week and since each started."""
    rate = {"US$": 1.0}
    if fx and fx.get("usd_per_cad"):
        rate["C$"] = float(fx["usd_per_cad"])
    active = [(m, _SUMMARIES.get(m["slug"])) for m in markets if m.get("active", True)]
    rows = [(rate[m["currency"]], s) for m, s in active if s and m["currency"] in rate]
    left_out = [m["name"] for m, s in active if s and m["currency"] not in rate]
    if len(rows) < 2:
        return ""
    cap = sum(r * s["capital"] for r, s in rows)
    # At the latest prices: the day's value (today so far, while markets are open) when there is one.
    val = sum(r * (s["day"]["value"] if s.get("day") else s["value"]) for r, s in rows)
    bench = sum(r * (s["day"]["bench_value"] if s.get("day") else s["bench_value"]) for r, s in rows)
    pl, bpl = val - cap, bench - cap
    table = []
    days = [(r, s["day"]) for r, s in rows if s.get("day")]
    latest = None
    if days:
        ch = sum(r * d["change"] for r, d in days)
        base = sum(r * (d["value"] - d["change"]) for r, d in days)
        bch = sum(r * d["bench_change"] for r, d in days)
        bbase = sum(r * (d["bench_value"] - d["bench_change"]) for r, d in days)
        live = [d for _, d in days if d.get("live")]
        latest = (max(live, key=lambda d: d["as_of"]) if live else max((d for _, d in days), key=lambda d: d["day"]))
        table.append((day_name(latest), ch, signed_usd(ch), ch / base * 100, bch / bbase * 100))
        wk = [(r, d) for r, d in days if "week_change" in d]
        if wk:
            wch = sum(r * d["week_change"] for r, d in wk)
            wbase = sum(r * (d["value"] - d["week_change"]) for r, d in wk)
            b1 = sum(r * d["bench_value"] for r, d in wk)
            b0 = sum(r * d["bench_value"] / (1 + d["week_bench_pct"] / 100) for r, d in wk)
            table.append(("This week", wch, signed_usd(wch), wch / wbase * 100, (b1 / b0 - 1) * 100))
    table.append(("Since start", pl, signed_usd(pl), pl / cap * 100, bpl / cap * 100))
    cad = [m["name"] for m, s in active if s and m["currency"] == "C$" and "C$" in rate]
    note = []
    if cad:
        note.append(f'{", ".join(cad)} counted in US$ at 1 C$ = {rate["C$"]:.4f} US$ ({pd.Timestamp(fx["date"]):%b %d}).')
    if left_out:
        note.append(f'Not counted (no exchange rate yet): {", ".join(left_out)}.')
    fxnote = f'<p class="fxnote">{" ".join(note)}</p>' if note else ""
    return (f'<section aria-label="All your accounts together">'
            f'{standing(pl - bpl, "the index funds", usd)}'
            f'<p class="worth">Your {len(rows)} accounts are worth {usd(val)}, from {usd(cap)} put in</p>'
            f'{race}{vs_table(table, "Index funds")}{as_of(latest, every=True)}'
            f'{fxnote}</section>')


def heads_for(s, day):
    """A Home card's switch heads-up, from the latest prices when the summary has them."""
    sw = (day or {}).get("switch")
    return switch_heads_up({"risk_on": sw["on"] if sw else s["risk_on"], "held": s.get("held")},
                           sw["gap"] if sw else None, week_closed(day))


def build_home(markets, summary_path, fx=None):
    load_summaries(markets, summary_path)
    cards = []
    for m in markets:
        set_market(m)
        s = _SUMMARIES.get(m["slug"])
        label, cls = summary_state(m)
        top = (f'<div class="mtop"><h2 class="mname">{esc(m["name"])}</h2>'
               f'<span class="state {cls}">{"In cash" if label == "Cash" else label}</span></div>'
               f'<p class="msub">{esc(m["about"])}</p>')
        if m.get("active", True) and s:
            day = s.get("day")
            val = day["value"] if day else s["value"]
            bench = day["bench_value"] if day else s["bench_value"]
            body = (f'{standing(val - bench, LABEL, lambda x: money(x, False))}'
                    f'<p class="worth">Worth {money(val, False)}</p>'
                    f'{vs_table(spans(day, s["capital"], val, bench, pd.Timestamp(s["start"]), signed_money), LABEL)}'
                    f'{heads_for(s, day)}'
                    f'<p class="last">Last trades: {esc(s["week"])}</p>'
                    f'<span class="go">Open {esc(m["name"])}</span>')
        else:
            msg = (f"First trades at the close on {pd.Timestamp(m['start_date']):%a %b} {pd.Timestamp(m['start_date']).day}"
                   if m.get("active", True) and m.get("start_date") else "Starts once its size is picked from the test")
            body = (f'<p class="verdict">Not trading yet</p><p class="worth">{msg}</p>'
                    f'<p class="last">See how the rules tested.</p>')
        cards.append(f'<a class="mcard" href="{m["slug"]}/">{top}{body}</a>')
    race = board(markets)
    total = home_total(markets, fx, race) or race
    header = f'''<header class="hdr"><div class="wrap hin">
<div class="brandrow">{logo.svg()}<div><h1 class="brand">Pixel Invest</h1>
<p class="tagline">Pretend money, up against the index funds</p></div></div>
{total}
</div></header>'''
    main = f'''<main class="wrap"><div class="mcards">{"".join(cards)}</div>
<p class="note">Each market has its own pretend account in its own currency, and trades only at Friday's close.
Past and paper results don't guarantee future returns.</p></main>'''
    return page_shell("Pixel Invest", "", markets, None, header, main)
