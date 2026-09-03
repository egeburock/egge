"""$50 sanal sermayeyle canlı piyasada paper-trading botu.

Sinyal üretimi canlı ajanla birebir aynıdır (3m, HTF teyidi, limit giriş);
PaperBroker gerçek maliyet muhasebesi yapar:
- fee: giriş maker %0.02, çıkış hedefte maker / stop-sürede taker %0.04
- slippage: her taker dolumda 1 bps
- funding: 8 saatlik sınır (00/08/16 UTC) kesişen açık pozisyonlar öder/alır

Gerçek emir GÖNDERİLMEZ — API anahtarı gerekmez. Amaç: stratejinin $50
üzerinde fee/funding/slippage dahil gerçek net sonucunu ölçmek.

Çalıştırma: python -m src.paper
"""
import asyncio
import logging
import time
from pathlib import Path

import aiohttp
import uvicorn

from src.config import load_config, tf_seconds
from src.db import Database
from src.klines import BinanceBanError, BinanceRest
from src.main import Agent
from src.paper_costs import MAKER_FEE, SLIPPAGE_BPS, TAKER_FEE
from src.web import create_app
from src.ws_feed import WsFeed

log = logging.getLogger("paper")

FUNDING_INTERVAL_MS = 8 * 3_600_000  # 00/08/16 UTC
POLL_S = 20


class PaperBroker:
    def __init__(self, db: Database, rest, cfg: dict):
        self.db = db
        self.rest = rest
        p = cfg.get("paper", {})
        self.start_equity = float(p.get("start_equity", 50.0))
        self.risk_pct = float(p.get("risk_pct", 0.02))
        self.per_trade_cap_x = float(p.get("per_trade_cap_x", 1.5))
        self.portfolio_cap_x = float(p.get("portfolio_cap_x", 3.0))
        self.min_notional = float(p.get("min_notional", 5.0))
        self.max_positions = int(p.get("max_positions", 5))
        self.horizon_ms = cfg.get("tracking", {}).get("horizon_minutes", 30) * 60_000
        self.entry_window_bars = int(cfg["signals"].get("entry_window_bars", 8))
        self.funding: dict[str, float] = {}
        self._last_funding_ms: int | None = None
        saved = db.get_paper_state("equity")
        self.equity = float(saved) if saved is not None else self.start_equity

    # --- emir açma -------------------------------------------------------
    def on_signal(self, sig) -> int | None:
        if self.equity < self.min_notional:
            log.warning("[PAPER] sermaye $%.2f — yeni işlem açılmıyor", self.equity)
            return None
        if sig.entry_limit is None or not sig.stop or not sig.target:
            return None
        live = self.db.paper_positions() + self.db.paper_orders()
        if len(live) >= self.max_positions:
            return None
        if any(t["symbol"] == sig.symbol for t in live):
            return None
        if sum(t["notional"] for t in self.db.paper_positions()) \
                >= self.equity * self.portfolio_cap_x:
            return None
        risk_amt = self.equity * self.risk_pct
        stop_pct = abs(sig.price - sig.stop) / sig.price
        if stop_pct <= 0:
            return None
        notional = min(risk_amt / stop_pct, self.equity * self.per_trade_cap_x)
        if notional < self.min_notional:
            return None
        window_ms = self.entry_window_bars * tf_seconds(sig.timeframe) * 1000
        deadline = sig.ts + window_ms
        trade_id = self.db.save_paper_trade(sig.symbol, sig.direction, sig.ts,
                                            sig.price, notional, sig.stop,
                                            sig.target, deadline, sig.entry_limit)
        log.info("[PAPER] emir #%d %s %s skor %.1f notional $%.2f limit %.6g",
                 trade_id, sig.symbol, sig.direction, sig.score, notional,
                 sig.entry_limit)
        return trade_id

    # --- fiyat akışı -----------------------------------------------------
    async def run(self):
        while True:
            try:
                await self.step()
                await asyncio.sleep(POLL_S)
            except BinanceBanError as e:
                log.warning("%s — 60 sn bekleniyor", e)
                await asyncio.sleep(60)
            except Exception as e:
                log.warning("paper step hatası: %s", e)
                await asyncio.sleep(POLL_S)

    async def step(self):
        prices = await self.rest.all_prices()
        now = int(time.time() * 1000)
        prev_funding = self._last_funding_ms
        self._last_funding_ms = now
        for order in self.db.paper_orders():
            price = prices.get(order["symbol"])
            if price is not None:
                await self.check_fill(order, price, now)
        for pos in self.db.paper_positions():
            price = prices.get(pos["symbol"])
            if price is None:
                continue
            self.apply_funding(pos, prev_funding, now)
            self.manage_position(pos, price, now)

    async def _recent_range(self, symbol: str) -> tuple[float, float] | None:
        """Son 1m mumunun low/high'ı (backtest'teki bar-içi dolum paritesi)."""
        try:
            raw = await self.rest._json("/fapi/v1/klines",
                                        {"symbol": symbol, "interval": "1m",
                                         "limit": 1})
            return float(raw[0][3]), float(raw[0][2])
        except Exception:
            return None

    async def check_fill(self, order: dict, price: float, now: int):
        lim = order["entry_limit"]
        d = order["direction"]
        rng = await self._recent_range(order["symbol"])
        if rng is not None:
            low, high = rng
            touched = low <= lim if d == "LONG" else high >= lim
        else:
            touched = price <= lim if d == "LONG" else price >= lim
        if touched:
            self.db.fill_paper_order(order["id"], lim)
            log.info("[PAPER] doldu #%d %s %s @ %.6g", order["id"],
                     order["symbol"], d, lim)
            return
        if now >= order["deadline_ts"]:
            self.db.close_paper_trade(order["id"], "MISSED", now, price,
                                      0.0, 0.0, 0.0, 0.0, self.equity)
            log.info("[PAPER] dolmadı #%d %s — iptal", order["id"], order["symbol"])

    def apply_funding(self, pos: dict, prev_ms: int | None, now: int):
        """Kesişen 8s sınırlarında funding ödemesi (pozitif rate: long öder)."""
        if prev_ms is None:
            return
        start = max(prev_ms, pos["entry_ts"])
        first = (start // FUNDING_INTERVAL_MS + 1) * FUNDING_INTERVAL_MS
        rate = self.funding.get(pos["symbol"], 0.0) or 0.0
        sign = 1.0 if pos["direction"] == "LONG" else -1.0
        total = 0.0
        for _b in range(first, now + 1, FUNDING_INTERVAL_MS):
            total += -rate * pos["notional"] * sign
        if total:
            self.db.add_paper_funding(pos["id"], total)
            log.info("[PAPER] funding #%d %s: $%+.4f (rate %.5f)",
                     pos["id"], pos["symbol"], total, rate)

    def manage_position(self, pos: dict, price: float, now: int):
        d = pos["direction"]
        sign = 1.0 if d == "LONG" else -1.0
        notional = pos["notional"]
        entry = pos["entry_price"]
        stop_hit = (price <= pos["stop"]) if d == "LONG" else (price >= pos["stop"])
        if stop_hit:
            gross = sign * (pos["stop"] - entry) / entry * notional
            fees = notional * (MAKER_FEE + TAKER_FEE + SLIPPAGE_BPS / 10000)
            self._close(pos, "STOP", pos["stop"], now, gross, fees)
            return
        target_hit = (price >= pos["target"]) if d == "LONG" else (price <= pos["target"])
        if target_hit:
            gross = sign * (pos["target"] - entry) / entry * notional
            fees = notional * (MAKER_FEE + MAKER_FEE)
            self._close(pos, "TARGET", pos["target"], now, gross, fees)
            return
        if now - pos["entry_ts"] >= self.horizon_ms:
            gross = sign * (price - entry) / entry * notional
            fees = notional * (MAKER_FEE + TAKER_FEE + SLIPPAGE_BPS / 10000)
            self._close(pos, "EXPIRED", price, now, gross, fees)

    def _close(self, pos: dict, status: str, exit_price: float, now: int,
               gross: float, fees: float):
        funding = float(pos.get("funding") or 0.0)
        net = gross - fees + funding
        self.equity += net
        self.db.set_paper_state("equity", f"{self.equity:.6f}")
        self.db.close_paper_trade(pos["id"], status, now, exit_price, fees,
                                  funding, gross, net, self.equity)
        log.info("[PAPER] kapandı #%d %s %s %s @ %.6g | net $%+.3f | sermaye $%.2f",
                 pos["id"], pos["symbol"], pos["direction"], status, exit_price,
                 net, self.equity)


class PaperAgent(Agent):
    """Ajanın sinyal hattını kullanır; sonuçları PaperBroker'a yönlendirir."""

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.broker = PaperBroker(self.db, self.rest, cfg)
        self.broker.funding = self.funding

    async def dispatch(self, sig):
        self.db.save_signal(sig)
        self.broker.on_signal(sig)

    async def start(self):
        async with aiohttp.ClientSession() as session:
            self.rest.session = session
            self.symbols = await self._load_symbols()
            log.info("[PAPER] sembol: %d | sermaye $%.2f | risk/işlem %.1f%%",
                     len(self.symbols), self.broker.equity,
                     self.broker.risk_pct * 100)
            feed = WsFeed(self.symbols, [], lambda b: None)
            tasks = [asyncio.create_task(self.minute_poller()),
                     asyncio.create_task(self.funding_poller()),
                     asyncio.create_task(self.oi_poller()),
                     asyncio.create_task(self.htf_poller()),
                     asyncio.create_task(self.symbol_refresher()),
                     asyncio.create_task(self.broker.run()),
                     asyncio.create_task(self.serve_dashboard(feed))]
            await asyncio.gather(*tasks)

    async def serve_dashboard(self, feed):
        app = create_app(self.db,
                         lambda: {"symbols": len(self.symbols), "ws": feed.connected},
                         paper_provider=self.paper_status)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.cfg["web"]["port"])
        try:
            await uvicorn.Server(config).serve()
        except SystemExit:
            log.error("Dashboard başlatılamadı (port %d kullanımda olabilir)",
                      self.cfg["web"]["port"])

    def paper_status(self) -> dict:
        return {
            "equity": round(self.broker.equity, 2),
            "start_equity": self.broker.start_equity,
            "realized_net": round(self.db.paper_net_total(), 4),
            "open_positions": len(self.db.paper_positions()),
            "pending_orders": len(self.db.paper_orders()),
            "positions": self.db.paper_positions(),
            "orders": self.db.paper_orders(),
            "trades": self.db.paper_history(20),
        }


def main():
    cfg = load_config(Path("config.toml"))
    asyncio.run(PaperAgent(cfg).start())


if __name__ == "__main__":
    main()
