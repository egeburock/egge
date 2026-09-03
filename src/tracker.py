import asyncio
import logging
import time

log = logging.getLogger(__name__)


class OutcomeTracker:
    def __init__(self, db, rest, cfg: dict):
        self.db = db
        self.rest = rest
        t = cfg.get("tracking", {})
        self.horizon_ms = t.get("horizon_minutes", 30) * 60_000
        self.poll_s = t.get("poll_interval_s", 20)

    async def run(self):
        while True:
            try:
                await self.check_once()
            except Exception as e:
                log.warning("tracker hatası: %s", e)
            await asyncio.sleep(self.poll_s)

    async def check_once(self, now_ms: int | None = None):
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        rows = self.db.open_signals()
        pending = self.db.pending_orders()
        if not rows and not pending:
            return
        try:
            prices = await self.rest.all_prices()
        except Exception as e:
            log.warning("fiyatlar alınamadı: %s", e)
            return
        for row in pending:
            price = prices.get(row["symbol"])
            if price is None:
                continue
            self.check_fill(row, price, now)
        for row in self.db.open_signals():
            price = prices.get(row["symbol"])
            if price is None:
                continue
            self.resolve(row, price, now)

    def check_fill(self, row: dict, price: float, now_ms: int):
        """Limit emir dolumu; dolumda stop/hedef giriş fiyatına göre yeniden hizalanır."""
        lim = row["entry_limit"]
        d = row["direction"]
        filled = price <= lim if d == "LONG" else price >= lim
        if filled:
            risk = row["price"] - row["stop"] if d == "LONG" else row["stop"] - row["price"]
            tgt_off = (row["target"] - row["price"] if d == "LONG"
                       else row["price"] - row["target"])
            new_stop = lim - risk if d == "LONG" else lim + risk
            new_tgt = lim + tgt_off if d == "LONG" else lim - tgt_off
            self.db.activate_order(row["id"], lim, new_stop, new_tgt)
            log.info("limit doldu #%d %s %s @ %.6g", row["id"], row["symbol"],
                     d, lim)
            return
        if row["entry_deadline"] is not None and now_ms >= row["entry_deadline"]:
            self.db.close_signal(row["id"], "MISSED", price, now_ms, None)
            log.info("limit dolmadı #%d %s — iptal", row["id"], row["symbol"])

    def resolve(self, row: dict, price: float, now_ms: int):
        entry, stop, target = row["price"], row["stop"], row["target"]
        d = row["direction"]
        if stop is not None:
            hit_stop = price <= stop if d == "LONG" else price >= stop
            if hit_stop:
                self._close(row, "STOPPED", stop, now_ms)
                return
        if target is not None:
            hit_target = price >= target if d == "LONG" else price <= target
            if hit_target:
                self._close(row, "TARGET", target, now_ms)
                return
        if now_ms - row["ts"] >= self.horizon_ms:
            self._close(row, "EXPIRED", price, now_ms)

    def _close(self, row: dict, status: str, exit_price: float, now_ms: int):
        entry, stop = row["price"], row["stop"]
        risk = abs(entry - stop) if stop is not None else 0.0
        if risk > 0:
            r = ((exit_price - entry) / risk if row["direction"] == "LONG"
                 else (entry - exit_price) / risk)
        else:
            r = None
        self.db.close_signal(row["id"], status, exit_price, now_ms, r)
        log.info("sonuç #%d %s %s: %s @ %.6g (R=%s)", row["id"], row["symbol"],
                 row["direction"], status, exit_price,
                 f"{r:.2f}" if r is not None else "-")
