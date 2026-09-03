import pandas as pd

from scripts.optimize_rr import MARKET_RT_COST_PCT, simulate


def ohlc(rows):
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close",
                                       "quote_volume"])


def make_df():
    rows = [(0, 100, 100.5, 99.5, 100, 1000.0)]
    rows.append((60_000, 100.2, 101.5, 99.8, 100.8, 1000.0))   # BE tetiğine değdi
    rows.append((120_000, 100.7, 100.9, 99.4, 99.6, 1000.0))   # girişe geri döndü
    return ohlc(rows)


def test_breakeven_exit_saves_loss():
    df = make_df()
    # stop 2 (98), hedef 4 (104), be_r=0.5 -> tetik 101, bar1'de armed
    result, pnl = simulate(df, 0, "LONG", 100.0, 2.0, 4.0, horizon=2, be_r=0.5)
    assert result == "BE"
    assert abs(pnl + MARKET_RT_COST_PCT * 100) < 1e-9  # gross 0, sadece maliyet


def test_no_breakeven_rides_to_loss():
    df = make_df()
    result, pnl = simulate(df, 0, "LONG", 100.0, 2.0, 4.0, horizon=2, be_r=0.0)
    assert result == "EXP_LOSS"
    assert pnl < -0.3


def test_breakeven_never_triggers_without_touch():
    df = ohlc([
        (0, 100, 100.5, 99.5, 100, 1000.0),
        (60_000, 100.2, 100.8, 99.9, 100.5, 1000.0),
        (120_000, 100.5, 100.9, 99.6, 99.8, 1000.0),
    ])
    result, pnl = simulate(df, 0, "LONG", 100.0, 2.0, 4.0, horizon=2, be_r=0.5)
    assert result == "EXP_LOSS"
    assert -0.3 < pnl < -0.2  # horizon sonunda -0.2% gross + maliyet
