import asyncio
import json
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
        self._known = set(symbols)
        self._open_conns = 0

    @property
    def connected(self) -> bool:
        return self._open_conns > 0

    async def run(self):
        if not self.timeframes:
            return
        await asyncio.gather(*(self._run_one(url)
                               for url in build_stream_urls(self.symbols)))

    async def _run_one(self, url: str):
        backoff = 1
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url) as ws:
                        self._open_conns += 1
                        backoff = 1
                        try:
                            await self._handle_ws(ws)
                        finally:
                            self._open_conns -= 1
            except Exception as e:
                log.warning("WS hata: %s — %ss sonra yeniden", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _handle_ws(self, ws):
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            payload = json.loads(msg.data)
            data = payload.get("data", payload)
            sym = data.get("s")
            if not sym or sym not in self._known:
                continue
            ts, price, qty = int(data["T"]), float(data["p"]), float(data["q"])
            for tf in self.timeframes:
                bar = self.aggregators[(sym, tf)].on_trade(ts, price, price * qty)
                if bar:
                    self.on_bar(bar)
