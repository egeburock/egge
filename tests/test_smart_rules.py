import pandas as pd

from src.rules import (absorption, ob_retest, supertrend, supertrend_rule,
                       volume_zscore)


def ohlc(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "quote_volume"])


def flat(n=60, vol=1000.0, price=100.0):
    return ohlc([[price, price + 1, price - 1, price, vol] for _ in range(n)])


def test_supertrend_uptrend():
    closes = [100 + i * 2 for i in range(80)]
    df = ohlc([[c - 1, c + 1, c - 2, c, 1000.0] for c in closes])
    trend, stop = supertrend(df, 20, 2.0)
    assert trend == 1
    assert stop is not None and stop < df["close"].iloc[-1]


def test_supertrend_downtrend():
    closes = [260 - i * 2 for i in range(80)]
    df = ohlc([[c + 1, c + 2, c - 1, c, 1000.0] for c in closes])
    trend, stop = supertrend(df, 20, 2.0)
    assert trend == -1
    assert stop is not None and stop > df["close"].iloc[-1]


def test_supertrend_rule_no_hit_on_steady_trend():
    up = ohlc([[c - 1, c + 1, c - 2, c, 1000.0] for c in (100 + i * 2 for i in range(80))])
    assert supertrend_rule(up, 20, 2.0) == []


def test_supertrend_rule_fires_on_flip():
    rows = []
    price = 100.0
    for _ in range(40):
        price += 2.0
        rows.append([price - 1, price + 1, price - 2, price, 1000.0])
    for _ in range(12):
        price -= 10.0
        rows.append([price, price + 1, price - 1, price, 1000.0])
    df = ohlc(rows)
    hits = supertrend_rule(df.iloc[:41], 20, 2.0)
    assert hits and "SHORT" in hits[0].detail


def test_volume_zscore_whale():
    df = flat(60)
    df.loc[59, "quote_volume"] = 50_000.0
    hits = volume_zscore(df, z_limit=3.0)
    assert hits and hits[0].rule == "volume_zscore"


def test_volume_zscore_normal_quiet():
    assert volume_zscore(flat(60), z_limit=3.0) == []


def test_absorption_tiny_body_big_volume():
    df = flat(60)
    df.loc[59] = [100.0, 105.0, 95.0, 100.2, 5_000.0]
    hits = absorption(df, body_pct=30.0, vol_mult=1.5)
    assert hits and hits[0].rule == "absorption"


def test_absorption_big_body_no_hit():
    df = flat(60)
    df.loc[59] = [100.0, 105.0, 95.0, 104.5, 5_000.0]
    assert absorption(df, body_pct=30.0, vol_mult=1.5) == []


def _pivot_low_df():
    # pivot_len=2 -> pivot index p = n-3 = 6. Dip 6'da, onay 7-8'de, retest son barda.
    lows = [99, 98, 99, 98, 99, 98, 90, 96, 90.5]
    closes = [100, 99, 100, 99, 100, 99, 92, 97, 94]
    rows = [[c, c + 1, lo, c, 1000.0] for c, lo in zip(closes, lows)]
    return ohlc(rows)


def test_ob_bull_retest():
    df = _pivot_low_df()
    hits = ob_retest(df, pivot_len=2, trend=1, atr=1.0)
    assert any(h.rule == "ob_retest" and "LONG" in h.detail for h in hits)


def test_ob_retest_requires_trend_alignment():
    assert ob_retest(_pivot_low_df(), pivot_len=2, trend=-1, atr=1.0) == []


def _pivot_high_df():
    highs = [101, 102, 101, 102, 101, 102, 112, 105, 111]
    closes = [100, 101, 100, 101, 100, 101, 110, 104, 108]
    rows = [[c, hi, c - 1, c, 1000.0] for c, hi in zip(closes, highs)]
    return ohlc(rows)


def test_ob_bear_retest():
    df = _pivot_high_df()
    hits = ob_retest(df, pivot_len=2, trend=-1, atr=1.0)
    assert any(h.rule == "ob_retest" and "SHORT" in h.detail for h in hits)
