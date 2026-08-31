from src.engine import SignalEngine
from src.models import Bar, RuleHit


def make_engine(threshold_long=5.0, threshold_short=5.0, cooldown_s=60):
    cfg = {"signals": {"threshold_long": threshold_long,
                       "threshold_short": threshold_short,
                       "strong_threshold": 8.0},
           "timeframes": {"cooldown_s": {"1m": cooldown_s}}}
    return SignalEngine(cfg)


def test_signal_emitted_above_threshold():
    eng = make_engine()
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    sigs = eng.evaluate(bar, hits, funding_rate=None, oi_pct=None)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.direction == "LONG" and s.score == 5.0 and not s.strong


def test_below_threshold_no_signal():
    eng = make_engine()
    hits = [RuleHit("funding", "SHORT eğilimi", 1.0)]
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    assert eng.evaluate(bar, hits, None, None) == []


def test_direction_conflict_resolves_to_majority():
    eng = make_engine()
    hits = [RuleHit("ema_cross", "LONG: x", 3.0),
            RuleHit("price_jump", "SHORT: -3%", 2.0),
            RuleHit("macd_cross", "SHORT: y", 2.0),
            RuleHit("funding", "SHORT eğilimi", 1.0)]
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    sigs = eng.evaluate(bar, hits, None, None)
    assert sigs and sigs[0].direction == "SHORT" and sigs[0].score == 5.0


def test_cooldown_suppresses_repeat():
    eng = make_engine(cooldown_s=60)
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    bar1 = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    bar2 = Bar("BTCUSDT", "1m", 60000, 120000, 103, 106, 102, 105, 90000.0)
    assert eng.evaluate(bar1, hits, None, None)
    assert eng.evaluate(bar2, hits, None, None) == []


def test_strong_signal():
    eng = make_engine()
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("macd_cross", "LONG: y", 2.0),
            RuleHit("volume_spike", "4x", 2.0), RuleHit("rsi_reversal", "LONG: z", 2.0)]
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    s = eng.evaluate(bar, hits, None, None)[0]
    assert s.strong and s.score == 9.0


def test_low_volume_skipped():
    eng = make_engine()
    eng.min_quote_volume = 50000.0
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    bar = Bar("SHIBCOIN", "1m", 0, 60000, 1, 1.1, 0.9, 1.05, 10.0)
    assert eng.evaluate(bar, hits, None, None) == []


def test_directional_thresholds():
    eng = make_engine(threshold_long=6.0, threshold_short=4.0)
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    short_hits = [RuleHit("ema_cross", "SHORT: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    sigs = eng.evaluate(bar, short_hits, None, None)
    assert sigs and sigs[0].direction == "SHORT" and sigs[0].score == 5.0
    long_hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    assert eng.evaluate(bar, long_hits, None, None) == []
