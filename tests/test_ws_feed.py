import pytest

from src.bars import BarAggregator
from src.ws_feed import WsFeed, build_stream_urls


def test_build_stream_urls_splits_200():
    symbols = [f"S{i}USDT" for i in range(450)]
    urls = build_stream_urls(symbols)
    assert len(urls) == 3
    assert "s0usdt@aggTrade" in urls[0]
    assert urls[0].count("@aggTrade") == 200


@pytest.mark.asyncio
async def test_feed_routes_trades_to_aggregators():
    agg = BarAggregator("BTCUSDT", "5s")
    received: list = []

    class FakeWs:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if received:
                raise StopAsyncIteration
            received.append(1)
            return {"data": {"s": "BTCUSDT", "T": 1000, "p": "100", "q": "1"}}

        async def close(self):
            pass

    feed = WsFeed(["BTCUSDT"], ["5s"], on_bar=lambda b: None)
    feed.aggregators[("BTCUSDT", "5s")] = agg
    await feed._handle_ws(FakeWs())
    assert agg.last_open_ts == 0
