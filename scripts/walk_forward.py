"""Walk-forward validation: veriyi kronolojik olarak train/test pencerelerine
bölerek out-of-sample performansı ölçer. Overfitting riskini azaltır.
"""
import asyncio
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import optimize_rr as opt
except ImportError:
    from scripts import optimize_rr as opt

import aiohttp

from src.config import load_config
from src.klines import BinanceRest

BE_R_MULTS = [0.0, 0.5, 1.0]  # breakeven tetiği (R cinsinden); 0 = kapalı


async def main():
    cfg = load_config(Path("config.toml"))
    c = cfg["signals"]
    stop_mult = c.get("stop_atr_mult", 2.0)
    target_mult = c.get("target_atr_mult", 2.0)
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

    print(f"Toplam {len(data)} seri yüklendi.")

    htf_series = {}
    if c.get("use_htf_filter", False):
        for (sym, tf), (df, _) in data.items():
            htf_series[(sym, tf)] = opt.htf_trend_at(
                htfs.get(sym, pd.DataFrame()), df, c.get("supertrend_len", 20),
                c.get("supertrend_mult", 2.0), htf_tf)

    n_folds = 4
    all_fold_results = []

    for fold in range(n_folds):
        fold_results = []
        for (sym, tf), (df, pre) in data.items():
            n = len(df)
            train_end = int(n * (fold + 1) / n_folds)
            if train_end < opt.WARMUP + opt.HORIZON[tf] + 10:
                continue
            train_df, test_df = df.iloc[:train_end].copy(), df.iloc[train_end:].copy()
            train_pre = {k: v[:train_end] for k, v in pre.items()}
            test_pre = {k: v[train_end:] for k, v in pre.items()}
            htf_full = htf_series.get((sym, tf))
            train_htf = htf_full[:train_end] if htf_full is not None else None
            test_htf = htf_full[train_end:] if htf_full is not None else None

            # Train'de en iyi (thr, stop, target, be_r) kombinasyonunu bul
            best_pnl = -1e9
            best_cfg = None
            chosen = []
            for th in opt.THRESHOLDS:
                sigs = list(opt.walk(train_df, train_pre, c, th, train_htf))
                if len(sigs) < 5:
                    continue
                by_series = {}
                for s in sigs:
                    by_series.setdefault((sym, tf), []).append(s)
                for stop in opt.STOP_MULTS:
                    for target in opt.TARGET_MULTS:
                        for be_r in BE_R_MULTS:
                            res = []
                            for (sy, tf2), g in by_series.items():
                                dff = data[(sy, tf2)][0].iloc[:train_end]
                                res += opt.evaluate(g, dff, tf2, stop, target, be_r)["details"]
                            if res:
                                avg_pnl = np.mean([x["pnl"] for x in res])
                                if avg_pnl > best_pnl:
                                    best_pnl = avg_pnl
                                    best_cfg = (th, stop, target, be_r)

            if best_cfg is None:
                continue
            (thr_tr, stop_tr, target_tr, be_tr) = best_cfg
            chosen.append(best_cfg)

            # Test'te en iyi cfg ile simüle et
            test_sigs = list(opt.walk(test_df, test_pre, c, thr_tr, test_htf))
            if not test_sigs:
                continue
            by_series = {}
            for s in test_sigs:
                by_series.setdefault((sym, tf), []).append(s)
            for (sy, tf2), g in by_series.items():
                dff = data[(sy, tf2)][0].iloc[train_end:]
                res = opt.evaluate(g, dff, tf2, stop_tr, target_tr, be_tr)["details"]
                for x in res:
                    x["fold"], x["sym"], x["tf"] = fold, sy, tf2
                    fold_results.append(x)

        if not fold_results:
            print(f"Fold {fold+1}: yeterli sinyal yok")
            continue

        wins = [x for x in fold_results if x["result"] == "WIN"]
        avg_pnl = np.mean([x["pnl"] for x in fold_results])
        avg_r = np.mean([x["r"] for x in fold_results])
        cfg_dist = {}
        for th, sm, tm, be in chosen:
            key = f"L{th[0]:g}/S{th[1]:g} stop{sm:g} tgt{tm:g} BE{be:g}"
            cfg_dist[key] = cfg_dist.get(key, 0) + 1
        cfg_line = ", ".join(f"{k} x{v}" for k, v in
                             sorted(cfg_dist.items(), key=lambda kv: -kv[1])[:3])
        print(f"\nFold {fold+1}/{n_folds} (train seçimleri: {cfg_line}):")
        print(f"  Test sinyalleri: {len(fold_results)} | WR: {len(wins)/len(fold_results):.1%} | "
              f"PnL: {avg_pnl:+.3f}% | R: {avg_r:+.2f}")
        for grp, key in (("LONG", lambda x: x["dir"] == "LONG"),
                         ("SHORT", lambda x: x["dir"] == "SHORT")):
            g = [x for x in fold_results if key(x)]
            if g:
                w = sum(1 for x in g if x["result"] == "WIN")
                print(f"  {grp}: {len(g)} sinyal | WR {w/len(g):.1%}")

        all_fold_results.extend(fold_results)

    if all_fold_results:
        wins = [x for x in all_fold_results if x["result"] == "WIN"]
        avg_pnl = np.mean([x["pnl"] for x in all_fold_results])
        avg_r = np.mean([x["r"] for x in all_fold_results])
        print(f"\n=== TOPLAM (tüm fold'lar) ===")
        print(f"  Sinyal: {len(all_fold_results)} | WR: {len(wins)/len(all_fold_results):.1%} | "
              f"PnL: {avg_pnl:+.3f}% | R: {avg_r:+.2f}")

    print("\nNot: walk-forward sonuçları in-sample grid'e kıyasla daha gerçekçidir.")


if __name__ == "__main__":
    asyncio.run(main())