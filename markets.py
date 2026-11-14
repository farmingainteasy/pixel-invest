"""Every market Pixel Invest runs. Adding a market = adding one block here.

Each market gets its own paper account (data/<slug>/), its own page
(docs/<slug>/), a card on the Home page and a line in the menu.
Set "active": False to show a market as "Not started" (no trading yet).
"""

from momentum_backtest import NASDAQ_100

TSX_60 = [t + ".TO" for t in """
RY TD BNS BMO CM NA MFC SLF POW IFC FFH BN BAM X GWO
ENB TRP CNQ SU CVE IMO TOU PPL
NTR AEM ABX K WPM FNV TECK-B FM CCO LUN AGI
CNR CP WCN CAE WSP TRI RBA STN TFII BBD-B
SHOP CSU OTEX GIB-A CLS DSG
ATD L DOL MRU WN QSR CTC-A GIL MG SAP CCL-B
BCE T RCI-B FTS EMA H
""".split()]          # approximate TSX 60 members (Yahoo symbols); edit freely

MARKETS = [
    {
        "slug": "nasdaq-100",
        "name": "Nasdaq-100",
        "about": "US tech-heavy",
        "benchmark": "QQQ", "bench_label": "QQQ",
        "currency": "US$", "symbol": "$",
        "tickers": NASDAQ_100,
        "top_n": 10, "exit_rank": 20, "lookback": 63, "sma_days": 200,
        "capital": 10_000.0, "cost_pct": 0.0,
        "dividends": False,          # price-only, as since the account started
        "active": True,
    },
    {
        "slug": "tsx-60",
        "name": "TSX 60",
        "about": "Canada's largest companies",
        "benchmark": "XIU.TO", "bench_label": "XIU",
        "currency": "C$", "symbol": "C$",
        "tickers": TSX_60,
        "top_n": 8, "exit_rank": 16, "lookback": 63, "sma_days": 200,   # picked from the 2016-26 backtest
        "capital": 10_000.0, "cost_pct": 0.0,
        "dividends": True,           # Canadian banks, telecoms and pipelines pay a lot
        "active": True,
        "start_date": "2026-11-20",  # first trades at this Friday's close
    },
]
