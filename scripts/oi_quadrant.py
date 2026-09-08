"""OI çeyrek backtest: açık pozisyon değişimi × fiyat değişimi sinyalleri.

Klasik türev oyun kitabı — 4 çeyrek (24h bakış):
  OI↑ px↑  -> yeni longlar giriyor        (süreklilik: LONG)
  OI↑ px↓  -> yeni shortlar giriyor       (süreklilik: SHORT)
  OI↓ px↑  -> short squeeze               (tükenme:  SHORT)
  OI↓ px↓  -> long tasfiyesi              (tükenme:  LONG)
Ters haritalama da test edilir. Maliyet: 0.08% tam tur / 24h işlem.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import optimize_rr as opt
except ImportError:
    from scripts import optimize_rr as opt

import aiohttp
import numpy as np

from src.klines import BinanceRest
from src.paper_costs import MARKET_RT_COST_PCT

DAYS = 30
STEP_MS = 4 * 3_600_000       # 4h OI örnek aralığı
LOOK_MS = 6 * STEP_MS         # 24h bakış
HOLD_MS = 6 * STEP_MS         # 24h tutuş
COST = MARKET_RT_COST_PCT


async def fetch_oi(rest, sym, since_ms):
    cached = opt._cache_get(sym, "oi4h")
    if cached is not None and "ts" in cached.columns:
        return cached
    try:
        raw = await rest._json("/futures/data/openInterestHist",
                               {"symbol": sym, "period": "4h", "limit": 500})
    except Exception:
        return None
    if not raw:
        return None
    rows = [(int(e["timestamp"]), float(e["sumOpenInterest"]))
            for e in raw if int(e["timestamp"]) >= since_ms]
    if len(rows) < 10:
        return None
    df = opt.pd.DataFrame(rows, columns=["ts", "oi"]).sort_values("ts")
    opt._cache_put(sym, "oi4h", df)
    return df


async def main():
    since_ms = int(time.time() * 1000) - DAYS * 86_400_000
    async with aiohttp.ClientSession() as session:
        rest = BinanceRest(session)
        symbols = await rest.exchange_info()
        print(f"{len(symbols)} sembol — OI geçmişi çekiliyor...")
        sem = asyncio.Semaphore(10)

        async def grab(sym):
            async with sem:
                return sym, await fetch_oi(rest, sym, since_ms)

        res = await asyncio.gather(*(grab(s) for s in symbols),
                                   return_exceptions=True)

    universe = {}
    for r in res:
        if isinstance(r, Exception) or r is None:
            continue
        sym, oi = r
        k = opt._cache_get(sym, "4h_carry")
        if k is None or oi is None:
            continue
        closes = dict(zip(k["ts"].to_numpy(), k["close"].to_numpy()))
        universe[sym] = (dict(zip(oi["ts"].to_numpy(), oi["oi"].to_numpy())),
                         closes)
    print(f"yeterli veri: {len(universe)} sembol")

    t0 = min(min(t for t in oi if t >= since_ms + LOOK_MS) for oi, _ in universe.values())
    t1 = max(max(oi) for oi, _ in universe.values()) - HOLD_MS
    grid = list(range((t0 // STEP_MS + 1) * STEP_MS, t1, STEP_MS))

    def oi_at(sym, t):
        d = universe[sym][0]
        prev = None
        for ts in sorted(d):
            if ts <= t:
                prev = ts
            else:
                break
        return d[prev] if prev is not None else None

    for name, sign in (("süreklilik", +1), ("tükenme (ters)", -1)):
        quad_pnl = {k: [] for k in
                    (f"{p}{o}{x}" for p in "LS" for o in "UD" for x in "UD")}
        for t in grid:
            for sym, (oi, closes) in universe.items():
                oi0, oi1 = oi_at(sym, t), oi_at(sym, t + LOOK_MS)
                c0, c1 = closes.get(t), closes.get(t + LOOK_MS)
                if oi0 is None or oi1 is None or c0 is None or c1 is None:
                    continue
                if oi0 <= 0:
                    continue
                oi_chg = (oi1 - oi0) / oi0 * 100
                px_chg = (c1 - c0) / c0 * 100
                fwd = closes.get(t + LOOK_MS + HOLD_MS)
                if fwd is None:
                    continue
                fwd_ret = (fwd - c1) / c1
                oi_up, px_up = oi_chg > 0, px_chg > 0
                if oi_up and px_up:
                    pos = "LONG" if sign > 0 else "SHORT"
                elif oi_up and not px_up:
                    pos = "SHORT" if sign > 0 else "LONG"
                elif not oi_up and px_up:
                    pos = "SHORT" if sign > 0 else "LONG"
                else:
                    pos = "LONG" if sign > 0 else "SHORT"
                pnl = (fwd_ret if pos == "LONG" else -fwd_ret) - COST
                key = ("L" if pos == "LONG" else "S") + \
                      ("U" if oi_up else "D") + ("U" if px_up else "D")
                quad_pnl[key].append(pnl)

        # --- zaman-eşleşmeli market-nötr spread (drift bağımsız) ---
        spreads = []
        for t in grid:
            legs = {}
            for sym, (oi, closes) in universe.items():
                oi0, oi1 = oi_at(sym, t), oi_at(sym, t + LOOK_MS)
                c0, c1 = closes.get(t), closes.get(t + LOOK_MS)
                if oi0 is None or oi1 is None or c0 is None or c1 is None or oi0 <= 0:
                    continue
                oi_chg = (oi1 - oi0) / oi0 * 100
                px_chg = (c1 - c0) / c0 * 100
                fwd = closes.get(t + LOOK_MS + HOLD_MS)
                if fwd is None:
                    continue
                legs[sym] = (oi_chg > 0, px_chg > 0, (fwd - c1) / c1)
            down = [s for s, (o, p, _) in legs.items() if not p]
            up = [s for s, (o, p, _) in legs.items() if p]
            if len(down) < 3 or len(up) < 3:
                continue
            fade_l = [s for s in down if not legs[s][0]]      # OI↓ px↓ -> LONG
            newshort_s = [s for s in down if legs[s][0]]      # OI↑ px↓ -> SHORT
            if len(fade_l) >= 1 and len(newshort_s) >= 1:
                pnl = (np.mean([legs[s][2] for s in fade_l])
                       - np.mean([legs[s][2] for s in newshort_s])) - 2 * COST
                spreads.append(pnl)

        print(f"\n[{name}] (24h tutuş, maliyet dahil)")
        for k, v in sorted(quad_pnl.items(), key=lambda kv: -len(kv[1])):
            if not v:
                continue
            arr = np.array(v)
            print(f"  pos {k[0]} | OI{'↑' if k[1]=='U' else '↓'} "
                  f"px{'↑' if k[2]=='U' else '↓'}: {len(arr):6d} | "
                  f"ort {arr.mean():+.4%} | WR {np.mean(arr>0):.0%}")
        if spreads:
            arr = np.array(spreads)
            print(f"  [SPREAD long-OI↓px↓ / short-OI↑px↓]: {len(arr)} aralık | "
                  f"ort {arr.mean():+.4%} | WR {np.mean(arr>0):.0%}")
        all_p = np.concatenate([v for v in quad_pnl.values() if v]) \
            if any(quad_pnl.values()) else np.array([0.0])
        print(f"  TOPLAM: {len(all_p)} | ort {all_p.mean():+.4%}")


if __name__ == "__main__":
    asyncio.run(main())
