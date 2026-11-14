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
