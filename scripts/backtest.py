"""Geçmiş mumlar üzerinde canlı kural setini oynatıp başarı oranını ölçer.

Kurallar ve skorlama optimize_rr.precompute/walk ile birebir aynıdır; bu da
canlı ajanla (src/rules + src/engine) eşleniktir. Stop/hedef config.toml'daki
ATR çarpanlarından alınır. Funding/OI geçmişte olmadığı için devre dışı
(canlıda da OI None).
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import optimize_rr as opt
except ImportError:
    from scripts import optimize_rr as opt

import aiohttp
import numpy as np
import pandas as pd

from src.config import load_config
from src.klines import BinanceRest


async def main():
    cfg = load_config(Path("config.toml"))
    c = cfg["signals"]
    thr = (c["threshold_long"], c["threshold_short"])
    stop_mult = c.get("stop_atr_mult", 1.5)
    target_mult = c.get("target_atr_mult", 3.0)
    htf_tf = c.get("htf_timeframe", "1h")

    async with aiohttp.ClientSession() as session:
        rest = BinanceRest(session)
        data = {}
        for sym in opt.SYMBOLS:
            for tf in opt.TFS:
                df = await opt.fetch(rest, sym, tf)
                if len(df) > opt.WARMUP + opt.HORIZON[tf] + 10:
                    data[(sym, tf)] = (df, opt.precompute(df, c))
        htfs = {}
        if c.get("use_htf_filter", False):
            for sym in opt.SYMBOLS:
                spans = [df["ts"] for (s, t), (df, _) in data.items() if s == sym]
                if not spans:
                    continue
                start_ms = int(min(s.min() for s in spans))
                end_ms = int(max(s.max() for s in spans)) + opt.TFS["3m"]
                htfs[sym] = await opt.fetch_htf(rest, sym, htf_tf, start_ms, end_ms)
                await asyncio.sleep(0.1)
        span = ""
        for (sym, tf), (df, _) in data.items():
            if sym == opt.SYMBOLS[0]:
                t0 = datetime.fromtimestamp(df["ts"].iat[0] / 1000, timezone.utc)
                t1 = datetime.fromtimestamp(df["ts"].iat[-1] / 1000, timezone.utc)
                span = f"{tf}: {t0:%d.%m %H:%M} - {t1:%d.%m %H:%M} UTC"

    results = []
    by_series: dict[tuple, list] = {}
    for (sym, tf), (df, pre) in data.items():
        htf = None
        if c.get("use_htf_filter", False):
            htf = opt.htf_trend_at(htfs.get(sym, pd.DataFrame()), df,
                                   c.get("supertrend_len", 20),
                                   c.get("supertrend_mult", 2.0), htf_tf)
        for s in opt.walk(df, pre, c, thr, htf):
            by_series.setdefault((sym, tf), []).append(s)
    for (sym, tf), g in by_series.items():
        df = data[(sym, tf)][0]
        for r in opt.evaluate(g, df, tf, stop_mult, target_mult)["details"]:
            r["sym"], r["tf"], r["ts"] = sym, tf, int(df["ts"].iat[r["i"]])
            results.append(r)

    print(f"\n=== Eşik L{thr[0]:g}/S{thr[1]:g} | stop {stop_mult:g}x / "
          f"hedef {target_mult:g}x ATR | {len(data)} seri | {span} ===")
    if not results:
        print("Sinyal yok.")
        return
    wins = [x for x in results if x["result"] == "WIN"]
    print(f"Toplam sinyal: {len(results)} | Kazanan: {len(wins)} "
          f"| Başarı: {len(wins) / len(results):.1%} "
          f"| Ort. PnL: {np.mean([x['pnl'] for x in results]):+.3f}% "
          f"| Ort. R: {np.mean([x['r'] for x in results]):+.2f}")
    for grp, key in (("LONG", lambda x: x["dir"] == "LONG"),
                     ("SHORT", lambda x: x["dir"] == "SHORT"),
                     ("GÜÇLÜ", lambda x: x["score"] >= c["strong_threshold"]),
                     ("normal", lambda x: x["score"] < c["strong_threshold"])):
        g = [x for x in results if key(x)]
        if g:
            w = sum(1 for x in g if x["result"] == "WIN")
            print(f"  {grp:7s}: {len(g):3d} sinyal | başarı {w / len(g):.1%} "
                  f"| ort. {np.mean([x['pnl'] for x in g]):+.3f}%")
    per_tf: dict[str, list] = {}
    for x in results:
        per_tf.setdefault(x["tf"], []).append(x)
    for tf, g in per_tf.items():
        w = sum(1 for x in g if x["result"] == "WIN")
        print(f"  {tf:7s}: {len(g):3d} sinyal | başarı {w / len(g):.1%}")
    print("Örnek sinyaller:")
    for x in results[:10]:
        t = datetime.fromtimestamp(x["ts"] / 1000, timezone.utc)
        print(f"  {t:%d.%m %H:%M} {x['sym']:9s} {x['tf']} {x['dir']:5s} "
              f"skor {x['score']} -> {x['result']} {x['pnl']:+.2f}%")


if __name__ == "__main__":
    asyncio.run(main())
