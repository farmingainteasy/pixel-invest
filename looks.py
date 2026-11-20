"""The Old look / New look switch, while the two looks are compared side by side.

The old look (dashboard.py) is the site as it was, at docs/. The new look (dashboard_new.py)
is built from the same data into docs/new/. A pill at the bottom of every page jumps to the
same page in the other look. Once one look is picked, delete the other module, this file and
docs/new/.
"""

CSS = """
.looks{position:fixed;left:50%;transform:translateX(-50%);bottom:max(14px,env(safe-area-inset-bottom));z-index:50;
 display:flex;gap:2px;padding:3px;background:rgba(28,26,20,.94);border:1px solid rgba(255,255,255,.16);border-radius:999px;
 box-shadow:0 8px 28px rgba(0,0,0,.65);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
.looks a,.looks span{font:700 13px/1 Inter,system-ui,-apple-system,sans-serif;color:#C4BDAA;text-decoration:none;
 padding:0 16px;min-height:40px;border-radius:999px;display:inline-flex;align-items:center;white-space:nowrap}
.looks span{background:#C8A951;color:#1C1A14}
.looks a:focus-visible{outline:2px solid #C8A951;outline-offset:2px}
body{padding-bottom:84px}
"""


def switch(rel, current, is_new):
    """rel = path back to this look's root ('' for Home, '../' for a market page);
    current = the market's slug, or None on Home."""
    here = "" if current is None else f"{current}/"
    old = '<span aria-current="page">Old look</span>' if not is_new else f'<a href="{rel}../{here}">Old look</a>'
    new = '<span aria-current="page">New look</span>' if is_new else f'<a href="{rel}new/{here}">New look</a>'
    return f'<nav class="looks" aria-label="Page look">{old}{new}</nav>'
