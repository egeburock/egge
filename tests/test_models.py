from src.models import Bar, RuleHit, Signal


def test_bar_fields():
    b = Bar(symbol="BTCUSDT", timeframe="5s", open_ts=0, close_ts=5,
            open=100.0, high=105.0, low=99.0, close=103.0, quote_volume=12000.0)
    assert b.close == 103.0 and b.symbol == "BTCUSDT"


def test_signal_contains_rule_hits():
    s = Signal(symbol="ETHUSDT", timeframe="1m", direction="LONG",
               strong=False, score=6.0, price=3000.0, stop=None, target=None, ts=0,
               hits=[RuleHit(rule="ema_cross", detail="EMA9>EMA21", score=3.0)])
    assert s.hits[0].rule == "ema_cross"
    assert s.direction in ("LONG", "SHORT")
