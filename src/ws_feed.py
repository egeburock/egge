import asyncio
import logging

import aiohttp

from src.bars import BarAggregator

log = logging.getLogger(__name__)
WS_BASE = "wss://fstream.binance.com/stream?streams="


def build_stream_urls(symbols: list[str], chunk: int = 200) -> list[str]:
    return [WS_BASE + "/".join(f"{s.lower()}@aggTrade" for s in symbols[i:i + chunk])
            for i in range(0, len(symbols), chunk)]


class WsFeed:
    def __init__(self, symbols: list[str], timeframes: list[str], on_bar):
        self.symbols = symbols
        self.timeframes = [tf for tf in timeframes if tf.endswith("s")]
        self.on_bar = on_bar
        self.aggregators: dict[tuple[str, str], BarAggregator] = {
            (s, tf): BarAggregator(s, tf) for s in symbols for tf in self.timeframes}
        self.connected = False

    async def run(self):
        urls = build_stream_urls(self.symbols)
        backoff = 1
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(urls[0]) as ws:
                        self.connected = True
                        backoff = 1
                        await self._handle_ws(ws)
            except Exception as e:
                log.warning("WS hata: %s — %ss sonra yeniden", e, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _handle_ws(self, ws):
        async for msg in ws:
            data = msg.get("data", msg) if isinstance(msg, dict) else {}
            sym = data.get("s")
            if not sym:
                continue
            ts, price, qty = int(data["T"]), float(data["p"]), float(data["q"])
            for tf in self.timeframes:
                bar = self.aggregators[(sym, tf)].on_trade(ts, price, price * qty)
                if bar:
                    self.on_bar(bar)
