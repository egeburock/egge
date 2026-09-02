from pathlib import Path

import pytest

from src.config import load_config
from src.main import Agent
from src.models import Bar


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
    return Agent(cfg)


def sec_bar(open_ts: int, close=100.0, vol=60_000.0):
    return Bar("BTCUSDT", "5s", open_ts, open_ts + 5000,
               close, close + 1, close - 1, close, vol)


def test_sec_df_accumulates_history(agent):
    df1 = agent._sec_df(sec_bar(0))
    assert df1 is not None and len(df1) == 1
    df2 = agent._sec_df(sec_bar(5000))
    assert len(df2) == 2


def test_sec_df_rejects_stale_and_duplicate(agent):
    agent._sec_df(sec_bar(0))
    agent._sec_df(sec_bar(5000))
    assert agent._sec_df(sec_bar(5000)) is None
    assert agent._sec_df(sec_bar(0)) is None
    assert len(agent.sec_history[("BTCUSDT", "5s")]) == 2


@pytest.mark.asyncio
async def test_on_bar_closed_second_tf_needs_no_rest(agent):
    for i in range(31):
        await agent.on_bar_closed(sec_bar(i * 5000))
    assert len(agent.sec_history[("BTCUSDT", "5s")]) == 31


@pytest.mark.asyncio
async def test_on_bar_closed_emits_signal_on_spike(agent):
    agent.engine.long_threshold = 4.0
    base = 100.0
    for i in range(30):
        await agent.on_bar_closed(sec_bar(i * 5000, close=base))
    spike = Bar("BTCUSDT", "5s", 150000, 155000, base, base * 1.05,
                base, base * 1.045, 500_000.0)
    await agent.on_bar_closed(spike)
    rows = agent.db.recent_signals(limit=10)
    assert rows and rows[0]["direction"] == "LONG"


def test_oi_snapshot_diffs(agent):
    agent._apply_oi_snapshot({"BTCUSDT": 100.0}, {"BTCUSDT": 50.0})
    assert agent.oi_chg == {} and agent.oi_price_chg == {}
    agent._apply_oi_snapshot({"BTCUSDT": 103.0}, {"BTCUSDT": 50.5})
    assert abs(agent.oi_chg["BTCUSDT"] - 3.0) < 1e-9
    assert abs(agent.oi_price_chg["BTCUSDT"] - 1.0) < 1e-9


def test_collect_hits_includes_oi_confirmation(agent):
    agent._apply_oi_snapshot({"BTCUSDT": 100.0}, {"BTCUSDT": 100.0})
    agent._apply_oi_snapshot({"BTCUSDT": 102.0}, {"BTCUSDT": 101.0})
    import pandas as pd
    df = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                       "close": [100.5], "quote_volume": [90000.0]})
    rules = [h.rule for h in agent.collect_hits(df, "BTCUSDT")]
    assert "oi_confirm" in rules


def test_evaluate_df_htf_filter(agent):
    import pandas as pd
    from src.models import RuleHit
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    df = pd.DataFrame({"open": [100.0], "high": [105.0], "low": [99.0],
                       "close": [103.0], "quote_volume": [90000.0]})
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    agent.collect_hits = lambda d, s: list(hits)
    agent.engine.long_threshold = 4.0
    agent.cfg["signals"]["use_htf_filter"] = True

    agent.htf_trend["BTCUSDT"] = 1
    assert agent.evaluate_df(df, bar)

    agent.engine._last.clear()
    agent.htf_trend["BTCUSDT"] = -1
    assert not agent.evaluate_df(df, bar)

    agent.engine._last.clear()
    agent.cfg["signals"]["use_htf_filter"] = False
    assert agent.evaluate_df(df, bar)
