"""Builds the Pixel Invest pages: Home (one card per market) and a "Control Room"
page per market. Self-contained, phone-first HTML with hand-drawn SVG charts. Dark in every theme, in
Pixel Ag's colours (farm-app src/theme.css, dark set)."""

import html
import json
import math
import os
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd

import logo
from company_info import name, sector

# Pixel Ag dark palette
PAGE, PANEL, PANEL2, PANEL3 = "#0B0A07", "#1B1812", "#14120D", "#262219"
LINE, LINE2 = "rgba(255,255,255,.10)", "rgba(255,255,255,.06)"
T1, T3, T4, T5 = "#F4F0E6", "#C4BDAA", "#A39D8E", "#7E7866"
GOLD, GOLD_TEXT = "#C8A951", "#E2C067"
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


def day_block(day, curve=None):
    """The week so far and the latest day's plus/minus. While the market is open the latest
    day is today, at the latest prices (`day["live"]`); otherwise it's a day's close.
    A day not yet in the record is valued from the shares held; it's shown only."""
    if not day:
        return ""
    d, prev = pd.Timestamp(day["day"]), pd.Timestamp(day["prev"])
    live = bool(day.get("live"))
    if live:
        pill = f'<span class="live"><i></i>Live &middot; {clock(pd.Timestamp(day["as_of"]))} ET</span>'
        note = ("If the week ended right now: the shares you hold, at the latest prices (they can be up to 15 minutes behind). "
                "Updates every 30 minutes while markets are open. Nothing is traded or recorded until the week's last close.")
        today = "today so far"
    else:
        pill = f'<span class="live off">{d:%a %b %d} close</span>'
        note = (f"From the account record, compared with {prev:%a %b %d}." if day["recorded"] else
                f"Valued from the shares held at {d:%a %b %d}'s close, compared with {prev:%a %b %d}. "
                f"Not in the record yet: a day goes into the record after the next close, and trades happen only at the week's last close.")
        today = f"on {d:%a %b %d}"
    held = "All in cash, so the account doesn't move." if not day["invested"] else \
        f"{signed_pct(day['pct'])} &middot; now {money(day['value'], False)}"
    tiles = []
    if "week_change" in day:
        since = pd.Timestamp(day["since"])
        tiles.append(f'<div class="kpi hero"><span class="k">Your account, this week so far (since {since:%a %b %d}\'s close)</span>'
                     f'<span class="v {tone(day["week_change"])}">{signed_money(day["week_change"])}</span>'
                     f'<span class="s">{signed_pct(day["week_pct"])} &middot; same money in {LABEL} {signed_pct(day["week_bench_pct"])}</span></div>')
    tiles += [
        f'<div class="kpi"><span class="k">Your account, {today}</span><span class="v {tone(day["change"])}">{signed_money(day["change"])}</span>'
        f'<span class="s">{held}</span></div>',
        f'<div class="kpi"><span class="k">Same money in {LABEL}, {today}</span><span class="v {tone(day["bench_change"])}">{signed_money(day["bench_change"])}</span>'
        f'<span class="s">{signed_pct(day["bench_pct"])}</span></div>',
    ]
    movers = ""
    if day["movers"]:
        movers = (f'<p class="lead" style="margin:0">Each stock you hold, {today}</p><div class="movers">'
                  + "".join(f'<span class="mv" title="{esc(name(t))}"><b>{esc(t)}</b>{chip(x)}</span>' for t, x in day["movers"])
                  + "</div>")
    title = "This week so far" if not day["recorded"] else "This week"
    return (f'<section class="card sec full"><div><h2>{title} {pill}</h2><p class="lead">{note}</p></div>'
            f'<div class="kpis daykpis">{"".join(tiles)}</div>{week_chart(curve)}{movers}</section>')


def week_chart(curve):
    """This week every 30 minutes, as % since the previous week's close; drag to read."""
    if not curve or len(curve) < 2:
        return ""
    data = json.dumps({"pts": curve, "sym": SYM, "bench": LABEL})
    return (f'<div class="wkc"><div class="wk-read" aria-live="polite"></div><div class="wk-plot"></div>'
            f'<div class="key"><span><i style="background:{GOLD_TEXT}"></i>Your account</span>'
            f'<span><i style="background:{T4}"></i>Same money in {LABEL}</span></div>'
            f'<script type="application/json" class="wk-data">{data}</script>{WK_SCRIPT}</div>')


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
function hint(){ box.querySelector('.wk-read').innerHTML='<span class="rs">Drag along the chart to see any half hour this week.</span>'; }
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
  var g=el('linearGradient',{id:'wkglow',x1:0,x2:0,y1:0,y2:1},el('defs',{},svg));
  el('stop',{offset:0,'stop-color':GOLD,'stop-opacity':.22},g); el('stop',{offset:1,'stop-color':GOLD,'stop-opacity':0},g);
  for(var v=Math.ceil(lo/st)*st; v<=hi+1e-9; v+=st){ var z=Math.abs(v)<1e-9;
    el('line',{x1:pl,x2:w-pr,y1:y(v),y2:y(v),stroke:z?ZERO:GRID,'stroke-dasharray':z?'3 3':'none'},svg);
    el('text',{x:w-pr+6,y:y(v)+4,'font-size':11,fill:T4},svg).textContent=(z?'0':sign(v,0)+(+Math.abs(v).toFixed(2)))+'%'; }
  var cur=Math.floor((P[n-1][0]-1)/DAY);
  ['Mon','Tue','Wed','Thu','Fri'].forEach(function(name,k){
    if(k) el('line',{x1:x(k*DAY),x2:x(k*DAY),y1:pt,y2:h-pb_,stroke:GRID},svg);
    el('text',{x:x(k*DAY+DAY/2),y:h-6,'font-size':11,'text-anchor':'middle',fill:k>cur?T5:T4,'font-weight':k===cur?700:400},svg).textContent=name; });
  function path(f){return P.map(function(r,i){return (i?'L':'M')+x(r[0]).toFixed(1)+','+y(f(r)).toFixed(1);}).join('');}
  el('path',{d:path(pa)+'L'+x(P[n-1][0]).toFixed(1)+','+y(lo)+'L'+x(0)+','+y(lo)+'Z',fill:'url(#wkglow)'},svg);
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


def chip(x):
    if x is None or pd.isna(x):
        return ""
    cls = "pos" if x > 0.05 else "neg" if x < -0.05 else "flat"
    sign = "+" if x > 0.05 else "&minus;" if x < -0.05 else ""
    return f'<span class="chip {cls}">{sign}{abs(x):.1f}%</span>'


# --------------------------------------------------------------------------
# Small graphics
# --------------------------------------------------------------------------
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
                   for v in [lo + (hi - lo) * k / 3 for k in range(4)])
    idx = sorted(set([0, n - 1] + list(range(0, n, max(1, n // 5)))))
    xl = "".join(f'<text x="{xs[i]:.1f}" y="{h - 8}" text-anchor="{"start" if i == 0 else "end" if i == n - 1 else "middle"}" '
                 f'class="ax">{a.index[i]:%b %d}</text>' for i in idx)
    area = f"{pts(a)} {xs[-1]:.1f},{h - pb} {xs[0]:.1f},{h - pb}"
    dots = "".join(f'<circle cx="{xs[i]:.1f}" cy="{y(a.iloc[i]):.1f}" r="8" class="hot"><title>{a.index[i]:%a %b %d}'
                   f'\nYour account: {money(a.iloc[i])}\n{LABEL}: {money(q.iloc[i])}</title></circle>' for i in range(n))
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Your account against the same money in {LABEL}, '
            f'{money(a.iloc[-1])} vs {money(q.iloc[-1])}">'
            f'<defs><linearGradient id="af" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="{GOLD_TEXT}" stop-opacity=".22"/>'
            f'<stop offset="1" stop-color="{GOLD_TEXT}" stop-opacity="0"/></linearGradient></defs>{grid}{xl}'
            f'<polygon points="{area}" fill="url(#af)"/>'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(q)}" fill="none" stroke="{T4}" stroke-width="1.5" stroke-dasharray="4 3"/>'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(a)}" fill="none" stroke="{GOLD_TEXT}" stroke-width="2" stroke-linejoin="round"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{y(a.iloc[-1]):.1f}" r="4.5" fill="{GOLD_TEXT}"/>{dots}</svg>')


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
                   for v in [lo + (hi - lo) * k / 3 for k in range(4)])
    months, prev = [], None
    for i, d in enumerate(sw.index):
        if d.month != prev and i < n - 12:
            months.append(f'<text x="{xs[i]:.1f}" y="{h - 8}" class="ax">{d:%b}</text>')
        prev = d.month
    last_q, last_s = sw["qqq"].iloc[-1], sw["sma"].iloc[-1]
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{LABEL} {last_q:.2f} against its '
            f'200-day average {last_s:.2f}; shaded where it closed below the line">{grid}{"".join(shade)}{"".join(months)}'
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(sw["sma"])}" fill="none" stroke="{GOLD}" stroke-width="1.5" stroke-dasharray="5 3"/>'
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
            f'<polyline vector-effect="non-scaling-stroke" points="{pts(1)}" fill="none" stroke="{GOLD_TEXT}" stroke-width="1.5" stroke-linejoin="round"/></svg>')


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
def page_shell(title, rel, markets, current, header, main):
    """rel = path back to the site root ('' for Home, '../' for a market page)."""
    def link(m):
        s = summary_state(m)
        here = ' class="on" aria-current="page"' if m["slug"] == current else ""
        return (f'<a href="{rel}{m["slug"]}/"{here}>{esc(m["name"])} '
                f'<small class="{s[1]}">{s[0]}</small></a>')
    side = "".join(link(m) for m in markets)
    chips = "" if current is None else "".join(f'<a href="{rel}{m["slug"]}/" class="{"on" if m["slug"] == current else ""}">{esc(m["name"])}</a>'
                    for m in markets)
    updated = datetime.now(ZoneInfo("America/New_York"))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#1C1A14">
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
/* Control Room: Pixel Ag's dark palette in every theme. Phone: one column, back link + market buttons.
   Computer: market sidebar on the left, page in two columns on the right. */
:root{{color-scheme:dark;--sans:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
*{{box-sizing:border-box}}
html{{background:{PAGE}}}
body{{margin:0;background:{PAGE};color:{T1};font-family:var(--sans);font-size:15px;line-height:1.45;-webkit-font-smoothing:antialiased}}
a{{color:{GOLD_TEXT}}}
a:focus-visible,summary:focus-visible{{outline:2px solid {GOLD};outline-offset:3px;border-radius:6px}}
.side{{display:none}}
.hdr{{background:#1C1A14;border-bottom:4px solid {GOLD};padding-top:max(14px,env(safe-area-inset-top))}}
.wrap{{max-width:1080px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left));padding-right:max(16px,env(safe-area-inset-right))}}
.hin{{display:flex;flex-direction:column;gap:12px;padding-bottom:16px}}
.back{{font-size:13px;font-weight:600;text-decoration:none;display:inline-flex;align-items:center;min-height:32px;align-self:flex-start}}
.brandrow{{display:flex;align-items:center;gap:12px}}
.brandrow svg{{width:44px;height:44px;flex:none}}
.brand{{font-size:13px;font-weight:800;color:#fff}}
.htitle{{margin:0;font-size:21px;font-weight:700;letter-spacing:-.03em;color:{GOLD};line-height:1.2}}
.hval{{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}}
.big{{font-size:34px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1.1}}
.sub{{font-size:13px;color:rgba(244,240,230,.72)}}
.state{{font-size:13px;font-weight:700;padding:6px 12px;border-radius:999px;white-space:nowrap}}
.state.on,small.on{{color:#8BE07C}} .state.on{{background:rgba(107,211,90,.16)}}
.state.off,small.off{{color:#FF9A90}} .state.off{{background:rgba(255,90,78,.18)}}
.state.wait,small.wait{{color:{GOLD_TEXT}}} .state.wait{{background:rgba(226,192,103,.14)}}
.switch{{display:flex;gap:6px;overflow-x:auto;background:{PANEL2};border-bottom:1px solid {LINE};padding-block:10px}}
.switch a{{flex:none;font-size:13px;font-weight:700;text-decoration:none;color:{T3};border:1px solid {LINE};border-radius:999px;padding:8px 14px;min-height:36px;display:inline-flex;align-items:center}}
.switch a.on{{background:{GOLD};color:#1C1A14;border-color:{GOLD}}}
@media(min-width:900px){{
 .app{{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:100vh}}
 .side{{display:flex;flex-direction:column;gap:4px;background:#1C1A14;border-right:1px solid {LINE};padding:18px 12px;position:sticky;top:0;height:100vh;overflow-y:auto}}
 .side .brandrow{{padding:0 6px 14px;border-bottom:4px solid {GOLD};margin-bottom:10px}}
 .side a{{display:flex;justify-content:space-between;align-items:center;gap:8px;text-decoration:none;color:{T3};font-size:14px;font-weight:600;padding:10px;border-radius:10px;min-height:44px}}
 .side a small{{font-size:12px;font-weight:600}}
 .side a.on{{background:{PANEL};color:{T1};box-shadow:inset 3px 0 0 {GOLD}}}
 .side a:hover{{background:{PANEL}}}
 .switch,.hdr .back,.hdr .brandrow svg,.hdr .brand{{display:none}}
}}
main.wrap{{padding-top:18px;padding-bottom:40px;display:grid;gap:16px;grid-template-columns:minmax(0,1fr)}}
@media(min-width:900px){{main.wrap{{grid-template-columns:repeat(2,minmax(0,1fr))}} .full{{grid-column:1/-1}}}}
.col{{display:contents}}
.o1{{order:1}} .o2{{order:2}} .o3{{order:3}} .o4{{order:4}} .o5{{order:5}} .o6{{order:6}} .o6 > *{{order:6}}
@media(min-width:900px){{.col{{display:flex;flex-direction:column;gap:16px;min-width:0}} .col > *{{order:0}}
 .full .rowlist{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:28px}}
 .full .rl:nth-last-child(2):nth-child(odd){{border-bottom:0}}}}
h2{{margin:0;font-size:18px;font-weight:700;letter-spacing:-.02em;text-wrap:balance}}
.lead{{margin:4px 0 0;font-size:13px;color:{T3}}}
.card{{background:{PANEL};border:1px solid {LINE};border-radius:16px;padding:14px}}
.sec{{display:flex;flex-direction:column;gap:12px}}
.kpis{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
@media(min-width:900px){{.kpis{{grid-template-columns:repeat(4,minmax(0,1fr))}}}}
.kpi{{background:{PANEL};border:1px solid {LINE};border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:2px;min-width:0}}
.kpi .k{{font-size:12px;color:{T4}}} .kpi .v{{font-size:20px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}}
.kpi .s{{font-size:12px;color:{T3}}}
.pos{{color:{GREEN}}} .neg{{color:{RED}}} .dim{{color:{T5}}} .amb{{color:{AMBER}}}
.chip{{font-weight:700;font-size:13px;padding:3px 9px;border-radius:8px;white-space:nowrap;font-variant-numeric:tabular-nums}}
.chip.pos{{background:rgba(107,211,90,.14);color:{GREEN}}} .chip.neg{{background:rgba(255,90,78,.14);color:{RED}}} .chip.flat{{background:{PANEL3};color:{T3}}}
.tag{{font-size:11px;font-weight:800;padding:2px 7px;border-radius:6px;vertical-align:1px}}
.tag.buy{{background:rgba(107,211,90,.14);color:{GREEN}}} .tag.sell{{background:rgba(255,90,78,.14);color:{RED}}}
.chart{{width:100%;height:auto;display:block;shape-rendering:geometricPrecision}} .chart .ax{{fill:{T4};font-size:11px;font-family:var(--sans)}}
.chart .hot{{fill:transparent;cursor:crosshair}} .chart .hot:hover{{fill:{GOLD_TEXT};fill-opacity:.3}}
.key{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:{T3}}}
.key i{{display:inline-block;width:16px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}}
.rowlist{{display:flex;flex-direction:column}}
.rl{{display:flex;flex-direction:column;gap:8px;padding:12px 0;border-bottom:1px solid {LINE2}}} .rl:last-child{{border-bottom:0;padding-bottom:2px}}
.rltop{{display:flex;justify-content:space-between;align-items:center;gap:10px}}
.nm{{display:flex;flex-direction:column;min-width:0}} .nm small{{font-size:12px;color:{T4};white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.rlsub{{display:flex;flex-wrap:wrap;gap:2px 10px;font-size:12px;color:{T4};font-variant-numeric:tabular-nums}}
.stt{{font-weight:600;color:{T3}}} .stt.ok{{color:{GREEN}}} .stt.keep{{color:{AMBER}}} .stt.sell{{color:{RED}}}
.subh{{margin:6px 0 0;font-size:15px;font-weight:700;color:{T1}}}
.stack{{display:flex;height:20px;border-radius:8px;overflow:hidden}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;color:{T3}}} .legend i{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}}
.stats{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
.stat{{background:{PANEL3};border-radius:10px;padding:10px;display:flex;flex-direction:column;gap:2px}}
.stat .k{{font-size:12px;color:{T4}}} .stat .v{{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums}} .stat small{{color:{T4};font-weight:500}}
.warn{{margin:0;font-size:12px;line-height:1.5;color:{GOLD_TEXT};background:rgba(226,192,103,.10);border-radius:10px;padding:10px}}
.rules{{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:10px;font-size:14px}} .rules b{{display:block;font-size:13px;color:{GOLD_TEXT}}}
.empty{{margin:0;color:{T3};font-size:14px}}
details.tuck summary{{cursor:pointer;font-weight:700;font-size:15px;list-style:none;display:flex;justify-content:space-between;align-items:center;min-height:28px}}
details.tuck summary::-webkit-details-marker{{display:none}}
details.tuck summary::after{{content:"+";font-size:20px;color:{GOLD_TEXT};font-weight:500}}
details.tuck[open] summary::after{{content:"\\2212"}}
.scroll{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}}
th{{text-align:left;color:{T4};font-weight:600;font-size:12px;padding:8px;border-bottom:1px solid {LINE}}}
td{{padding:9px 8px;border-bottom:1px solid {LINE2};white-space:nowrap;font-variant-numeric:tabular-nums}}
.note{{font-size:12px;color:{T5};line-height:1.6;margin:4px 0 0}}
.bt-read,.wk-read{{display:flex;flex-direction:column;gap:2px;min-height:56px}}
.bt-read .rv,.wk-read .rv{{font-size:26px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1.1}}
.bt-read .rs,.wk-read .rs{{font-size:13px;color:{T3};font-variant-numeric:tabular-nums}}
.pill{{font-weight:700;font-size:12px;padding:2px 8px;border-radius:7px;margin-left:6px;white-space:nowrap}}
.pill.pos{{background:rgba(107,211,90,.14);color:{GREEN}}} .pill.neg{{background:rgba(255,90,78,.14);color:{RED}}}
.bt-plot{{touch-action:pan-y;user-select:none;-webkit-user-select:none}}
.bt-plot svg{{display:block;width:100%;height:auto;overflow:visible}} .bt-plot text{{font-family:var(--sans);font-variant-numeric:tabular-nums}}
.bt-chips{{display:flex;gap:6px}}
.bt-chips button{{font:inherit;font-size:13px;font-weight:700;color:{T3};background:transparent;border:1px solid {LINE};border-radius:999px;padding:7px 14px;min-height:36px;cursor:pointer}}
.bt-chips button[aria-pressed="true"]{{background:{GOLD};color:#1C1A14;border-color:{GOLD}}}
.bt-chips button:focus-visible{{outline:2px solid {GOLD};outline-offset:2px}}
/* Home */
.mcards{{display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}}
@media(min-width:900px){{.mcards{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.mcard{{display:flex;flex-direction:column;gap:10px;text-decoration:none;color:inherit;background:{PANEL};border:1px solid {LINE};border-radius:16px;padding:16px}}
.mcard:hover{{border-color:rgba(226,192,103,.4)}}
.mtop,.mrow{{display:flex;justify-content:space-between;gap:10px}} .mtop{{align-items:flex-start}} .mrow{{align-items:flex-end}}
.mname{{font-size:18px;font-weight:700;letter-spacing:-.02em}}
.msub{{font-size:12px;color:{T4}}}
.mval{{font-size:26px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums}}
.mval small{{font-size:13px;font-weight:600;color:{T4};margin-left:4px}}
.mcard svg{{width:120px;height:40px;flex:none}}
.wk{{font-size:13px;color:{T3};border-top:1px solid {LINE2};padding-top:10px;display:flex;justify-content:space-between;gap:10px}}
.wk b{{color:{GOLD_TEXT};white-space:nowrap}}
.tot{{border-color:rgba(200,169,81,.35)}}
.tval{{font-size:34px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1.15}}
.mday{{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 10px;font-size:13px;color:{T3}}}
.mday b{{font-variant-numeric:tabular-nums}}
/* The day's plus/minus */
.kpis.daykpis{{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}}
.daykpis .kpi{{background:{PANEL2}}}
.movers{{display:flex;flex-wrap:wrap;gap:6px}}
.mv{{display:inline-flex;align-items:center;gap:6px;font-size:13px;background:{PANEL2};border:1px solid {LINE2};border-radius:10px;padding:3px 4px 3px 9px}}
.mv .chip{{font-size:12px;padding:2px 7px}}
.daykpis .kpi.hero{{grid-column:1/-1}} .kpi.hero .v{{font-size:28px}}
.live{{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:#8BE07C;background:rgba(107,211,90,.14);border-radius:999px;padding:3px 10px;margin-left:6px;vertical-align:3px;white-space:nowrap;letter-spacing:0}}
.live i{{width:7px;height:7px;border-radius:50%;background:{GREEN};animation:blink 2s ease-in-out infinite}}
.live.off{{color:{T3};background:{PANEL3}}}
.mday .live{{margin-left:0;vertical-align:0}}
@keyframes blink{{50%{{opacity:.3}}}}
@media(prefers-reduced-motion:reduce){{.live i{{animation:none}}}}
.wkc{{display:flex;flex-direction:column;gap:8px}}
.wk-read{{min-height:0}} .wk-read .rv{{font-size:20px}}
.wk-plot{{touch-action:pan-y;user-select:none;-webkit-user-select:none}}
.wk-plot svg{{display:block;width:100%;height:auto;overflow:visible}} .wk-plot text{{font-family:var(--sans);font-variant-numeric:tabular-nums}}
</style></head><body><div class="app">
<nav class="side" aria-label="Markets"><div class="brandrow">{logo.svg()}<div class="brand" style="display:block">Pixel Invest</div></div>
<a href="{rel or './'}" class="{"on" if current is None else ""}">All markets</a>{side}</nav>
<div class="content">
{header}
{f'<nav class="switch wrap" aria-label="Markets">{chips}</nav>' if chips else ""}
{main}
</div></div>
<script>(function(){{var t=Date.now();document.addEventListener('visibilitychange',function(){{
if(document.visibilityState==='visible'&&Date.now()-t>5*60*1000)location.reload();}});}})();</script>
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
    return (f'<p class="note">Pretend money only: trades at each Friday\'s closing price, no fees. {divs} '
            f'Prices in {cfg["currency"]}. Trades every weekend; the day\'s and week\'s plus/minus update every 30 minutes while markets are open. Last updated {updated:%b %d, %Y %I:%M %p} '
            f'New York time. Past and paper results don\'t guarantee future returns.</p>')


def backtest_block(cfg, bt, open_=True):   # always shown
    if not bt:
        return ""
    r, q = bt["rules"], bt["qqq"]
    data = json.dumps({"curve": [[d, round(a, 2), round(b, 2)] for d, a, b in bt["curve"]],
                       "sym": SYM, "bench": LABEL})
    return f'''<section class="card sec bt"><h2>How these rules did in testing</h2>
<div class="sec">
<p class="lead">{money(10_000, False)} run through the same rules every week since {bt["start"][:4]}, with {bt["cost_pct"] * 100:.1f}% trading costs
and dividends included. Drag along the chart to read any date.</p>
<div class="bt-read" aria-live="polite"></div>
<div class="bt-plot">{backtest_chart(bt)}</div>
<div class="bt-chips" role="group" aria-label="Time range">
<button type="button" data-y="1" aria-pressed="false">1Y</button><button type="button" data-y="3" aria-pressed="false">3Y</button>
<button type="button" data-y="5" aria-pressed="false">5Y</button><button type="button" data-y="0" aria-pressed="true">All</button></div>
<div class="key"><span><i style="background:{GOLD_TEXT}"></i>The rules</span><span><i style="background:{T4}"></i>Just holding {LABEL}</span></div>
<p class="lead bt-note" hidden>Both lines restart at {money(10_000, False)} at the start of the range.</p>
<div class="stats">
<div class="stat"><span class="k">Yearly return</span><span class="v">{r["cagr"] * 100:.1f}% <small>vs {q["cagr"] * 100:.1f}%</small></span></div>
<div class="stat"><span class="k">Worst drop</span><span class="v">&minus;{abs(r["drop"]) * 100:.1f}% <small>vs &minus;{abs(q["drop"]) * 100:.1f}%</small></span></div>
<div class="stat"><span class="k">Winning trades</span><span class="v">{(bt["win_rate"] or 0) * 100:.0f}%</span></div>
<div class="stat"><span class="k">Trades a year</span><span class="v">{bt["trades_per_year"]:.0f}</span></div></div>
<p class="warn">This test uses today's {esc(cfg["name"])} list, so it includes later winners and leaves out companies that dropped out of the index.
Real results would very likely have been lower. Refreshed {pd.Timestamp(bt["computed_on"]):%b %d, %Y}.</p></div>
<script type="application/json" class="bt-data">{data}</script>{BT_SCRIPT}</section>'''


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
  var g=el('linearGradient',{id:'btglow',x1:0,x2:0,y1:0,y2:1},el('defs',{},svg));
  el('stop',{offset:0,'stop-color':GOLD,'stop-opacity':.28},g); el('stop',{offset:1,'stop-color':GOLD,'stop-opacity':0},g);
  ticks(lo,hi).forEach(function(v){ el('line',{x1:pl,x2:w-pr,y1:y(v),y2:y(v),stroke:GRID},svg);
    el('text',{x:w-pr+6,y:y(v)+4,'font-size':11,fill:T4},svg).textContent=short(v); });
  var seen={}, yrs=[]; data.forEach(function(r,i){var k=r[0].slice(0,4); if(!seen[k]){seen[k]=1;yrs.push([k,i]);}});
  var step=Math.ceil(yrs.length/5); yrs.forEach(function(p,k){ if(k%step||x(p[1])>w-pr-26) return;
    el('text',{x:x(p[1]),y:h-6,'font-size':11,fill:T5},svg).textContent = n<70 ? day(data[p[1]][0]).toLocaleDateString('en-US',{month:'short'})+" '"+p[0].slice(2) : p[0]; });
  function path(k){return data.map(function(r,i){return (i?'L':'M')+x(i).toFixed(1)+','+y(r[k]).toFixed(1);}).join('');}
  el('path',{d:path(1)+'L'+x(n-1)+','+(h-pb)+'L'+x(0)+','+(h-pb)+'Z',fill:'url(#btglow)'},svg);
  el('path',{d:path(2),fill:'none',stroke:GREY,'stroke-width':1.25,'stroke-opacity':.85,'stroke-linejoin':'round'},svg);
  el('path',{d:path(1),fill:'none',stroke:GOLD,'stroke-width':1.75,'stroke-linejoin':'round'},svg);
  var v=el('line',{y1:pt,y2:h-pb,stroke:'rgba(244,240,230,.35)',visibility:'hidden'},svg),
      d1=el('circle',{r:4.5,fill:GOLD,stroke:'#1B1812','stroke-width':2,visibility:'hidden'},svg),
      d2=el('circle',{r:3.5,fill:GREY,stroke:'#1B1812','stroke-width':2,visibility:'hidden'},svg);
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
draw();
})();
</script>"""


def rules_block(cfg, cls="o5"):
    return f'''<section class="card sec {cls}"><h2>The rules</h2><ul class="rules">
<li><b>Every Friday</b>Rank the {esc(cfg["name"])} by 3-month gain.</li>
<li><b>Buy</b>Own the top {cfg["top_n"]}, with equal money in each new stock.</li>
<li><b>Sell</b>When a stock falls below rank {cfg["exit_rank"]}.</li>
<li><b>Safety switch</b>When {LABEL} closes below its {cfg["sma_days"]}-day average, sell everything and wait in cash.</li></ul></section>'''


def set_market(cfg):
    global SYM, LABEL
    SYM, LABEL = cfg.get("symbol", "$"), cfg.get("bench_label", "QQQ")


# --------------------------------------------------------------------------
# A market's page
# --------------------------------------------------------------------------
def build_dashboard(cfg, acct, trades, holdings, status, watch, last_rebal, prices, bt, markets, ledger=None, day_info=None,
                    curve=None):
    set_market(cfg)
    start_val = cfg["capital"]
    now_val = acct["paper_account"].iloc[-1]
    qqq_val = acct["qqq_same_money"].iloc[-1]
    acct_ret = (now_val / start_val - 1) * 100
    qqq_ret = (qqq_val / start_val - 1) * 100
    diff = now_val - qqq_val
    top_n, exit_rank = cfg["top_n"], cfg["exit_rank"]
    start = pd.Timestamp(cfg["start_date"])
    held = {h["ticker"] for h in holdings}
    on = status["risk_on"]
    invested = bool(holdings)          # what the account holds now (changes only at a week's last close)
    gap = (status["qqq"] / status["sma"] - 1) * 100
    # The switch is read every day but acted on only at the week's last close, so mid-week it
    # can point the other way from what's held: a heads-up for Friday, not a trade.
    if on == invested:
        sw_val = f'<span class="v {"pos" if on else "neg"}">{"Invested" if on else "Cash"}</span>'
        sw_sub = f'{LABEL} {abs(gap):.1f}% {"above" if gap >= 0 else "under"} the line'
    elif invested:
        sw_val = '<span class="v amb">Sell signal</span>'
        sw_sub = f"{LABEL} {abs(gap):.1f}% under the line. If it's still under at Friday's close, everything is sold."
    else:
        sw_val = '<span class="v amb">Buy signal</span>'
        sw_sub = f"{LABEL} {abs(gap):.1f}% above the line. If it's still above at Friday's close, the top {top_n} are bought."

    closed = trades[trades["action"] == "SELL"]
    wins = int((closed["gain_pct"] > 0).sum())
    record = f"{wins} of {len(closed)} sells gained" if len(closed) else "No sells yet"
    if ledger and ledger.get("dividends_received"):
        record += f" &middot; {money(ledger['dividends_received'], False)} dividends"

    def updown(r):
        return f"{'Up' if r >= 0 else 'Down'} {abs(r):.1f}%"

    # Your stocks: one list of what's held, plus anything sold at the last close
    week = trades[trades["date"] == last_rebal.date()]
    started = last_rebal.normalize() == start.normalize()
    buys, sells = week[week["action"] == "BUY"], week[week["action"] == "SELL"]
    when = f"{last_rebal:%a %b %d}"

    def status_of(rank):
        if rank is None:
            return "", "Keeping"
        if rank <= top_n:
            return "ok", f"Keeping &middot; ranked {rank}"
        if rank <= exit_rank:
            return "keep", f"Keeping for now &middot; ranked {rank}, sold if it drops below {exit_rank}"
        return "sell", f"Sells at Friday's close &middot; ranked {rank}"

    def row(ticker, sub, right, line=""):
        return (f'<div class="rl"><div class="rltop"><div class="nm"><b>{esc(ticker)}</b><small>{sub}</small></div>{right}</div>'
                f'{line}</div>')

    rank_note = (f"Each Friday the rules rank every {esc(cfg['name'])} stock by its 3-month gain. "
                 f"A stock is kept while it ranks in the top {exit_rank}.")
    parts = []
    if holdings:
        title = f"Your {len(holdings)} stock{'s' if len(holdings) != 1 else ''}"
        if started:
            lead = f"Bought at the {when} close, when the account started, with equal money in each. {rank_note}"
        elif week.empty:
            lead = f"No trades at the {when} close: every stock still ranked high enough to keep. {rank_note}"
        else:
            did = " and ".join(x for x in [f"bought {len(buys)}" if len(buys) else "", f"sold {len(sells)}" if len(sells) else ""] if x)
            lead = f"At the {when} close the rules {did}. {rank_note}"
        if not on:
            parts.append(f'<p class="warn">If {LABEL} is still under its 200-day average at Friday\'s close, '
                         f'all of these are sold (the safety switch).</p>')
        hrows = []
        for h in holdings:
            cls, st = status_of(h["rank"])
            new = ' <span class="tag buy">New</span>' if (not started and h["buy_date"] == str(last_rebal.date())) else ""
            line = (f'<div class="rlsub"><span class="stt {cls}">{st}</span>'
                    f'<span>Bought {pd.Timestamp(h["buy_date"]):%b %d} at {money(h["buy_price"])} &middot; now {money(h["price"])}</span></div>')
            hrows.append(row(h["ticker"], f'{esc(name(h["ticker"]))} &middot; {money(h["value"])}', chip(h["gain_pct"]), line)
                         .replace(f'<b>{esc(h["ticker"])}</b>', f'<b>{esc(h["ticker"])}{new}</b>', 1))
        parts.append(f'<div class="rowlist">{"".join(hrows)}</div>')
    else:
        title = "Your stocks"
        lead = f"None right now: the account is in cash because {LABEL} is under its 200-day average (the safety switch)."
    if len(sells):
        srows = "".join(row(t["ticker"], esc(name(t["ticker"])), chip(t["gain_pct"]),
                            f'<div class="rlsub"><span>Sold {pd.Timestamp(t["date"]):%b %d} at {money(t["price"])}</span></div>')
                        for t in sells.to_dict("records"))
        parts.append(f'<h3 class="subh">Sold at the {when} close</h3><div class="rowlist">{srows}</div>')
    if holdings:
        parts.append(f'<h3 class="subh">Where your money is</h3>{sector_bar(holdings, status["cash"])}')
    else:
        nxt = [t for t in watch.index if t not in held][:top_n]
        nrows = "".join(row(t, f'{esc(name(t))} &middot; 3-month gain', f'<span class="chip pos">+{watch[t] * 100:.0f}%</span>')
                        for t in nxt)
        parts.append(f'<h3 class="subh">Next buys if the switch turns back on</h3>'
                     f'<p class="lead" style="margin:0">If {LABEL} closes above {money(status["sma"])} on a Friday, '
                     f'today\'s top {top_n} by 3-month gain are bought at that close.</p><div class="rowlist">{nrows}</div>')
    stocks_html = (f'<section class="card sec full"><div><h2>{title}</h2><p class="lead">{lead}</p></div>'
                   f'{"".join(parts)}</section>')

    # Charts
    b = cfg["benchmark"]
    sw = pd.DataFrame({"qqq": prices[b], "sma": prices[b].rolling(cfg["sma_days"]).mean()}
                      ).dropna().loc[:status["last_day"]].iloc[-130:]
    if on == invested:
        switch_lead = (f"{LABEL} is above the dashed line, so the rules stay invested." if on else
                       f"{LABEL} is below the dashed line, so the rules sit in cash. Shaded days are below the line.")
    elif invested:
        switch_lead = (f"{LABEL} has dipped below the dashed line. The rules act on it only at the week's last close: "
                       f"if it's still below at Friday's close, they sell everything and sit in cash. Until then the stocks are kept. "
                       f"Shaded days are below the line.")
    else:
        switch_lead = (f"{LABEL} is back above the dashed line. The rules act on it only at the week's last close: "
                       f"if it's still above at Friday's close, they buy back in. Shaded days are below the line.")
    acct_html = (account_chart(acct) if len(acct) > 1 else
                 '<p class="empty">The chart starts after the first full day.</p>')

    hist = "".join(f'''<tr><td>{pd.Timestamp(t["date"]):%b %d, %Y}</td>
<td><span class="tag {'buy' if t["action"] == "BUY" else 'sell'}">{t["action"].title()}</span></td>
<td><b>{esc(t["ticker"])}</b></td><td>{money(t["price"])}</td><td>{money(t["value"], False)}</td><td>{chip(t["gain_pct"])}</td></tr>'''
                   for t in trades.iloc[::-1].to_dict("records"))

    header = f'''<header class="hdr"><div class="wrap hin">
<a class="back" href="../">&#8249; All markets</a>
<div class="brandrow">{logo.svg()}<div><div class="brand">Pixel Invest</div><h1 class="htitle">{esc(cfg["name"])} Momentum</h1></div></div>
<div class="hval"><div><div class="big">{money(now_val)}</div>
<div class="sub">{updown(acct_ret)} since {start:%b %d} &middot; {LABEL} {updown(qqq_ret).lower()} &middot; prices as of {status["last_day"]:%a %b %d}</div></div>
<span class="state {'on' if invested else 'off'}">{'Invested' if invested else 'In cash'}</span></div>
</div></header>'''
    main = f'''<main class="wrap">
{day_block(day_info, curve)}
<div class="kpis full">
 <div class="kpi"><span class="k">Account</span><span class="v">{money(now_val, False)}</span><span class="s {'pos' if acct_ret >= 0 else 'neg'}">{'+' if acct_ret >= 0 else '&minus;'}{abs(acct_ret):.1f}% since {start:%b %d}</span></div>
 <div class="kpi"><span class="k">Same money in {LABEL}</span><span class="v">{money(qqq_val, False)}</span><span class="s">You're {money(abs(diff), False)} {'ahead' if diff >= 0 else 'behind'}</span></div>
 <div class="kpi"><span class="k">Safety switch</span>{sw_val}<span class="s">{sw_sub}</span></div>
 <div class="kpi"><span class="k">Stocks held</span><span class="v">{len(holdings)} of {top_n}</span><span class="s">{record}</span></div>
</div>
{stocks_html}

<div class="col">
<section class="card sec o1"><div><h2>Your account vs. just holding {LABEL}</h2><p class="lead">Started {start:%b %d} with {money(start_val, False)}. Tap a point for that day.</p></div>
<div class="key"><span><i style="background:{GOLD_TEXT}"></i>Your account</span><span><i style="background:{T4}"></i>Same money in {LABEL}</span></div>
{acct_html}</section>
{rules_block(cfg)}
</div>
<div class="col">
<section class="card sec o2"><div><h2>Safety switch: {LABEL} vs. its 200-day average</h2><p class="lead">{switch_lead}
Today {LABEL} is {status["qqq"]:.2f} and the line is {status["sma"]:.2f}.</p></div>
<div class="key"><span><i style="background:{T1}"></i>{LABEL}</span><span><i style="background:{GOLD}"></i>200-day average</span></div>
{switch_chart(sw)}</section>
</div>

<div class="col full o6">{backtest_block(cfg, bt)}
<details class="card tuck"><summary>Every trade ({len(trades)})</summary><div class="scroll" style="margin-top:10px"><table>
<tr><th>Date</th><th>Action</th><th>Stock</th><th>Price</th><th>Amount</th><th>Result</th></tr>{hist}</table></div></details>
{updated_note(cfg)}</div>
</main>'''
    _SUMMARIES[cfg["slug"]] = {**(_SUMMARIES.get(cfg["slug"]) or {}), "risk_on": on, "held": len(holdings)}
    page = page_shell(f"{esc(cfg['name'])} &middot; Pixel Invest", "../", markets, cfg["slug"], header, main)
    return page, now_val, qqq_val, week


def build_waiting(cfg, bt, markets):
    """Page for a market that hasn't started trading: its rules and backtest."""
    set_market(cfg)
    if cfg.get("active", True) and cfg.get("start_date"):
        d = pd.Timestamp(cfg["start_date"])
        starts = f"Starts {d:%b %d}"
        when = (f"The account makes its first trades at the close on {d:%A, %B %d}: the top {cfg['top_n']} by "
                f"3-month gain, unless {LABEL} is below its 200-day average. This page fills in after that run.")
    else:
        starts = "Not started"
        when = "This market starts once its portfolio size is picked from the backtest below."
    header = f'''<header class="hdr"><div class="wrap hin">
<a class="back" href="../">&#8249; All markets</a>
<div class="brandrow">{logo.svg()}<div><div class="brand">Pixel Invest</div><h1 class="htitle">{esc(cfg["name"])} Momentum</h1></div></div>
<div class="hval"><div><div class="big">{money(cfg["capital"])}</div>
<div class="sub">A {cfg["currency"]} paper account, compared with {LABEL}</div></div><span class="state wait">{starts}</span></div>
</div></header>'''
    main = f'''<main class="wrap">
<section class="card sec full"><h2>Not trading yet</h2><p class="lead">{when}</p></section>
<div class="col full">{backtest_block(cfg, bt, open_=True)}</div>
{rules_block(cfg, "full")}
<div class="col full">{updated_note(cfg)}</div>
</main>'''
    return page_shell(f"{esc(cfg['name'])} &middot; Pixel Invest", "../", markets, cfg["slug"], header, main)


# --------------------------------------------------------------------------
# Home: one card per market
# --------------------------------------------------------------------------
def mini_spark(points, w=120, h=40):
    if len(points) < 2:
        return f'<svg viewBox="0 0 {w} {h}" aria-hidden="true"><line x1="0" x2="{w}" y1="{h / 2}" y2="{h / 2}" stroke="{T5}" stroke-dasharray="3 3"/></svg>'
    vals = [v for p in points for v in p]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    xs = [2 + i / (len(points) - 1) * (w - 4) for i in range(len(points))]
    y = lambda v: 3 + (1 - (v - lo) / span) * (h - 6)
    a = " ".join(f"{xs[i]:.1f},{y(p[0]):.1f}" for i, p in enumerate(points))
    q = " ".join(f"{xs[i]:.1f},{y(p[1]):.1f}" for i, p in enumerate(points))
    return (f'<svg viewBox="0 0 {w} {h}" aria-hidden="true"><polyline vector-effect="non-scaling-stroke" points="{q}" fill="none" stroke="{T5}" stroke-width="1.5" '
            f'stroke-dasharray="4 3"/><polyline vector-effect="non-scaling-stroke" points="{a}" fill="none" stroke="{GOLD_TEXT}" stroke-width="2.5" stroke-linejoin="round"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{y(points[-1][0]):.1f}" r="3" fill="{GOLD_TEXT}"/></svg>')


def home_day(day):
    """Lines on a Home card: the latest day's plus/minus (today so far, while the market is open)
    and the week so far, for the account and its index fund."""
    if not day:
        return ""
    d = pd.Timestamp(day["day"])
    when = (f'<span class="live"><i></i>Live {clock(pd.Timestamp(day["as_of"]))}</span><span>Today</span>' if day.get("live")
            else f'<span>{d:%a %b %d}</span>')
    out = (f'<div class="mday">{when}'
           + (f'<b class="{tone(day["change"])}">{signed_money(day["change"])} ({signed_pct(day["pct"])})</b>'
              if day["invested"] else f'<b>{money(0)}</b><span>(in cash)</span>') +
           f'<span>{LABEL} {signed_pct(day["bench_pct"])}</span>'
           f'{"" if day["recorded"] or day.get("live") else "<span class=dim>not in the record yet</span>"}</div>')
    if "week_change" in day:
        out += (f'<div class="mday"><span>Week so far</span><b class="{tone(day["week_change"])}">'
                f'{signed_money(day["week_change"])} ({signed_pct(day["week_pct"])})</b>'
                f'<span>{LABEL} {signed_pct(day["week_bench_pct"])}</span></div>')
    return out


def switch_heads_up(s):
    """A Home card note when the safety switch points away from what's held (acted on Friday)."""
    held = bool(s.get("held"))
    if s["risk_on"] == held:
        return ""
    if held:
        return (f'<p class="warn">Safety switch: {LABEL} is under its 200-day average. '
                f"If it's still under at Friday's close, everything is sold.</p>")
    return (f'<p class="warn">Safety switch: {LABEL} is back above its 200-day average. '
            f"If it's still above at Friday's close, the rules buy back in.</p>")


def home_total(markets, fx=None):
    """All the accounts added up in US$ (C$ converted at `fx`, today's rate): profit/loss since
    each started, the same money in each index fund, and today's and the week's plus/minus."""
    rate = {"US$": 1.0}
    if fx and fx.get("usd_per_cad"):
        rate["C$"] = float(fx["usd_per_cad"])
    active = [(m, _SUMMARIES.get(m["slug"])) for m in markets if m.get("active", True)]
    rows = [(rate[m["currency"]], s) for m, s in active if s and m["currency"] in rate]
    left_out = [m["name"] for m, s in active if s and m["currency"] not in rate]
    if len(rows) < 2:
        return ""

    def usd(x):
        return f"${x:,.0f}"

    def signed_usd(x):
        return "$0.00" if abs(x) < 0.005 else f'{"+" if x > 0 else "&minus;"}${abs(x):,.2f}'

    cap = sum(r * s["capital"] for r, s in rows)
    # At the latest prices: the day's value (today so far, while markets are open) when there is one.
    val = sum(r * (s["day"]["value"] if s.get("day") else s["value"]) for r, s in rows)
    bench = sum(r * (s["day"]["bench_value"] if s.get("day") else s["bench_value"]) for r, s in rows)
    pl, bpl = val - cap, bench - cap
    held = sum(1 for _, s in rows if s.get("held"))
    lines = ""
    days = [(r, s["day"]) for r, s in rows if s.get("day")]
    if days:
        ch = sum(r * d["change"] for r, d in days)
        base = sum(r * (d["value"] - d["change"]) for r, d in days)
        bch = sum(r * d["bench_change"] for r, d in days)
        bbase = sum(r * (d["bench_value"] - d["bench_change"]) for r, d in days)
        live = [d["as_of"] for _, d in days if d.get("live")]
        when = (f'<span class="live"><i></i>Live {clock(pd.Timestamp(max(live)))}</span><span>Today</span>' if live
                else f'<span>{pd.Timestamp(max(d["day"] for _, d in days)):%a %b %d}</span>')
        lines += (f'<div class="mday">{when}<b class="{tone(ch)}">{signed_usd(ch)} ({signed_pct(ch / base * 100)})</b>'
                  f'<span>index funds {signed_pct(bch / bbase * 100)}</span></div>')
        wk = [(r, d) for r, d in days if "week_change" in d]
        if wk:
            wch = sum(r * d["week_change"] for r, d in wk)
            wbase = sum(r * (d["value"] - d["week_change"]) for r, d in wk)
            b1 = sum(r * d["bench_value"] for r, d in wk)
            b0 = sum(r * d["bench_value"] / (1 + d["week_bench_pct"] / 100) for r, d in wk)
            lines += (f'<div class="mday"><span>Week so far</span><b class="{tone(wch)}">{signed_usd(wch)} '
                      f'({signed_pct(wch / wbase * 100)})</b><span>index funds {signed_pct((b1 / b0 - 1) * 100)}</span></div>')
    cad = [m["name"] for m, s in active if s and m["currency"] == "C$" and "C$" in rate]
    note = []
    if cad:
        note.append(f'{", ".join(cad)} counted in US$ at 1 C$ = {rate["C$"]:.4f} US$ ({pd.Timestamp(fx["date"]):%b %d}).')
    if left_out:
        note.append(f'Not counted (no exchange rate yet): {", ".join(left_out)}.')
    return f'''<section class="card sec full tot"><div class="mtop"><div><div class="mname">All your accounts</div>
<div class="msub">{len(rows)} paper accounts added up, in US$</div></div><span class="state {"on" if held else "off"}">{held} of {len(rows)} invested</span></div>
<div><div class="msub">Profit/loss since each account started</div><div class="tval {tone(pl)}">{signed_usd(pl)}</div>
<div class="msub">{signed_pct(pl / cap * 100)} &middot; worth {usd(val)} of {usd(cap)} put in, at the latest prices</div></div>
<div class="mday"><span>Same money in the index funds</span><b class="{tone(bpl)}">{signed_usd(bpl)} ({signed_pct(bpl / cap * 100)})</b>
<span>you're {usd(abs(pl - bpl))} {"ahead" if pl >= bpl else "behind"}</span></div>
{lines}{f'<p class="note" style="margin:0">{" ".join(note)}</p>' if note else ""}</section>'''


def build_home(markets, summary_path, fx=None):
    load_summaries(markets, summary_path)
    cards = []
    for m in markets:
        set_market(m)
        s = _SUMMARIES.get(m["slug"])
        label, cls = summary_state(m)
        sub = f'{esc(m["about"])} &middot; compared with {LABEL} &middot; {m["currency"]}'
        if m.get("active", True) and s:
            ret = (s["value"] / s["capital"] - 1) * 100
            diff = s["value"] - s["bench_value"]
            body = (f'<div class="mrow"><div><div class="mval">{money(s["value"], False)}<small>{m["currency"]}</small></div>'
                    f'<div class="msub"><span class="{"pos" if ret >= 0 else "neg"}">{"+" if ret >= 0 else "&minus;"}{abs(ret):.1f}%</span>'
                    f' since {pd.Timestamp(s["start"]):%b %d} &middot; {money(abs(diff), False)} {"ahead of" if diff >= 0 else "behind"} {LABEL}</div></div>'
                    f'{mini_spark(s["spark"])}</div>'
                    f'{home_day(s.get("day"))}{switch_heads_up(s)}'
                    f'<div class="wk"><span>Last trades: {esc(s["week"])}</span><b>Open &#8250;</b></div>')
        else:
            msg = (f"First trades at the close on {pd.Timestamp(m['start_date']):%a %b %d}"
                   if m.get("active", True) and m.get("start_date") else "Starts once its size is picked from the backtest")
            body = (f'<div class="mrow"><div><div class="mval dim">{money(m["capital"], False)}<small>{m["currency"]}</small></div>'
                    f'<div class="msub">{msg}</div></div></div>'
                    f'<div class="wk"><span>See how the rules tested.</span><b>Open &#8250;</b></div>')
        cards.append(f'''<a class="mcard" href="{m["slug"]}/"><div class="mtop"><div><div class="mname">{esc(m["name"])}</div>
<div class="msub">{sub}</div></div><span class="state {cls}">{"In cash" if label == "Cash" else label}</span></div>{body}</a>''')
    header = f'''<header class="hdr"><div class="wrap hin">
<div class="brandrow">{logo.svg()}<div><div class="brand">Pixel Invest</div><h1 class="htitle">All markets</h1></div></div>
<div class="sub">Pretend money. Each market has its own account, in its own currency. Trades every weekend; the plus/minus updates every 30 minutes while markets are open.</div>
</div></header>'''
    main = f'''<main class="wrap">{home_total(markets, fx)}<div class="mcards full">{"".join(cards)}</div>
<p class="note full">Past and paper results don't guarantee future returns. Tap a market for its trades, charts and rules.</p></main>'''
    return page_shell("Pixel Invest", "", markets, None, header, main)
