"""Tests for the paper trader's ledger engine, on made-up prices (no internet)."""

import copy
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paper_trader as pt  # noqa: E402

CFG = {**pt.DEFAULTS, "top_n": 3, "exit_rank": 6}


def make_prices(days=700, n=25, seed=1, qqq_trend=0.0006):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=days)
    drift = rng.normal(0.0004, 0.0008, n)
    rets = rng.normal(drift, 0.02, (days, n))
    cols = [f"S{i:02d}" for i in range(n)]
    px = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=cols)
    px[pt.BENCHMARK] = 300 * np.exp(np.cumsum(rng.normal(qqq_trend, 0.01, days)))
    return px


def no_splits(px):
    return pd.DataFrame(0.0, index=px.index, columns=px.columns)


def fresh(px, start):
    first = px.index[px.index >= pd.Timestamp(start)][0]
    return pt.new_ledger(first.date(), px.at[first, pt.BENCHMARK], CFG["capital"])


def run_once(px, start, cutoff):
    led = fresh(px, start)
    t, v, _ = pt.advance(px, no_splits(px), CFG, led, cutoff)
    return led, t, v


def test_weekly_runs_match_one_long_run():
    px = make_prices()
    start, end = "2024-11-06", date(2026, 3, 1)
    led_all, trades_all, values_all = run_once(px, start, end)
    assert any(t["action"] == "SELL" for t in trades_all)

    led, trades, values = fresh(px, start), [], []
    for cut in pd.date_range(start, end, freq="W-SAT"):          # run every Saturday
        t, v, _ = pt.advance(px, no_splits(px), CFG, led, cut.date())
        trades += t
        values += v
    assert trades == trades_all
    assert values == values_all
    assert led["positions"] == led_all["positions"]


def test_midweek_runs_also_match():
    px = make_prices()
    start, end = "2024-11-06", date(2025, 6, 1)
    _, trades_all, values_all = run_once(px, start, end)
    led, trades, values = fresh(px, start), [], []
    for cut in pd.date_range(start, end, freq="D")[::3]:         # every 3rd day, any weekday
        t, v, _ = pt.advance(px, no_splits(px), CFG, led, cut.date())
        trades += t
        values += v
    t, v, _ = pt.advance(px, no_splits(px), CFG, led, end)
    assert trades + t == trades_all
    assert values + v == values_all


def test_past_trades_never_change_when_old_prices_are_revised():
    px = make_prices()
    led, first_trades, _ = run_once(px, "2024-11-06", date(2025, 3, 1))
    revised = px.copy()
    revised.loc[:"2025-02-28"] *= 1.37                           # rewrite history
    revised.loc[:"2025-02-28", "S03"] *= 5
    t, _, _ = pt.advance(revised, no_splits(revised), CFG, led, date(2025, 3, 15))
    assert all(x["date"] > "2025-02-28" for x in t)


def test_account_survives_beyond_two_year_download_window():
    px = make_prices(days=1100)
    led, _, _ = run_once(px, "2024-11-06", date(2027, 1, 1))
    # Later runs only download ~450 days before last_processed
    window = px[px.index > pd.Timestamp(led["last_processed"]) - pd.Timedelta(days=pt.HISTORY_DAYS)]
    t, v, _ = pt.advance(window, no_splits(window), CFG, led, date(2027, 3, 1))
    assert v, "should keep processing new days"
    assert v[-1]["qqq_same_money"] == pytest.approx(
        CFG["capital"] * px.at[pd.Timestamp(v[-1]["date"]), pt.BENCHMARK] / px[pt.BENCHMARK].loc["2024-11-06"], abs=0.01)


def test_split_after_last_download_adjusts_carried_shares():
    px = make_prices()
    led, _, values = run_once(px, "2024-11-06", date(2025, 3, 1))
    held = next(iter(led["positions"]))
    before = values[-1]["paper_account"]
    led["data_through"] = values[-1]["date"]
    shares_before = led["positions"][held]["shares"]

    # Yahoo now reports a 4-for-1 split on Mar 3: all history before it is divided by 4
    adj = px.copy()
    adj.loc[:"2025-02-28", held] /= 4
    splits = no_splits(adj)
    splits.at[pd.Timestamp("2025-03-03"), held] = 4.0
    adj.loc["2025-03-03":, held] = adj.at[pd.Timestamp("2025-02-28"), held]   # flat after
    _, v, notes = pt.advance(adj, splits, CFG, led, date(2025, 3, 8))
    assert notes and held in notes[0]
    if held in led["positions"]:
        assert led["positions"][held]["shares"] == pytest.approx(shares_before * 4)
    # Account value shouldn't drop 75% of that holding's worth
    assert v[0]["paper_account"] > before * 0.8


def test_safety_switch_goes_to_cash():
    px = make_prices()
    px.loc["2025-01-01":, pt.BENCHMARK] *= 0.5                   # crash
    _, trades, values = run_once(px, "2024-11-06", date(2025, 2, 1))
    sells = [t for t in trades if t["reason"].startswith("Safety switch")]
    assert sells
    assert values[-1]["positions"] == 0


def test_unfinished_week_waits_for_its_last_day():
    px = make_prices()
    # Thursday 2025-01-09 cutoff: Mon-Wed of that week are final, Wed might be the
    # last trading day of the week, so it waits.
    led, _, values = run_once(px, "2024-11-06", date(2025, 1, 9))
    assert values[-1]["date"] == "2025-01-07"
    assert led["last_rebalance"] == "2025-01-03"


def test_missing_qqq_history_stops_instead_of_selling():
    px = make_prices()
    with pytest.raises(pt.DataError):
        run_once(px, "2024-02-01", date(2024, 3, 1))           # < 200 days of QQQ


def test_final_cutoff_respects_market_close():
    from datetime import datetime
    assert pt.final_cutoff(datetime(2026, 11, 13, 15, 0)) == date(2026, 11, 13)
    assert pt.final_cutoff(datetime(2026, 11, 13, 17, 0)) == date(2026, 11, 14)


def test_dividends_are_paid_into_cash_and_the_index_reinvests():
    px = make_prices()
    cfg = {**CFG, "dividends": True}
    first = px.index[px.index >= pd.Timestamp("2024-11-06")][0]
    led = pt.new_ledger(first.date(), px.at[first, pt.BENCHMARK], cfg["capital"], dividends=True)
    t, v, _ = pt.advance(px, no_splits(px), cfg, led, date(2025, 1, 4), no_splits(px))
    held = next(iter(led["positions"]))
    shares, cash0 = led["positions"][held]["shares"], led["cash"]
    divs = no_splits(px)
    divs.at[pd.Timestamp("2025-01-07"), held] = 2.0                  # $2 a share
    divs.at[pd.Timestamp("2025-01-07"), pt.BENCHMARK] = 3.0          # index fund pays too
    units0 = led["bench_units"]
    _, v2, _ = pt.advance(px, no_splits(px), cfg, led, date(2025, 1, 9), divs)
    assert led["dividends_received"] == pytest.approx(shares * 2.0)
    assert led["cash"] == pytest.approx(cash0 + shares * 2.0)
    assert led["bench_units"] == pytest.approx(units0 * (1 + 3.0 / px.at[pd.Timestamp("2025-01-07"), pt.BENCHMARK]))


def test_price_only_markets_ignore_dividends():
    px = make_prices()
    led, _, values = run_once(px, "2024-11-06", date(2025, 1, 4))
    divs = no_splits(px)
    divs.loc["2025-01-07"] = 5.0
    cash0 = led["cash"]
    pt.advance(px, no_splits(px), CFG, led, date(2025, 1, 9), divs)
    assert "bench_units" not in led and led["cash"] == cash0


def _values(rows):
    return pd.DataFrame(rows, columns=pt.VALUE_COLS)


def test_day_change_values_unrecorded_days_from_shares_held():
    idx = pd.to_datetime(["2026-11-13", "2026-11-16", "2026-11-17"])
    px = pd.DataFrame({"AAA": [100.0, 110.0, 99.0], "BBB": [50.0, 50.0, 55.0],
                       pt.BENCHMARK: [200.0, 202.0, 204.02]}, index=idx)
    led = {"start": "2026-11-13", "qqq_start": 200.0, "cash": 0.0, "last_processed": "2026-11-13",
           "positions": {"AAA": {"shares": 50.0, "buy_price": 100.0, "buy_date": "2026-11-13"},
                         "BBB": {"shares": 100.0, "buy_price": 50.0, "buy_date": "2026-11-13"}}}
    vals = _values([["2026-11-13", 10000.0, 0.0, 2, 10000.0]])
    before = copy.deepcopy(led)
    d = pt.day_change(px, CFG, led, vals)
    assert led == before                                    # nothing recorded or changed
    assert d["day"] == "2026-11-17" and d["prev"] == "2026-11-16" and not d["recorded"]
    # Mon: 50*110 + 100*50 = 10,500. Tue: 50*99 + 100*55 = 10,450.
    assert d["value"] == pytest.approx(10450) and d["change"] == pytest.approx(-50)
    assert d["pct"] == pytest.approx(-50 / 10500 * 100, abs=1e-3)
    assert d["bench_pct"] == pytest.approx(1.0, abs=1e-3)
    assert d["week_change"] == pytest.approx(450) and d["since"] == "2026-11-13"
    assert [m[0] for m in d["movers"]] == ["BBB", "AAA"]     # +10% then -10%


def test_day_change_uses_record_when_nothing_newer():
    idx = pd.to_datetime(["2026-11-12", "2026-11-13"])
    px = pd.DataFrame({pt.BENCHMARK: [200.0, 198.0]}, index=idx)
    led = {"start": "2026-11-12", "qqq_start": 200.0, "cash": 10000.0, "last_processed": "2026-11-13",
           "positions": {}}
    vals = _values([["2026-11-12", 10000.0, 10000.0, 0, 10000.0], ["2026-11-13", 10000.0, 10000.0, 0, 9900.0]])
    d = pt.day_change(px, CFG, led, vals)
    assert d["recorded"] and d["change"] == 0 and d["invested"] == 0
    assert d["bench_change"] == pytest.approx(-100)
    assert "week_change" not in d


def test_day_change_needs_two_days():
    idx = pd.to_datetime(["2026-11-13"])
    px = pd.DataFrame({pt.BENCHMARK: [200.0]}, index=idx)
    led = {"start": "2026-11-13", "qqq_start": 200.0, "cash": 10000.0, "last_processed": "2026-11-13", "positions": {}}
    assert pt.day_change(px, CFG, led, _values([["2026-11-13", 10000.0, 10000.0, 0, 10000.0]])) is None


# ---- live estimate while the market is open (shown only, never recorded)
NY = "America/New_York"


def _ny(s):
    return pd.Timestamp(s, tz=NY).to_pydatetime()


def test_market_open_hours():
    assert not pt.market_open(_ny("2026-11-18 09:29"))
    assert pt.market_open(_ny("2026-11-18 09:30")) and pt.market_open(_ny("2026-11-18 16:29"))
    assert not pt.market_open(_ny("2026-11-18 16:30"))          # closing prices count as final now
    assert not pt.market_open(_ny("2026-11-21 12:00"))          # Saturday


def _held_week():
    """Bought AAA and BBB at Fri Nov 13's close; Mon settled, Tue unrecorded, Wed is today."""
    idx = pd.to_datetime(["2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18"])
    closes = pd.DataFrame({"AAA": [100.0, 110.0, 99.0, 90.0], "BBB": [50.0, 50.0, 55.0, 60.0],
                           "ZZZ": [10.0, 10.0, 10.0, 11.0], pt.BENCHMARK: [200.0, 202.0, 204.02, 210.0]}, index=idx)
    led = {"start": "2026-11-13", "qqq_start": 200.0, "cash": 0.0, "last_processed": "2026-11-16",
           "last_rebalance": "2026-11-13",
           "positions": {"AAA": {"shares": 50.0, "buy_price": 100.0, "buy_date": "2026-11-13"},
                         "BBB": {"shares": 100.0, "buy_price": 50.0, "buy_date": "2026-11-13"}}}
    vals = _values([["2026-11-13", 10000.0, 0.0, 2, 10000.0], ["2026-11-16", 10500.0, 0.0, 2, 10100.0]])
    bars = pd.DataFrame({"AAA": [111.0, 98.0, 97.0, 95.0, 94.0], "BBB": [50.0, 56.0, 57.0, 58.0, 59.0],
                         pt.BENCHMARK: [203.0, 204.0, 205.0, 206.0, 207.0]},
                        index=pd.DatetimeIndex(["2026-11-16 15:30", "2026-11-17 15:30", "2026-11-18 09:30",
                                                "2026-11-18 10:00", "2026-11-18 10:30"]).tz_localize(NY))
    return closes, led, vals, bars


def test_live_prices_use_the_latest_30_minute_prices():
    closes, led, vals, bars = _held_week()
    now = _ny("2026-11-18 10:12")                 # the 10:30 bar hasn't started yet
    row = pt.live_prices(closes, bars, now, pt.BENCHMARK)
    assert row.name == pd.Timestamp("2026-11-18")
    assert row["AAA"] == 95.0 and row[pt.BENCHMARK] == 206.0     # the 10:00 bar so far
    assert row["ZZZ"] == 11.0                                     # not held: today's daily row
    assert pt.live_prices(closes, bars, _ny("2026-11-18 09:00"), pt.BENCHMARK) is None
    assert pt.live_prices(closes, bars, _ny("2026-11-18 17:00"), pt.BENCHMARK) is None


def test_live_week_and_curve_meet_and_record_nothing():
    closes, led, vals, bars = _held_week()
    now = _ny("2026-11-18 10:12")
    prices = closes[closes.index < pd.Timestamp("2026-11-18")]
    shown = pd.concat([prices, pt.live_prices(closes, bars, now, pt.BENCHMARK).to_frame().T])
    before = copy.deepcopy(led)
    series = pt.valued_days(shown, CFG, led, vals)
    d = pt.day_change(shown, CFG, led, vals, series=series)
    assert led == before
    # Today so far: 50*95 + 100*58 = 10,550, against Tue's 50*99 + 100*55 = 10,450.
    assert d["day"] == "2026-11-18" and d["value"] == pytest.approx(10550) and d["change"] == pytest.approx(100)
    assert d["week_change"] == pytest.approx(550) and d["since"] == "2026-11-13"
    pts = pt.week_curve(bars, series, shown, CFG, led, now)
    assert pts[0][:3] == [0, "Fri Nov 13 close", 10000.0]
    assert [p[0] for p in pts] == [0, 390, 780, 810, 822]     # Fri close, Mon/Tue close, Wed 10:00, now (10:12)
    assert pts[1][2] == pytest.approx(10500) and pts[2][2] == pytest.approx(10450)   # each day ends on its value
    assert pts[-1][2] == pytest.approx(d["value"]) and pts[-1][1] == "Wed 10:12am"
    # Once the week's trades have happened the holdings changed, so there's no curve.
    assert pt.week_curve(bars, series, shown, CFG, {**led, "last_rebalance": "2026-11-18"}, now) is None


def test_badge_shows_what_is_held_not_where_the_switch_points():
    import dashboard
    m = {"slug": "x-test", "active": True}
    dashboard._SUMMARIES["x-test"] = {"risk_on": False, "held": 5}     # switch off mid-week, still holding
    assert dashboard.summary_state(m) == ("Invested", "on")
    assert "everything is sold" in dashboard.switch_heads_up(dashboard._SUMMARIES["x-test"])
    dashboard._SUMMARIES["x-test"] = {"risk_on": True, "held": 0}      # back on mid-week, still in cash
    assert dashboard.summary_state(m) == ("Cash", "off")
    dashboard._SUMMARIES["x-test"] = {"risk_on": True, "held": 5}      # agree: no heads-up
    assert dashboard.switch_heads_up(dashboard._SUMMARIES["x-test"]) == ""
    del dashboard._SUMMARIES["x-test"]


def test_home_total_adds_accounts_in_us_dollars():
    import dashboard
    day = lambda v, c, bv, bc: {"day": "2026-11-18", "value": v, "change": c, "bench_value": bv, "bench_change": bc,
                                "invested": 1, "recorded": False, "week_change": c, "week_pct": 0.0, "week_bench_pct": 0.0}
    mk = [{"slug": "us-t", "name": "US", "currency": "US$"}, {"slug": "ca-t", "name": "Canada", "currency": "C$"}]
    dashboard._SUMMARIES["us-t"] = {"capital": 10000.0, "value": 9000.0, "bench_value": 9500.0, "held": 5,
                                        "day": day(9100.0, 100.0, 9400.0, -100.0)}
    dashboard._SUMMARIES["ca-t"] = {"capital": 10000.0, "value": 11000.0, "bench_value": 10000.0, "held": 0,
                                        "day": day(11000.0, 0.0, 10000.0, 0.0)}
    html = dashboard.home_total(mk, {"usd_per_cad": 0.7, "date": "2026-11-18"})
    # Latest values: 9,100 + 0.7 * 11,000 = 16,800 against 17,000 put in (-200); the index funds 16,400, so $400 ahead.
    assert "&minus;$200.00" in html and "worth $16,800, from $17,000 put in" in html
    assert "$400 ahead of the index funds" in html and "+$100.00" in html
    assert "Canada counted in US$ at 1 C$ = 0.7000 US$" in html
    assert dashboard.home_total(mk, None) == ""
    for k in ("us-t", "ca-t"):
        del dashboard._SUMMARIES[k]


def test_52_week_bar_uses_the_last_year_of_closes():
    import dashboard
    idx = pd.bdate_range("2025-06-02", "2026-11-20")
    px = pd.DataFrame({"AAA": np.linspace(50.0, 150.0, len(idx))}, index=idx)
    px.loc["2025-09-01":"2025-09-30", "AAA"] = 400.0      # over a year ago: left out
    px.iloc[-1] = 140.0                                    # the latest price, below the high
    lo, hi, now = dashboard.year_range(px, "AAA")
    assert hi == px["AAA"].iloc[-2] and now == 140.0
    assert lo == px.loc[px.index > idx[-1] - pd.Timedelta(days=365), "AAA"].min()
    bar = dashboard.range_bar((100.0, 150.0, 140.0))
    assert "7% below high" in bar and "left:80.0%" in bar
    assert "At its high" in dashboard.range_bar((100.0, 150.0, 150.0))
    assert dashboard.year_range(px.tail(50), "AAA") is None              # under a year of prices
    assert dashboard.year_range(px, "ZZZ") is None and dashboard.range_bar(None) == ""


def test_week_end_gaps_find_a_missing_friday_close():
    days = pd.bdate_range("2026-11-09", "2026-11-19")                    # Fri Nov 20 not posted yet
    assert pt.week_end_gaps(days, "2026-11-18", date(2026, 11, 21)) == [date(2026, 11, 20)]
    assert pt.week_end_gaps(days, "2026-11-18", date(2026, 11, 20)) == []   # Friday not final yet
    assert pt.week_end_gaps(days, "2026-11-13", date(2026, 11, 21)) == [date(2026, 11, 20)]
    full = pd.bdate_range("2026-11-09", "2026-11-20")
    assert pt.week_end_gaps(full, "2026-11-18", date(2026, 11, 21)) == []


def test_late_friday_close_waits_but_a_holiday_trades():
    days = pd.bdate_range("2026-11-09", "2026-11-19")
    def bars_on(*dates):
        idx = pd.DatetimeIndex([f"{d} 15:30" for d in dates]).tz_localize(NY)
        return lambda tickers: pd.DataFrame({pt.BENCHMARK: 1.0}, index=idx)
    def down(tickers):
        raise ConnectionError("no network")
    check = lambda d, intraday: pt.check_week_end_closes(d, "2026-11-18", date(2026, 11, 21), pt.BENCHMARK, intraday)
    with pytest.raises(pt.DataError, match="Fri Nov 20"):
        check(days, bars_on("2026-11-19", "2026-11-20"))      # Friday traded: its close is just late
    check(days, bars_on("2026-11-18", "2026-11-19"))           # no trading Friday: a holiday, go ahead
    with pytest.raises(pt.DataError):
        check(days, down)                                      # can't tell: wait
    check(pd.bdate_range("2026-11-09", "2026-11-20"), down)    # nothing missing: no download needed
    # A new account (nothing processed yet) checks its whole history the same way
    px = make_prices()
    with pytest.raises(pt.DataError, match="Fri Jan 10"):
        pt.check_week_end_closes(px.index[px.index < "2025-01-10"], None, date(2025, 1, 11),
                                 pt.BENCHMARK, bars_on("2025-01-10"))


def test_switch_now_reads_the_latest_price():
    idx = pd.bdate_range("2026-01-01", periods=260)
    px = pd.DataFrame({pt.BENCHMARK: np.r_[np.full(259, 100.0), 90.0]}, index=idx)
    sw = pt.switch_now(px, {**CFG, "sma_days": 200})
    assert sw["on"] is False and sw["gap"] == round((90 / 99.95 - 1) * 100, 2)
    assert pt.switch_now(px.tail(50), {**CFG, "sma_days": 200}) is None       # too short for an average


def test_heads_up_after_fridays_close_says_what_the_weekend_does():
    import dashboard
    fri = lambda **k: {"day": "2026-11-20", **k}
    assert dashboard.week_closed(fri(live=True, as_of="2026-11-20T16:00"))
    assert not dashboard.week_closed(fri(live=True, as_of="2026-11-20T15:30"))
    assert dashboard.week_closed(fri())                                       # an evening or weekend run
    assert not dashboard.week_closed({"day": "2026-11-19"})                   # Thursday: the week goes on
    assert not dashboard.week_closed(None)
    held_off = {"risk_on": False, "held": 5}
    after = dashboard.switch_heads_up(held_off, -0.3, closed=True)
    assert "Selling everything at Friday" in after and "Friday's closing prices" in after and "0.3%" in after
    assert "If it's still under" in dashboard.switch_heads_up(held_off, -0.3)
    assert "Buying back in" in dashboard.switch_heads_up({"risk_on": True, "held": 0}, 0.4, closed=True)
    assert dashboard.day_name(fri(live=True, as_of="2026-11-20T16:00")) == "Fri Nov 20"
    # On the Home page the latest prices win over the last recorded day
    s = {"risk_on": True, "held": 5}
    assert "Selling everything" in dashboard.heads_for(s, fri(live=True, as_of="2026-11-20T16:00",
                                                            switch={"on": False, "gap": -0.3}))
    assert dashboard.heads_for(s, None) == ""


def test_race_lanes_show_each_market_against_its_fund():
    import dashboard
    ms = [{"slug": "x-a", "name": "A", "short": "A", "bench_label": "QQQ", "symbol": "$", "active": True},
          {"slug": "x-b", "name": "B", "short": "B", "bench_label": "XIU", "symbol": "C$", "active": True}]
    dashboard._SUMMARIES["x-a"] = {"risk_on": False, "held": 0, "capital": 10_000, "value": 9_800, "bench_value": 9_300}
    dashboard._SUMMARIES["x-b"] = {"risk_on": True, "held": 4, "capital": 10_000, "value": 9_000, "bench_value": 9_250,
                                   "day": {"value": 9_100, "bench_value": 9_350}}
    a, b = dashboard.standing_of(ms[0]), dashboard.standing_of(ms[1])
    assert (a["txt"], a["cls"], a["state"]) == ("+$500", "pos", "In cash")
    assert (b["txt"], b["cls"], b["state"]) == ("&minus;C$250", "neg", "Invested")    # latest prices win
    html_ = dashboard.lanes(ms, "../", "x-a")
    assert 'class="lane on" href="../x-a/" aria-current=page' in html_
    assert '<i class="pos" style="--w:100.0%">' in html_ and '<i class="neg" style="--w:50.0%">' in html_
    for k in ("x-a", "x-b"):
        del dashboard._SUMMARIES[k]


def test_refresh_menus_brings_in_later_scores_and_keeps_section_links():
    import dashboard
    ms = [{"slug": "x-a", "name": "A", "bench_label": "QQQ", "symbol": "$", "active": True}]
    dashboard._SUMMARIES["x-a"] = {"risk_on": False, "held": 0, "capital": 10_000, "value": 10_100, "bench_value": 10_000}
    page = dashboard.page_shell("t", "../", ms, "x-a", "<header></header>", "<main></main>",
                                [("week", "This week"), ("rules", "Rules")])
    assert "+$100" in page
    dashboard._SUMMARIES["x-a"]["value"] = 9_900
    new = dashboard.refresh_menus(page, "../", ms, "x-a")
    assert "+$100" not in new and "&minus;$100" in new
    assert new.count('data-sec="week"') == page.count('data-sec="week"') == 2      # side menu + phone strip
    del dashboard._SUMMARIES["x-a"]
