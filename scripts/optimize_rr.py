"""V2 kural setiyle stop/hedef çarpanı ve eşik grid araması yapar.

Canlı ajanla aynı kurallar (supertrend, volume_zscore, absorption, ob_retest
+ temel kurallar), aynı skorlama mantığı (src.engine ile birebir).
Funding/OI geçmişte olmadığı için devre dışı (canlıda da OI None).
"""
import asyncio
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp
import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from src.config import load_config
from src.klines import BinanceRest, parse_klines

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
           "PEPEUSDT", "WIFUSDT", "ARBUSDT", "OPUSDT", "LINKUSDT", "SUIUSDT"]
TFS = {"1m": 60_000, "3m": 180_000}
BARS = 3000
WARMUP = 80
HORIZON = {"1m": 45, "3m": 45}  # canlı tracker horizon_minutes=30 ile aynı
COOLDOWN_BARS = 3
MIN_SIGNALS = 10

THRESHOLDS = [(6.0, 4.0), (6.0, 5.0), (6.0, 6.0), (7.0, 5.0), (7.0, 6.0),
              (7.0, 7.0), (8.0, 6.0), (8.0, 7.0)]
STOP_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]
TARGET_MULTS = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0]

# İşlem maliyetleri (Binance USDT-M Futures varsayılan)
TAKER_FEE = 0.0004   # 0.04%
MAKER_FEE = 0.0002   # 0.02%
SLIPPAGE_BPS = 1     # 1 bps = 0.01% per fill
MARKET_RT_COST_PCT = TAKER_FEE + MAKER_FEE + 2 * SLIPPAGE_BPS / 10000  # ~0.08%
LIMIT_RT_COST_PCT = MAKER_FEE + MAKER_FEE + SLIPPAGE_BPS / 10000       # ~0.05% (limit giriş: slipaj yok)


CACHE_DIR = Path(__file__).resolve().parents[1] / ".data_cache"
CACHE_TTL_H = 6


def _cache_get(sym: str, tf: str) -> pd.DataFrame | None:
    f = CACHE_DIR / f"{sym}_{tf}.csv"
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_TTL_H * 3600:
        df = pd.read_csv(f)
        df.attrs["symbol"], df.attrs["timeframe"] = sym, tf
        return df
    return None


def _cache_put(sym: str, tf: str, df: pd.DataFrame):
    CACHE_DIR.mkdir(exist_ok=True)
    df.to_csv(CACHE_DIR / f"{sym}_{tf}.csv", index=False)


async def fetch(rest: BinanceRest, sym: str, tf: str) -> pd.DataFrame:
    cached = _cache_get(sym, tf)
    if cached is not None:
        return cached
    tf_ms = TFS[tf]
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = end - BARS * tf_ms
    frames, cursor = [], start
    while cursor < end:
        try:
            raw = await rest._json("/fapi/v1/klines",
                                   {"symbol": sym, "interval": tf,
                                    "startTime": cursor, "limit": 1500})
        except Exception as e:
            print(f"  ! {sym} {tf}: veri alınamadı ({e})")
            return pd.DataFrame()
        if not raw:
            break
        frames.append(parse_klines(raw, sym, tf))
        cursor = raw[-1][0] + tf_ms
        await asyncio.sleep(0.15)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).drop_duplicates("ts").reset_index(drop=True)
    df = df[df["ts"] < end - tf_ms]  # son açık mumu at
    _cache_put(sym, tf, df)
    return df


HTF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


async def fetch_htf(rest: BinanceRest, sym: str, htf: str,
                    start_ms: int, end_ms: int) -> pd.DataFrame:
    """Teyit katmanı için yüksek zaman dilimi mumları (yalnız kapalı mumlar)."""
    cached = _cache_get(sym, f"{htf}_htf")
    if cached is not None:
        return cached
    tf_ms = HTF_MS[htf]
    try:
        raw = await rest._json("/fapi/v1/klines",
                               {"symbol": sym, "interval": htf,
                                "startTime": start_ms - 40 * tf_ms,
                                "endTime": end_ms, "limit": 500})
    except Exception as e:
        print(f"  ! {sym} {htf}: HTF veri alınamadı ({e})")
        return pd.DataFrame()
    if not raw:
        return pd.DataFrame()
    df = parse_klines(raw, sym, htf)
    df = df[df["ts"] < end_ms - tf_ms]
    _cache_put(sym, f"{htf}_htf", df)
    return df


def htf_trend_at(df_htf: pd.DataFrame, df: pd.DataFrame,
                 length: int, mult: float, htf: str) -> np.ndarray:
    """df'deki her bar için, o anda KAPALI olan son HTF barının Supertrend yönü."""
    out = np.zeros(len(df), dtype=int)
    if df_htf.empty or len(df_htf) < length + 2:
        return out
    st = supertrend_series(df_htf, length, mult)
    close_ts = df_htf["ts"].to_numpy() + HTF_MS[htf]
    j = np.searchsorted(close_ts, df["ts"].to_numpy(), side="right") - 1
    ok = j >= 0
    out[ok] = st[j[ok]]
    return out


def supertrend_series(df: pd.DataFrame, length: int, mult: float) -> np.ndarray:
    """src.rules.supertrend'in tüm seriye uygulanmış hali; bar bazında trend."""
    n = len(df)
    out = np.zeros(n)
    if n < length + 2:
        return out
    atr = AverageTrueRange(df["high"], df["low"], df["close"],
                           window=length).average_true_range()
    hl2 = (df["high"] + df["low"]) / 2
    upper = (hl2 + mult * atr).to_numpy()
    lower = (hl2 - mult * atr).to_numpy()
    closes = df["close"].to_numpy()
    trend = 1 if closes[length] > lower[length] else -1
    stop = lower[length] if trend == 1 else upper[length]
    out[length] = trend
    for i in range(length + 1, n):
        prev_stop = stop
        if closes[i] > prev_stop and closes[i - 1] <= prev_stop:
            trend = 1
        elif closes[i] < prev_stop and closes[i - 1] >= prev_stop:
            trend = -1
        if trend == 1:
            stop = max(lower[i], prev_stop) if not np.isnan(lower[i]) else prev_stop
        else:
            stop = min(upper[i], prev_stop) if not np.isnan(upper[i]) else prev_stop
        out[i] = trend
    return out


def precompute(df: pd.DataFrame, c: dict) -> dict:
    """Bar bazında uzun/kısa/nötr skor dizileri + ATR (canlı kuralların aynısı)."""
    close, high, low = df["close"], df["high"], df["low"]
    n = len(df)
    f = EMAIndicator(close, window=c["ema_fast"]).ema_indicator().to_numpy()
    s = EMAIndicator(close, window=c["ema_slow"]).ema_indicator().to_numpy()
    r = RSIIndicator(close, window=c["rsi_period"]).rsi().to_numpy()
    h = MACD(close).macd_diff().to_numpy()
    adx = ADXIndicator(high, low, close).adx().to_numpy()
    atr = AverageTrueRange(high, low, close).average_true_range().to_numpy()
    v = df["quote_volume"]

    long = np.zeros(n)
    short = np.zeros(n)
    neutral = np.zeros(n)

    trend_ok = ~np.isnan(adx) & (adx > c["adx_min"])
    ema_up = (f[1:] > s[1:]) & (f[:-1] <= s[:-1])
    ema_dn = (f[1:] < s[1:]) & (f[:-1] >= s[:-1])
    macd_up = (h[:-1] <= 0) & (h[1:] > 0)
    macd_dn = (h[:-1] >= 0) & (h[1:] < 0)
    gated = trend_ok[1:]
    long[1:][ema_up & gated] += 3.0
    short[1:][ema_dn & gated] += 3.0
    long[1:][macd_up & gated] += 2.0
    short[1:][macd_dn & gated] += 2.0

    prev_r = r[:-1]
    long[1:][~np.isnan(prev_r) & (prev_r < c["rsi_oversold"]) & (r[1:] > c["rsi_oversold"])] += 2.0
    short[1:][~np.isnan(prev_r) & (prev_r > c["rsi_overbought"]) & (r[1:] < c["rsi_overbought"])] += 2.0

    spike_avg = v.rolling(c["volume_avg_bars"]).mean().shift(1).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = v.to_numpy() / spike_avg
    neutral[(spike_avg > 0) & (ratio >= c["volume_spike_x"])] += 2.0

    o = df["open"].to_numpy()
    cl = df["close"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        chg = (cl - o) / o * 100
    long[(o > 0) & (chg >= c["price_jump_pct"])] += 2.0
    short[(o > 0) & (chg <= -c["price_jump_pct"])] += 2.0

    if c.get("use_supertrend", True):
        st = supertrend_series(df, c.get("supertrend_len", 20),
                               c.get("supertrend_mult", 2.0))
        flip_up = (st[1:] == 1) & (st[:-1] != 1)
        flip_dn = (st[1:] == -1) & (st[:-1] != -1)
        long[1:][flip_up] += 2.0
        short[1:][flip_dn] += 2.0
    else:
        st = np.zeros(n)

    if c.get("use_zscore_whale", True):
        sma = v.rolling(20).mean().to_numpy()
        std = v.rolling(50).std().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            z = (v.to_numpy() - sma) / std
        neutral[(std > 0) & ~np.isnan(z) & (z >= c.get("whale_z_limit", 3.0))] += 2.0

    if c.get("use_absorption", True):
        rng = (df["high"] - df["low"]).to_numpy()
        body_pct = c.get("absorption_body_pct", 30.0)
        vol_mult = c.get("absorption_vol_mult", 1.5)
        avg20 = v.rolling(20).mean().shift(1).to_numpy()
        vv = v.to_numpy()
        small_body = (rng > 0) & (np.abs(cl - o) / np.where(rng > 0, rng, 1) * 100 <= body_pct)
        vol_ok = (avg20 > 0) & (vv >= avg20 * vol_mult)
        cand = small_body & vol_ok
        long[cand & (cl >= o)] += 1.0
        short[cand & (cl < o)] += 1.0

    if c.get("use_rsi2_pullback", False):
        r2 = RSIIndicator(close, window=c.get("rsi2_period", 2)).rsi().to_numpy()
        with np.errstate(invalid="ignore"):
            long[~np.isnan(r2) & (st == 1) & (r2 <= c.get("rsi2_oversold", 10.0))] += 3.0
            short[~np.isnan(r2) & (st == -1) & (r2 >= c.get("rsi2_overbought", 90.0))] += 3.0

    if c.get("use_ema_pullback", False):
        tol = c.get("ema_pullback_tol_pct", 0.1) / 100
        pe = c.get("ema_pullback_period", 21)
        ep = EMAIndicator(close, window=pe).ema_indicator().to_numpy()
        lo = df["low"].to_numpy()
        hi2 = df["high"].to_numpy()
        with np.errstate(invalid="ignore"):
            long[~np.isnan(ep) & (st == 1) & (lo <= ep * (1 + tol)) & (cl > ep)] += 2.0
            short[~np.isnan(ep) & (st == -1) & (hi2 >= ep * (1 - tol)) & (cl < ep)] += 2.0

    if c.get("use_ob_retest", True):
        pivot_len = c.get("ob_pivot_len", 5)
        lows = df["low"].to_numpy()
        highs = df["high"].to_numpy()
        for i in range(2 * pivot_len + 1, n):
            if np.isnan(atr[i]) or atr[i] <= 0:
                continue
            p = i - pivot_len
            if st[i] == 1:
                if lows[p - pivot_len:p].min() <= lows[p]:
                    continue
                if lows[p + 1:p + pivot_len + 1].min() < lows[p]:
                    continue
                ob_top = min(o[p], cl[p])
                if lows[i] <= ob_top and cl[i] > ob_top:
                    long[i] += 3.0
            elif st[i] == -1:
                if highs[p - pivot_len:p].max() >= highs[p]:
                    continue
                if highs[p + 1:p + pivot_len + 1].max() > highs[p]:
                    continue
                ob_bot = max(o[p], cl[p])
                if highs[i] >= ob_bot and cl[i] < ob_bot:
                    short[i] += 3.0

    return {"long": long, "short": short, "neutral": neutral, "atr": atr}


def walk(df: pd.DataFrame, pre: dict, c: dict,
         thr: tuple[float, float], htf: np.ndarray | None = None) -> list[dict]:
    """Verilen eşiklerle ateşlenen aday sinyaller (cooldown dahil)."""
    out = []
    n = len(df)
    last = {"LONG": -10**9, "SHORT": -10**9}
    v = df["quote_volume"].to_numpy()
    cl = df["close"].to_numpy()
    use_htf = c.get("use_htf_filter", False) and htf is not None
    min_atr_pct = c.get("min_atr_pct", 0.0)
    for i in range(WARMUP, n):
        if v[i] < c["min_quote_volume_usd"]:
            continue
        if min_atr_pct > 0 and cl[i] > 0 and pre["atr"][i] / cl[i] * 100 < min_atr_pct:
            continue
        ls, ss = pre["long"][i], pre["short"][i]
        if ls == 0 and ss == 0:
            continue
        direction = "LONG" if ls >= ss else "SHORT"
        if use_htf and htf[i] != (1 if direction == "LONG" else -1):
            continue
        score = (ls if direction == "LONG" else ss) + pre["neutral"][i]
        thr_d = thr[0] if direction == "LONG" else thr[1]
        if score < thr_d or i - last[direction] < COOLDOWN_BARS:
            continue
        atr = pre["atr"][i]
        if math.isnan(atr) or atr <= 0:
            continue
        last[direction] = i
        out.append({"i": i, "dir": direction, "score": score,
                    "price": cl[i], "atr": atr})
    return out


def simulate(df: pd.DataFrame, i: int, direction: str, price: float,
             stop_dist: float, tgt_dist: float, horizon: int,
             be_r: float = 0.0, stale_bars: int = 0,
             entry_off_r: float = 0.0, entry_window: int = 8) -> tuple[str, float]:
    """entry_off_r > 0: sinyal kapanışından entry_off_r*R geri çekilmede limit
    giriş; entry_window bar içinde dolmazsa fırsat kaçar (MISSED)."""
    hi, lo, cl = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    sign = 1.0 if direction == "LONG" else -1.0
    cost = LIMIT_RT_COST_PCT if entry_off_r > 0 else MARKET_RT_COST_PCT

    if entry_off_r > 0:
        lim = price - sign * (entry_off_r * stop_dist)
        filled_at = -1
        for j in range(i + 1, min(i + 1 + entry_window, len(df))):
            touched = lo[j] <= lim if direction == "LONG" else hi[j] >= lim
            if touched:
                filled_at = j
                break
        if filled_at < 0:
            return "MISSED", 0.0
        price = lim
        i = filled_at

    stop = price - sign * stop_dist
    target = price + sign * tgt_dist
    be_trigger = price + sign * (be_r * stop_dist) if be_r > 0 else None
    armed = False
    for j in range(i + 1, min(i + 1 + horizon, len(df))):
        if be_trigger is not None and not armed:
            armed = hi[j] >= be_trigger if direction == "LONG" else lo[j] <= be_trigger
        eff_stop = price if armed else stop
        hit_stop = lo[j] <= eff_stop if direction == "LONG" else hi[j] >= eff_stop
        if hit_stop:
            if armed:
                return "BE", -cost * 100
            gross = -stop_dist / price * 100
            return "LOSS", gross - cost * 100
        hit_target = hi[j] >= target if direction == "LONG" else lo[j] <= target
        if hit_target:
            gross = tgt_dist / price * 100
            return "WIN", gross - cost * 100
        if stale_bars > 0 and j - i >= stale_bars:
            unreal = sign * (cl[j] - price) / price * 100
            if unreal <= 0:
                return "STALE", unreal - cost * 100
    end = cl[min(i + horizon, len(df) - 1)]
    gross = sign * (end - price) / price * 100
    return ("WIN" if gross > 0 else "LOSS"), gross - cost * 100


def evaluate(sigs: list[dict], df: pd.DataFrame, tf: str,
             stop_mult: float, target_mult: float, be_r: float = 0.0,
             stale_bars: int = 0, entry_off_r: float = 0.0) -> dict:
    horizon = HORIZON[tf]
    results = []
    for s in sigs:
        stop_dist, tgt_dist = stop_mult * s["atr"], target_mult * s["atr"]
        result, pnl = simulate(df, s["i"], s["dir"], s["price"],
                               stop_dist, tgt_dist, horizon, be_r, stale_bars,
                               entry_off_r)
        if result == "MISSED":
            continue
        results.append({**s, "result": result, "pnl": pnl,
                        "r": pnl / (stop_dist / s["price"] * 100)})
    if not results:
        return {"n": 0}
    wins = [x for x in results if x["result"] == "WIN"]
    return {"n": len(results), "wins": len(wins),
            "wr": len(wins) / len(results),
            "avg_pnl": float(np.mean([x["pnl"] for x in results])),
            "avg_r": float(np.mean([x["r"] for x in results])),
            "details": results}


async def main():
    cfg = load_config(Path("config.toml"))
    c = cfg["signals"]
    htf_tf = c.get("htf_timeframe", "1h")
    async with aiohttp.ClientSession() as session:
        rest = BinanceRest(session)
        data = {}
        for sym in SYMBOLS:
            for tf in TFS:
                df = await fetch(rest, sym, tf)
                if len(df) > WARMUP + HORIZON[tf] + 10:
                    data[(sym, tf)] = (df, precompute(df, c))
        htfs = {}
        if c.get("use_htf_filter", False):
            for sym in SYMBOLS:
                spans = [df["ts"] for (s, t), (df, _) in data.items() if s == sym]
                if not spans:
                    continue
                start_ms = int(min(s.min() for s in spans))
                end_ms = int(max(s.max() for s in spans)) + max(TFS.values())
                df_htf = await fetch_htf(rest, sym, htf_tf, start_ms, end_ms)
                htfs[sym] = df_htf
                await asyncio.sleep(0.1)
        print(f"{len(data)} seri yüklendi.")
        span = ""
        for (sym, tf), (df, _) in data.items():
            if sym == SYMBOLS[0]:
                t0 = datetime.fromtimestamp(df["ts"].iat[0] / 1000, timezone.utc)
                t1 = datetime.fromtimestamp(df["ts"].iat[-1] / 1000, timezone.utc)
                span = f"{tf}: {t0:%d.%m %H:%M} - {t1:%d.%m %H:%M} UTC"
        print(span)

    # aday sinyalleri eşik bazında bir kez yürüt (simülasyon grid'de tekrarlanır)
    htf_series = {}
    if c.get("use_htf_filter", False):
        for (sym, tf), (df, _) in data.items():
            df_htf = htfs.get(sym, pd.DataFrame())
            htf_series[(sym, tf)] = htf_trend_at(
                df_htf, df, c.get("supertrend_len", 20),
                c.get("supertrend_mult", 2.0), htf_tf)
    candidates: dict[tuple, list] = {}
    for thr in THRESHOLDS:
        acc = []
        for (sym, tf), (df, pre) in data.items():
            for s in walk(df, pre, c, thr, htf_series.get((sym, tf))):
                acc.append({**s, "sym": sym, "tf": tf})
        candidates[thr] = acc

    rows = []
    for thr, sigs in candidates.items():
        if len(sigs) < MIN_SIGNALS:
            continue
        # seri bazında grupla: simulate için her sinyalin kendi df'si gerek
        by_series = {}
        for s in sigs:
            by_series.setdefault((s["sym"], s["tf"]), []).append(s)
        for sm in STOP_MULTS:
            for tm in TARGET_MULTS:
                all_res = []
                for (sym, tf), g in by_series.items():
                    df = data[(sym, tf)][0]
                    all_res += evaluate(g, df, tf, sm, tm)["details"]
                wins = [x for x in all_res if x["result"] == "WIN"]
                rows.append({
                    "thr": thr, "stop": sm, "target": tm, "n": len(all_res),
                    "wr": len(wins) / len(all_res),
                    "avg_pnl": float(np.mean([x["pnl"] for x in all_res])),
                    "avg_r": float(np.mean([x["r"] for x in all_res])),
                    "details": all_res,
                })

    rows.sort(key=lambda r: r["avg_pnl"], reverse=True)
    print(f"\n{'EŞİK':14s} {'STOP':>5s} {'HEDEF':>6s} {'N':>5s} {'WR':>7s} "
          f"{'ORT PNL':>9s} {'ORT R':>7s}")
    for r in rows[:12]:
        lt, st = r["thr"]
        print(f"L{lt:g}/S{st:g}        {r['stop']:>5.1f} {r['target']:>6.1f} "
              f"{r['n']:>5d} {r['wr']:>6.1%} {r['avg_pnl']:>+8.3f}% "
              f"{r['avg_r']:>+7.2f}")

    if not rows:
        print("Yeterli sinyal yok.")
        return
    best = rows[0]
    cur = next((r for r in rows if r["thr"] == (c["threshold_long"], c["threshold_short"])
                and r["stop"] == cfg["signals"].get("stop_atr_mult", 2.0)
                and r["target"] == cfg["signals"].get("target_atr_mult", 3.0)), None)
    if cur:
        print(f"\nMevcut canlı ayar (L{cur['thr'][0]:g}/S{cur['thr'][1]:g}, "
              f"stop {cur['stop']}, hedef {cur['target']}): "
              f"N={cur['n']} WR={cur['wr']:.1%} PnL={cur['avg_pnl']:+.3f}% R={cur['avg_r']:+.2f}")

    print(f"\nEn iyi: L{best['thr'][0]:g}/S{best['thr'][1]:g} "
          f"stop={best['stop']} hedef={best['target']} | "
          f"N={best['n']} WR={best['wr']:.1%} "
          f"PnL={best['avg_pnl']:+.3f}% R={best['avg_r']:+.2f}")
    for grp, key in (("LONG", lambda x: x["dir"] == "LONG"),
                     ("SHORT", lambda x: x["dir"] == "SHORT"),
                     ("GÜÇLÜ (>=8)", lambda x: x["score"] >= 8.0)):
        g = [x for x in best["details"] if key(x)]
        if g:
            w = sum(1 for x in g if x["result"] == "WIN")
            print(f"  {grp:11s}: {len(g):3d} sinyal | WR {w / len(g):.1%} "
                  f"| ort {np.mean([x['pnl'] for x in g]):+.3f}%")
    by_tf = {}
    for x in best["details"]:
        by_tf.setdefault(x["tf"], []).append(x)
    for tf, g in by_tf.items():
        w = sum(1 for x in g if x["result"] == "WIN")
        print(f"  {tf:11s}: {len(g):3d} sinyal | WR {w / len(g):.1%}")

    base = next((r for r in rows if r["thr"] == (6.0, 4.0)), None)
    if base:
        print("\nSkor bazında (eşik L6/S4, en iyi stop/hedef):")
        by_sc = {}
        for x in base["details"]:
            by_sc.setdefault((x["dir"], int(x["score"])), []).append(x)
        for (d, sc), g in sorted(by_sc.items()):
            w = sum(1 for x in g if x["result"] == "WIN")
            print(f"  {d:5s} skor {sc}: {len(g):4d} sinyal | WR {w / len(g):.1%} "
                  f"| ort {np.mean([x['pnl'] for x in g]):+.3f}%")


if __name__ == "__main__":
    asyncio.run(main())
