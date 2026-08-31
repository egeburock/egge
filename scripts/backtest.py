"""Geçmiş mumlar üzerinde kural setini oynatıp sinyal başarı oranını ölçer.

Canlı ajanla aynı kurallar (src/rules ile birebir mantık), aynı eşikler.
Her sinyal için: stop = 1.5*ATR(14), hedef = 1.0R (simetrik); ufuk içinde
önce hangisi gelirse o sayılır, ikisi de gelmezse ufuk sonu kapanışa bakılır.
Funding/OI geçmişte olmadığı için bu backtest'te devre dışı (canlıda da OI None).
"""
import asyncio
import math
from datetime import datetime, timezone
from pathlib import Path

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
WARMUP = 60
HORIZON = {"1m": 120, "3m": 60}
ATR_MULT = 1.5


async def fetch(rest: BinanceRest, sym: str, tf: str) -> pd.DataFrame:
    tf_ms = TFS[tf]
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = end - BARS * tf_ms
    frames = []
    cursor = start
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
    return df[df["ts"] < end - tf_ms]  # son açık mumu at


def precompute(df: pd.DataFrame, c: dict) -> dict:
    close, high, low = df["close"], df["high"], df["low"]
    return {
        "f": EMAIndicator(close, window=c["ema_fast"]).ema_indicator().to_numpy(),
        "s": EMAIndicator(close, window=c["ema_slow"]).ema_indicator().to_numpy(),
        "r": RSIIndicator(close, window=c["rsi_period"]).rsi().to_numpy(),
        "h": MACD(close).macd_diff().to_numpy(),
        "adx": ADXIndicator(high, low, close).adx().to_numpy(),
        "atr": AverageTrueRange(high, low, close).average_true_range().to_numpy(),
    }


def hits_at(i: int, df: pd.DataFrame, ind: dict, c: dict):
    o, cl = df["open"].iat[i], df["close"].iat[i]
    v = df["quote_volume"].to_numpy()
    long, short, neutral = [], [], []
    trend_ok = not math.isnan(ind["adx"][i]) and ind["adx"][i] > c["adx_min"]
    if trend_ok:
        if ind["f"][i - 1] <= ind["s"][i - 1] and ind["f"][i] > ind["s"][i]:
            long.append(("ema_cross", 3.0))
        elif ind["f"][i - 1] >= ind["s"][i - 1] and ind["f"][i] < ind["s"][i]:
            short.append(("ema_cross", 3.0))
        if ind["h"][i - 1] <= 0 < ind["h"][i]:
            long.append(("macd_cross", 2.0))
        elif ind["h"][i - 1] >= 0 > ind["h"][i]:
            short.append(("macd_cross", 2.0))
    if not math.isnan(ind["r"][i - 1]):
        if ind["r"][i - 1] < c["rsi_oversold"] < ind["r"][i]:
            long.append(("rsi_reversal", 2.0))
        elif ind["r"][i - 1] > c["rsi_overbought"] > ind["r"][i]:
            short.append(("rsi_reversal", 2.0))
    avg = v[i - c["volume_avg_bars"]:i].mean()
    if avg > 0 and v[i] / avg >= c["volume_spike_x"]:
        neutral.append(("volume_spike", 2.0))
    if o > 0:
        chg = (cl - o) / o * 100
        if chg >= c["price_jump_pct"]:
            long.append(("price_jump", 2.0))
        elif chg <= -c["price_jump_pct"]:
            short.append(("price_jump", 2.0))
    return long, short, neutral


def simulate(df: pd.DataFrame, i: int, direction: str, price: float,
             dist: float, horizon: int) -> tuple[str, float]:
    hi, lo, cl = (df["high"].to_numpy(), df["low"].to_numpy(),
                  df["close"].to_numpy())
    sign = 1.0 if direction == "LONG" else -1.0
    stop, target = price - sign * dist, price + sign * dist
    for j in range(i + 1, min(i + 1 + horizon, len(df))):
        hit_stop = lo[j] <= stop if direction == "LONG" else hi[j] >= stop
        if hit_stop:
            return "LOSS", -dist / price * 100
        hit_target = hi[j] >= target if direction == "LONG" else lo[j] <= target
        if hit_target:
            return "WIN", dist / price * 100
    end = cl[min(i + horizon, len(df) - 1)]
    pnl = sign * (end - price) / price * 100
    return ("WIN" if pnl > 0 else "LOSS"), pnl


def walk(sym: str, tf: str, df: pd.DataFrame, ind: dict, c: dict, threshold: float):
    out = []
    horizon = HORIZON[tf]
    cooldown_bars = 3
    last = {"LONG": -10**9, "SHORT": -10**9}
    v = df["quote_volume"].to_numpy()
    for i in range(WARMUP, len(df) - horizon):
        if v[i] < c["min_quote_volume_usd"]:
            continue
        if any(math.isnan(x[i]) or math.isnan(x[i - 1])
               for x in (ind["f"], ind["s"], ind["r"], ind["h"])):
            continue
        long, short, neutral = hits_at(i, df, ind, c)
        ls, ss = sum(w for _, w in long), sum(w for _, w in short)
        if ls == 0 and ss == 0:
            continue
        direction = "LONG" if ls >= ss else "SHORT"
        score = (ls if direction == "LONG" else ss) + sum(w for _, w in neutral)
        if score < threshold or i - last[direction] < cooldown_bars:
            continue
        last[direction] = i
        atr = ind["atr"][i]
        if math.isnan(atr) or atr <= 0:
            continue
        price = df["close"].iat[i]
        result, pnl = simulate(df, i, direction, price, ATR_MULT * atr, horizon)
        out.append({"sym": sym, "tf": tf, "dir": direction, "score": score,
                    "strong": score >= 8.0, "result": result, "pnl": pnl,
                    "rules": "+".join(r for r, _ in long + short + neutral),
                    "ts": int(df["ts"].iat[i])})
    return out


async def main():
    cfg = load_config(Path("config.toml"))
    c = cfg["signals"]
    async with aiohttp.ClientSession() as session:
        rest = BinanceRest(session)
        data = {}
        for sym in SYMBOLS:
            for tf in TFS:
                df = await fetch(rest, sym, tf)
                if len(df) > WARMUP + HORIZON[tf] + 10:
                    data[(sym, tf)] = (df, precompute(df, c))
        span = ""
        for (sym, tf), (df, _) in data.items():
            if sym == SYMBOLS[0]:
                t0 = datetime.fromtimestamp(df["ts"].iat[0] / 1000, timezone.utc)
                t1 = datetime.fromtimestamp(df["ts"].iat[-1] / 1000, timezone.utc)
                span = f"{tf}: {t0:%d.%m %H:%M} - {t1:%d.%m %H:%M} UTC"

    for threshold in (c["threshold"], 3.0):
        sigs = [s for (sym, tf), (df, ind) in data.items()
                for s in walk(sym, tf, df, ind, c, threshold)]
        print(f"\n=== Eşik {threshold} | {len(data)} seri | {span} ===")
        if not sigs:
            print("Sinyal yok.")
            continue
        wins = [s for s in sigs if s["result"] == "WIN"]
        print(f"Toplam sinyal: {len(sigs)} | Kazanan: {len(wins)} "
              f"| Başarı: {len(wins) / len(sigs):.1%} "
              f"| Ort. PnL: {np.mean([s['pnl'] for s in sigs]):+.3f}%")
        for grp, key in (("LONG", lambda s: s["dir"] == "LONG"),
                         ("SHORT", lambda s: s["dir"] == "SHORT"),
                         ("GÜÇLÜ", lambda s: s["strong"]),
                         ("normal", lambda s: not s["strong"])):
            g = [s for s in sigs if key(s)]
            if g:
                w = sum(1 for s in g if s["result"] == "WIN")
                print(f"  {grp:7s}: {len(g):3d} sinyal | başarı {w / len(g):.1%} "
                      f"| ort. {np.mean([s['pnl'] for s in g]):+.3f}%")
        per_tf = {}
        for s in sigs:
            per_tf.setdefault(s["tf"], []).append(s)
        for tf, g in per_tf.items():
            w = sum(1 for s in g if s["result"] == "WIN")
            print(f"  {tf:7s}: {len(g):3d} sinyal | başarı {w / len(g):.1%}")
        if threshold == 3.0:
            print("Örnek sinyaller:")
            for s in sigs[:10]:
                t = datetime.fromtimestamp(s["ts"] / 1000, timezone.utc)
                print(f"  {t:%d.%m %H:%M} {s['sym']:9s} {s['tf']} {s['dir']:5s} "
                      f"skor {s['score']} [{s['rules']}] -> {s['result']} {s['pnl']:+.2f}%")


if __name__ == "__main__":
    asyncio.run(main())
