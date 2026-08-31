import json

from src.bars import BarAggregator
from src.engine import SignalEngine

CFG = {"signals": {"threshold_long": 4.0, "threshold_short": 4.0,
                   "strong_threshold": 8.0, "min_quote_volume_usd": 0.0},
       "timeframes": {"cooldown_s": {"5s": 30}}}


def test_fixture_replay_produces_bars():
    agg = BarAggregator("BTCUSDT", "5s")
    bars = []
    with open("tests/fixtures/aggtrades.jsonl") as f:
        for line in f:
            d = json.loads(line)
            bar = agg.on_trade(int(d["T"]), float(d["p"]), 100.0)
            if bar:
                bars.append(bar)
    assert len(bars) >= 10
    assert all(b.close_ts - b.open_ts == 5000 for b in bars)


def test_engine_end_to_end_with_synthetic_spike():
    eng = SignalEngine(CFG)
    from src.models import Bar, RuleHit
    bar = Bar("BTCUSDT", "5s", 0, 5000, 100, 110, 99, 109, 1000.0)
    hits = [RuleHit("price_jump", "LONG: bar içi +9.0%", 2.0),
            RuleHit("volume_spike", "Hacim 4x", 2.0)]
    sigs = eng.evaluate(bar, hits, None, None)
    assert sigs and sigs[0].direction == "LONG" and sigs[0].score == 4.0
