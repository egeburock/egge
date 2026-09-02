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
