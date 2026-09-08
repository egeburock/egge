"""Funding-carry backtest: ekstrem funding'ta market-nötr pozisyon.

Tüm USDT-M perpetual sembollerin funding geçmişi + 4h fiyat verisiyle:
- Her 4h sınırında funding'e göre sırala: en negatif N adedine LONG (short
  öder), en pozitif N adedine SHORT (long öder).
- Aralık getirisi = fiyat getirisi + funding tahakkuku − maliyet (tam
  devir varsayımı, her bacak için piyasa turu maliyeti).
- Kategorik olarak OOS'tur: t anındaki sıralama yalnız geçmiş funding'i
  kullanır. Aynı pencerede N ve eşik duyarlılığı raporlanır.
"""
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import optimize_rr as opt
except ImportError:
    from scripts import optimize_rr as opt

import aiohttp
import numpy as np

from src.config import load_config
from src.klines import BinanceRest
from src.paper_costs import MARKET_RT_COST_PCT

DAYS = 30
INTERVAL_MS = 4 * 3_600_000
COST_LEG = MARKET_RT_COST_PCT  # 0.08% tam tur, bacak başına her aralık
NS = [5, 10, 20]
THRESHOLDS = [0.0, 0.0002, 0.0005]


async def fetch_funding(rest, sym, since_ms):
    key = f"{sym}_fund"
    cached = opt._cache_get(sym, "fund")
    if cached is not None and "ts" in cached.columns:
        return cached
    try:
        raw = await rest._json("/fapi/v1/fundingRate",
                               {"symbol": sym, "limit": 1000})
    except Exception:
        return None
    if not raw:
        return None
    rows = [(int(e["fundingTime"]), float(e["fundingRate"])) for e in raw
            if int(e["fundingTime"]) >= since_ms]
    df = opt.pd.DataFrame(rows, columns=["ts", "rate"])
    opt._cache_put(sym, "fund", df)
    return df


async def fetch_4h(rest, sym, since_ms):
    cached = opt._cache_get(sym, "4h_carry")
    if cached is not None:
        return cached
    try:
        raw = await rest._json("/fapi/v1/klines",
                               {"symbol": sym, "interval": "4h",
                                "startTime": since_ms, "limit": 200})
    except Exception:
        return None
    if not raw:
        return None
    df = opt.parse_klines(raw, sym, "4h")
    opt._cache_put(sym, "4h_carry", df)
    return df


async def main():
    since_ms = int(time.time() * 1000) - DAYS * 86_400_000
    async with aiohttp.ClientSession() as session:
        rest = BinanceRest(session)
        symbols = await rest.exchange_info()
        print(f"{len(symbols)} sembol — veri çekiliyor (funding + 4h klines)...")

        sem = asyncio.Semaphore(10)

        async def grab(sym):
            async with sem:
                f = await fetch_funding(rest, sym, since_ms)
                k = await fetch_4h(rest, sym, since_ms)
            return sym, f, k

        results = await asyncio.gather(*(grab(s) for s in symbols),
                                       return_exceptions=True)

    universe = {}
    for r in results:
        if isinstance(r, Exception) or r is None:
            continue
        sym, f, k = r
        if f is None or k is None or len(k) < 8 or len(f) < 3:
            continue
        universe[sym] = (f, k)
    print(f"yeterli veri: {len(universe)} sembol")

    closes = {s: dict(zip(df["ts"].to_numpy(), df["close"].to_numpy()))
              for s, (_, df) in universe.items()}
    t0 = min(df["ts"].min() for _, df in universe.values())
    t1 = max(df["ts"].max() for _, df in universe.values())
    grid = list(range((t0 // INTERVAL_MS + 1) * INTERVAL_MS, t1, INTERVAL_MS))

    # funding olaylarını sembol -> zaman sıralı listeler
    fund_events = {}
    for s, (f, _) in universe.items():
        fund_events[s] = sorted(zip(f["ts"].to_numpy(), f["rate"].to_numpy()))

    def latest_rate(sym, t):
        """t anında bilinen son funding oranı (7 gün içinde)."""
        evs = fund_events.get(sym)
        if not evs:
            return None
        ts_list = [ts for ts, _ in evs]
        lo, hi = 0, len(evs) - 1
        if ts_list[-1] <= t - 3 * INTERVAL_MS * 2:
            return None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if ts_list[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        return evs[lo][1]

    def interval_carry(sym, t):
        """(t, t+INTERVAL] içindeki funding olaylarının toplamı."""
        evs = fund_events.get(sym, [])
        return sum(r for ts, r in evs if t < ts <= t + INTERVAL_MS)

    print(f"\n{'N':>3} {'eşik':>7} {'aralık':>9} {'gün':>7} {'WR':>6} {'işlem/adet':>10}")

    def variant(mode, n, thr):
        pnls = []
        for t in grid:
            rates = {}
            for s in universe:
                r = latest_rate(s, t)
                if r is not None and abs(r) >= thr:
                    rates[s] = r
            if len(rates) < 2 * n:
                continue

            def ret24(s):
                c0, c6 = closes[s].get(t), closes[s].get(t - 6 * INTERVAL_MS)
                if c0 is None or c6 is None or c6 <= 0:
                    return 0.0
                return c0 / c6 - 1

            momo = {s: ret24(s) for s in rates}

            def ret(s):
                c0, c1 = closes[s].get(t), closes[s].get(t + INTERVAL_MS)
                if c0 is None or c1 is None or c0 <= 0:
                    return 0.0
                return (c1 - c0) / c0

            if mode == "carry":
                ranked = sorted(rates.items(), key=lambda kv: kv[1])
                longs = [s for s, _ in ranked[:n]]
                shorts = [s for s, _ in ranked[-n:]]
            else:  # momentum-aligned
                cand_l = sorted(((s, m) for s, m in momo.items() if rates[s] >= 0),
                                key=lambda kv: -kv[1])[:n]
                cand_s = sorted(((s, m) for s, m in momo.items() if rates[s] <= 0),
                                key=lambda kv: kv[1])[:n]
                longs = [s for s, _ in cand_l]
                shorts = [s for s, _ in cand_s]

            carry_l = np.mean([interval_carry(s, t) for s in longs]) if longs else 0.0
            carry_s = np.mean([interval_carry(s, t) for s in shorts]) if shorts else 0.0
            price_l = np.mean([ret(s) for s in longs]) if longs else 0.0
            price_s = np.mean([ret(s) for s in shorts]) if shorts else 0.0
            if mode == "carry":
                pnl = ((price_l - price_s) / 2
                       + (carry_l + carry_s) / 2
                       - COST_LEG)
            else:  # long pozitif funding öder, short negatif funding alır
                pnl = ((price_l - price_s) / 2
                       + (carry_s - carry_l) / 2
                       - COST_LEG)
            pnls.append(pnl)
        return np.array(pnls) if pnls else None

    for mode in ("carry", "momentum"):
        print(f"\n[{mode}]")
        for n in NS:
            for thr in THRESHOLDS:
                arr = variant(mode, n, thr)
                if arr is None:
                    print(f"{n:>3} {thr:>7.4%}  veri yok")
                    continue
                wr = np.mean(arr > 0)
                per_day = arr.mean() * 6
                print(f"{n:>3} {thr:>7.2%} {arr.mean():>+9.4%} {per_day:>+7.4%} "
                      f"{wr:>6.0%} {len(arr):>10}")

    print("\nNot: maliyet modeli tam devir varsayımlı (muhafazakar); "
          "carry pozitifse canlı paper bot ile doğrulanır.")


if __name__ == "__main__":
    asyncio.run(main())
