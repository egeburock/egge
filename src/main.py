import asyncio
import logging
import logging.handlers
from collections import deque
from pathlib import Path

import aiohttp
import uvicorn

from src.bars import bars_to_df
from src.config import load_config, tf_seconds
from src.db import Database
from src.engine import SignalEngine, direction_of
from src.klines import BinanceRest
from src.models import Bar
from src.notify import Notifier
from src import rules
from src.tracker import OutcomeTracker
from src.web import create_app
from src.ws_feed import WsFeed

POLL_CONCURRENCY = 10
SEC_HISTORY = 200
SYMBOL_REFRESH_S = 3600
OI_INTERVAL_S = 300
HTF_INTERVAL_S = 900


def setup_logging():
    Path("logs").mkdir(exist_ok=True)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        Path("logs") / "agent.log", when="midnight", backupCount=7, encoding="utf-8")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.StreamHandler(), file_handler])


setup_logging()
log = logging.getLogger("main")

POLL_CONCURRENCY = 10
SEC_HISTORY = 200


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
        self.sec_history: dict[tuple[str, str], deque] = {}
        self.oi_chg: dict[str, float] = {}
        self.oi_price_chg: dict[str, float] = {}
        self._prev_oi: dict[str, float] = {}
        self._prev_prices: dict[str, float] = {}
        self.htf_trend: dict[str, int] = {}

    def _apply_symbol_filter(self, symbols: list[str]) -> list[str]:
        if self.cfg["aggressive"]["enabled"]:
            return [s for s in symbols if s in self.cfg["aggressive"]["symbols"]]
        return symbols

    async def start(self):
        async with aiohttp.ClientSession() as session:
            self.rest.session = session
            self.symbols = self._apply_symbol_filter(await self.rest.exchange_info())
            log.info("Taranacak sembol: %d", len(self.symbols))
            sec_tfs = [t for t in self.cfg["timeframes"]["enabled"] if t.endswith("s")]
            feed = WsFeed(self.symbols, sec_tfs,
                          lambda bar: asyncio.create_task(self.on_bar_closed(bar)))
            tracker = OutcomeTracker(self.db, self.rest, self.cfg)
            tasks = [asyncio.create_task(feed.run()),
                     asyncio.create_task(self.minute_poller()),
                     asyncio.create_task(self.funding_poller()),
                     asyncio.create_task(self.oi_poller()),
                     asyncio.create_task(self.htf_poller()),
                     asyncio.create_task(self.symbol_refresher()),
                     asyncio.create_task(self.notifier.flush_loop()),
                     asyncio.create_task(tracker.run()),
                     asyncio.create_task(self.serve_dashboard(feed))]
            await asyncio.gather(*tasks)

    async def dispatch(self, sig):
        """Sinyal akışı: kaydet + bildir. PaperAgent bu metodu override eder."""
        self.db.save_signal(sig)
        await self.notifier.send(sig)

    async def on_bar_closed(self, bar: Bar):
        if bar.timeframe.endswith("s"):
            df = self._sec_df(bar)
        else:
            df = await self.bar_history(bar.symbol, bar.timeframe)
        if df is None or len(df) < 30:
            return
        for sig in self.evaluate_df(df, bar):
            await self.dispatch(sig)

    def _sec_df(self, bar: Bar):
        hist = self.sec_history.setdefault(
            (bar.symbol, bar.timeframe), deque(maxlen=SEC_HISTORY))
        if hist and bar.open_ts <= hist[-1].open_ts:
            return None
        hist.append(bar)
        return bars_to_df(hist)

    def evaluate_df(self, df, bar: Bar):
        c = self.cfg["signals"]
        atr = rules.atr_value(df)
        min_atr_pct = c.get("min_atr_pct", 0.0)
        if min_atr_pct > 0 and (atr is None or atr / bar.close * 100 < min_atr_pct):
            return []
        hits = self.collect_hits(df, bar.symbol)
        trend = self.htf_trend.get(bar.symbol)
        if c.get("use_htf_filter", False) and trend in (1, -1):
            want = "LONG" if trend == 1 else "SHORT"
            hits = [h for h in hits if direction_of(h) in (want, None)]
        return self.engine.evaluate(bar, hits, atr)

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
        if c.get("use_price_jump", True):
            hits += rules.price_jump(df, c["price_jump_pct"])
        trend, _ = rules.supertrend(df, c.get("supertrend_len", 20),
                                    c.get("supertrend_mult", 2.0))
        if c.get("use_supertrend", True):
            hits += rules.supertrend_rule(df, c.get("supertrend_len", 20),
                                          c.get("supertrend_mult", 2.0))
        if c.get("use_zscore_whale", True):
            hits += rules.volume_zscore(df, c.get("whale_z_limit", 3.0))
        if c.get("use_absorption", True):
            hits += rules.absorption(df, c.get("absorption_body_pct", 30.0),
                                     c.get("absorption_vol_mult", 1.5))
        if c.get("use_ob_retest", True):
            hits += rules.ob_retest(df, c.get("ob_pivot_len", 5), trend,
                                    rules.atr_value(df))
        if c.get("use_rsi2_pullback", False):
            hits += rules.rsi2_pullback(df, trend, c.get("rsi2_period", 2),
                                        c.get("rsi2_oversold", 10.0),
                                        c.get("rsi2_overbought", 90.0))
        if c.get("use_ema_pullback", False):
            hits += rules.ema_pullback(df, trend, c.get("ema_pullback_period", 21),
                                       c.get("ema_pullback_tol_pct", 0.1))
        if not rules.adx_ok(df, c["adx_min"]):
            hits = [h for h in hits if h.rule not in ("ema_cross", "macd_cross")]
        hits += rules.funding_rule(self.funding.get(symbol),
                                   c["funding_crowded"], c["funding_extreme_neg"])
        oi_pct = self.oi_chg.get(symbol)
        px_chg = self.oi_price_chg.get(symbol)
        if oi_pct is not None and px_chg is not None:
            hits += rules.oi_rule(oi_pct, px_chg)
        return hits

    async def minute_poller(self):
        minute_tfs = [t for t in self.cfg["timeframes"]["enabled"] if t.endswith("m")]
        sem = asyncio.Semaphore(POLL_CONCURRENCY)

        async def scan(sym: str, tf: str):
            async with sem:
                df = await self.bar_history(sym, tf)
            if df is None or df.empty:
                return
            last = df.iloc[-1]
            bar = Bar(sym, tf, int(last["ts"]), int(last["ts"]) + tf_seconds(tf) * 1000,
                      last["open"], last["high"], last["low"], last["close"],
                      last["quote_volume"])
            for sig in self.evaluate_df(df, bar):
                await self.dispatch(sig)

        while True:
            await asyncio.gather(*(scan(sym, tf) for tf in minute_tfs
                                   for sym in self.symbols))
            await asyncio.sleep(60)

    async def funding_poller(self):
        while True:
            try:
                self.funding = await self.rest.all_funding()
            except Exception as e:
                log.warning("funding oranları alınamadı: %s", e)
            await asyncio.sleep(60)

    def _apply_oi_snapshot(self, oi: dict[str, float], prices: dict[str, float]):
        self.oi_chg = {s: (v - self._prev_oi[s]) / self._prev_oi[s] * 100
                       for s, v in oi.items()
                       if self._prev_oi.get(s, 0) > 0}
        self.oi_price_chg = {s: (p - self._prev_prices[s]) / self._prev_prices[s] * 100
                             for s, p in prices.items()
                             if self._prev_prices.get(s, 0) > 0}
        self._prev_oi, self._prev_prices = oi, prices

    async def oi_poller(self):
        sem = asyncio.Semaphore(POLL_CONCURRENCY)
        while True:
            try:
                oi: dict[str, float] = {}
                prices = await self.rest.all_prices()

                async def fetch_oi(sym: str):
                    async with sem:
                        oi[sym] = await self.rest.open_interest(sym)

                await asyncio.gather(*(fetch_oi(s) for s in self.symbols),
                                     return_exceptions=True)
                self._apply_oi_snapshot(oi, prices)
                log.info("OI anlık görüntüsü: %d sembol", len(oi))
            except Exception as e:
                log.warning("OI güncellenemedi: %s", e)
            await asyncio.sleep(OI_INTERVAL_S)

    async def symbol_refresher(self):
        while True:
            await asyncio.sleep(SYMBOL_REFRESH_S)
            try:
                self.symbols = self._apply_symbol_filter(await self.rest.exchange_info())
                log.info("Sembol listesi yenilendi: %d sembol", len(self.symbols))
            except Exception as e:
                log.warning("sembol listesi yenilenemedi: %s", e)

    async def htf_poller(self):
        """Yüksek zaman dilimi Supertrend onayı (teyit katmanı)."""
        c = self.cfg["signals"]
        if not c.get("use_htf_filter", False):
            return
        tf = c.get("htf_timeframe", "1h")
        sem = asyncio.Semaphore(POLL_CONCURRENCY)
        while True:
            async def fetch_trend(sym: str):
                async with sem:
                    df = await self.rest.klines(sym, tf, limit=100)
                if df is None or len(df) < 25:
                    return
                trend, _ = rules.supertrend(df, c.get("supertrend_len", 20),
                                            c.get("supertrend_mult", 2.0))
                self.htf_trend[sym] = trend

            try:
                await asyncio.gather(*(fetch_trend(s) for s in self.symbols),
                                     return_exceptions=True)
                log.info("HTF trend (%s) güncellendi: %d sembol", tf, len(self.htf_trend))
            except Exception as e:
                log.warning("HTF trend güncellenemedi: %s", e)
            await asyncio.sleep(HTF_INTERVAL_S)

    async def serve_dashboard(self, feed):
        app = create_app(self.db, lambda: {"symbols": len(self.symbols),
                                           "ws": feed.connected})
        config = uvicorn.Config(app, host="127.0.0.1", port=self.cfg["web"]["port"])
        try:
            await uvicorn.Server(config).serve()
        except SystemExit:
            log.error("Dashboard başlatılamadı (port %d kullanımda olabilir) — "
                      "ajan taramaya devam ediyor", self.cfg["web"]["port"])


def main():
    cfg = load_config(Path("config.toml"))
    asyncio.run(Agent(cfg).start())


if __name__ == "__main__":
    main()
