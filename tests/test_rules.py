import pandas as pd

from src.rules import ema_cross, price_jump, rsi_reversal, volume_spike


def ohlc(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "quote_volume"])


def test_ema_cross_long():
    closes = [100 - i for i in range(30)] + [100 + i * 5 for i in range(1, 3)]
    df = ohlc([[c, c + 1, c - 1, c, 1000.0] for c in closes])
    hits = ema_cross(df, fast=9, slow=21)
    assert any(h.rule == "ema_cross" and "LONG" in h.detail for h in hits)


def test_ema_cross_short():
    closes = [100 + i for i in range(30)] + [100 - i * 5 for i in range(1, 3)]
    df = ohlc([[c, c + 1, c - 1, c, 1000.0] for c in closes])
    hits = ema_cross(df, fast=9, slow=21)
    assert any("SHORT" in h.detail for h in hits)


def test_rsi_reversal_long():
    closes = [100 - i * 2 for i in range(25)] + [60, 68]
    df = ohlc([[c, c + 1, c - 1, c, 1000.0] for c in closes])
    hits = rsi_reversal(df, period=14, oversold=30, overbought=70)
    assert any(h.rule == "rsi_reversal" and "LONG" in h.detail for h in hits)


def test_volume_spike():
    rows = [[100, 101, 99, 100, 1000.0] for _ in range(20)]
    rows.append([100, 105, 99, 104, 5000.0])
    hits = volume_spike(ohlc(rows), mult=3.0, lookback=20)
    assert hits and hits[0].rule == "volume_spike"


def test_price_jump_bidirectional():
    rows = [[100, 101, 99, 100, 1000.0] for _ in range(5)]
    up = ohlc(rows + [[100, 104, 100, 103, 2000.0]])
    down = ohlc(rows + [[100, 100, 96, 97, 2000.0]])
    hu, hd = price_jump(up, 2.0), price_jump(down, 2.0)
    assert hu and "LONG" in hu[0].detail
    assert hd and "SHORT" in hd[0].detail
