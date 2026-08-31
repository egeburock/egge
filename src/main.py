import asyncio
import logging
from pathlib import Path

import aiohttp
import uvicorn

from src.config import load_config, tf_seconds
from src.db import Database
from src.engine import SignalEngine
from src.klines import BinanceRest
from src.models import Bar
from src.notify import Notifier
from src import rules
from src.web import create_app
from src.ws_feed import WsFeed

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")


class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.db = Database("signals.db")
        self.engine = SignalEngine(cfg)
        self.notifier = Notifier(self.db, cfg["telegram"]["token"],
                                 cfg["telegram"]["chat_id"], cfg["agent"]["dry_run"])
        self.symbols: list[str] = []
        self.rest = BinanceRest(None)
        self.funding: dict[str, float | None] = {}

    async def start(self):
        async with aiohttp.ClientSession() as session:
            self.rest.session = session
            self.symbols = await self.rest.exchange_info()
            if self.cfg["aggressive"]["enabled"]:
                self.symbols = [s for s in self.symbols
                                if s in self.cfg["aggressive"]["symbols"]]
            log.info("Taranacak sembol: %d", len(self.symbols))
            engine, notifier = self.engine, self.notifier

            async def on_bar_async(bar):
                df = await self.bar_history(bar.symbol, bar.timeframe)
                if df is None or len(df) < 30:
                    return
                hits = self.collect_hits(df, bar.symbol)
                atr = rules.atr_value(df)
                for sig in engine.evaluate(bar, hits,
                                           self.funding.get(bar.symbol), None, atr):
                    self.db.save_signal(sig)
                    await notifier.send(sig)

            def on_bar(bar):
                asyncio.create_task(on_bar_async(bar))

            sec_tfs = [t for t in self.cfg["timeframes"]["enabled"] if t.endswith("s")]
            feed = WsFeed(self.symbols, sec_tfs, on_bar)
            tasks = [asyncio.create_task(feed.run()),
                     asyncio.create_task(self.minute_poller(on_bar_async)),
                     asyncio.create_task(self.funding_poller()),
                     asyncio.create_task(self.serve_dashboard(feed))]
            await asyncio.gather(*tasks)

    async def bar_history(self, symbol: str, tf: str):
        if tf.endswith("s"):
            return None
        try:
            return await self.rest.klines(symbol, tf, limit=200)
        except Exception as e:
            log.warning("kline hatası %s %s: %s", symbol, tf, e)
            return None

    def collect_hits(self, df, symbol: str):
        c = self.cfg["signals"]
        hits = []
        hits += rules.ema_cross(df, c["ema_fast"], c["ema_slow"])
        hits += rules.rsi_reversal(df, c["rsi_period"], c["rsi_oversold"], c["rsi_overbought"])
        hits += rules.macd_cross(df)
        hits += rules.volume_spike(df, c["volume_spike_x"], c["volume_avg_bars"])
        hits += rules.price_jump(df, c["price_jump_pct"])
        if not rules.adx_ok(df, c["adx_min"]):
            hits = [h for h in hits if h.rule not in ("ema_cross", "macd_cross")]
        hits += rules.funding_rule(self.funding.get(symbol),
                                   c["funding_crowded"], c["funding_extreme_neg"])
        return hits

    async def minute_poller(self, on_bar_async):
        minute_tfs = [t for t in self.cfg["timeframes"]["enabled"] if t.endswith("m")]
        while True:
            for tf in minute_tfs:
                for sym in self.symbols:
                    df = await self.bar_history(sym, tf)
                    if df is None or df.empty:
                        continue
                    last = df.iloc[-1]
                    bar = Bar(sym, tf, int(last["ts"]), int(last["ts"]) + tf_seconds(tf) * 1000,
                              last["open"], last["high"], last["low"], last["close"],
                              last["quote_volume"])
                    hits = self.collect_hits(df, sym)
                    atr = rules.atr_value(df)
                    for sig in self.engine.evaluate(bar, hits, self.funding.get(sym), None, atr):
                        self.db.save_signal(sig)
                        await self.notifier.send(sig)
            await asyncio.sleep(60)

    async def funding_poller(self):
        while True:
            for sym in self.symbols[:50]:
                try:
                    self.funding[sym] = await self.rest.premium_index(sym)
                except Exception:
                    pass
            await asyncio.sleep(60)

    async def serve_dashboard(self, feed):
        app = create_app(self.db, lambda: {"symbols": len(self.symbols),
                                           "ws": feed.connected})
        config = uvicorn.Config(app, host="127.0.0.1", port=self.cfg["web"]["port"])
        await uvicorn.Server(config).serve()


def main():
    cfg = load_config(Path("config.toml"))
    asyncio.run(Agent(cfg).start())


if __name__ == "__main__":
    main()
