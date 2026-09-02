import asyncio
import json
import types

import aiohttp
import pytest

from src.ws_feed import WsFeed, build_stream_urls


def ws_msg(payload: dict):
    return types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT,
                                 data=json.dumps(payload))


class FakeWs:
    def __init__(self, msgs):
        self._msgs = list(msgs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._msgs:
            raise StopAsyncIteration
        return self._msgs.pop(0)


def test_build_stream_urls_splits_200():
    symbols = [f"S{i}USDT" for i in range(450)]
    urls = build_stream_urls(symbols)
    assert len(urls) == 3
    assert "s0usdt@aggTrade" in urls[0]
    assert urls[0].count("@aggTrade") == 200


@pytest.mark.asyncio
async def test_feed_builds_bars_from_combined_stream():
    bars = []
    feed = WsFeed(["BTCUSDT"], ["5s"], on_bar=bars.append)
    msgs = [ws_msg({"stream": "btcusdt@aggTrade",
                    "data": {"s": "BTCUSDT", "T": t, "p": "100", "q": "1"}})
            for t in (1000, 5001)]
    msgs.append(types.SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=b"x"))
    await feed._handle_ws(FakeWs(msgs))
    assert len(bars) == 1
    assert bars[0].open_ts == 0 and bars[0].close_ts == 5000
    assert bars[0].quote_volume == 100.0


@pytest.mark.asyncio
async def test_unknown_symbol_ignored():
    bars = []
    feed = WsFeed(["BTCUSDT"], ["5s"], on_bar=bars.append)
    msg = ws_msg({"data": {"s": "ETHUSDT", "T": 1000, "p": "1", "q": "1"}})
    await feed._handle_ws(FakeWs([msg]))
    assert bars == []


def test_connected_reflects_open_connections():
    feed = WsFeed(["BTCUSDT"], ["5s"], on_bar=lambda b: None)
    assert not feed.connected
    feed._open_conns = 2
    assert feed.connected


@pytest.mark.asyncio
async def test_run_noop_without_second_timeframes():
    feed = WsFeed(["BTCUSDT"], ["1m"], on_bar=lambda b: None)
    await asyncio.wait_for(feed.run(), timeout=1)
