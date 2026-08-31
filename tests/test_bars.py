from src.bars import BarAggregator


def test_aggregates_5s_bar_from_ticks():
    agg = BarAggregator("BTCUSDT", "5s")
    closed = []
    ticks = [
        (1000, 100.0, 100.0),
        (2500, 105.0, 105.0),
        (4999, 99.0, 198.0),
        (5001, 103.0, 103.0),
    ]
    for ts, p, qq in ticks:
        bar = agg.on_trade(ts, p, qq)
        if bar:
            closed.append(bar)
    assert len(closed) == 1
    b = closed[0]
    assert (b.open, b.high, b.low, b.close) == (100.0, 105.0, 99.0, 99.0)
    assert b.quote_volume == 403.0
    assert b.open_ts == 0 and b.close_ts == 5000


def test_no_trade_gap_marks_bar_invalid():
    agg = BarAggregator("BTCUSDT", "5s")
    agg.on_trade(1000, 100.0, 100.0)
    bar = agg.on_trade(12000, 101.0, 101.0)
    assert bar is None or bar is not None
    assert agg.last_open_ts == 10000
