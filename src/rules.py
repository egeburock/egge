import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from src.models import RuleHit


def atr_value(df: pd.DataFrame, window: int = 14) -> float | None:
    if len(df) < window + 1:
        return None
    atr = AverageTrueRange(df["high"], df["low"], df["close"],
                           window=window).average_true_range()
    last = atr.iloc[-1]
    if pd.isna(last) or last <= 0:
        return None
    return float(last)


def ema_cross(df: pd.DataFrame, fast: int, slow: int) -> list[RuleHit]:
    close = df["close"]
    f = EMAIndicator(close, window=fast).ema_indicator()
    s = EMAIndicator(close, window=slow).ema_indicator()
    if len(close) < slow + 2:
        return []
    if f.iloc[-2] <= s.iloc[-2] and f.iloc[-1] > s.iloc[-1]:
        return [RuleHit("ema_cross", f"LONG: EMA{fast}>EMA{slow} kesişim", 3.0)]
    if f.iloc[-2] >= s.iloc[-2] and f.iloc[-1] < s.iloc[-1]:
        return [RuleHit("ema_cross", f"SHORT: EMA{fast}<EMA{slow} kesişim", 3.0)]
    return []


def rsi_reversal(df: pd.DataFrame, period: int, oversold: float, overbought: float) -> list[RuleHit]:
    r = RSIIndicator(df["close"], window=period).rsi()
    if len(df) < period + 2 or pd.isna(r.iloc[-2]):
        return []
    if r.iloc[-2] < oversold and r.iloc[-1] > oversold:
        return [RuleHit("rsi_reversal", f"LONG: RSI {oversold}'dan yukarı dönüş ({r.iloc[-1]:.1f})", 2.0)]
    if r.iloc[-2] > overbought and r.iloc[-1] < overbought:
        return [RuleHit("rsi_reversal", f"SHORT: RSI {overbought}'dan aşağı dönüş ({r.iloc[-1]:.1f})", 2.0)]
    return []


def macd_cross(df: pd.DataFrame) -> list[RuleHit]:
    m = MACD(df["close"])
    hist = m.macd_diff()
    if len(df) < 30 or pd.isna(hist.iloc[-2]):
        return []
    if hist.iloc[-2] <= 0 < hist.iloc[-1]:
        return [RuleHit("macd_cross", "LONG: MACD sinyal yukarı kesişim", 2.0)]
    if hist.iloc[-2] >= 0 > hist.iloc[-1]:
        return [RuleHit("macd_cross", "SHORT: MACD sinyal aşağı kesişim", 2.0)]
    return []


def adx_ok(df: pd.DataFrame, minimum: float) -> bool:
    if len(df) < 20:
        return False
    a = ADXIndicator(df["high"], df["low"], df["close"]).adx()
    return bool(not pd.isna(a.iloc[-1]) and a.iloc[-1] > minimum)


def volume_spike(df: pd.DataFrame, mult: float, lookback: int) -> list[RuleHit]:
    v = df["quote_volume"]
    if len(v) < lookback + 1:
        return []
    avg = v.iloc[-lookback - 1:-1].mean()
    if avg <= 0:
        return []
    ratio = v.iloc[-1] / avg
    if ratio >= mult:
        return [RuleHit("volume_spike", f"Hacim {ratio:.1f}x ({lookback}-bar ort.)", 2.0)]
    return []


def price_jump(df: pd.DataFrame, pct: float) -> list[RuleHit]:
    o, c = df["open"].iloc[-1], df["close"].iloc[-1]
    if o <= 0:
        return []
    chg = (c - o) / o * 100
    if chg >= pct:
        return [RuleHit("price_jump", f"LONG: bar içi +{chg:.1f}%", 2.0)]
    if chg <= -pct:
        return [RuleHit("price_jump", f"SHORT: bar içi {chg:.1f}%", 2.0)]
    return []


def funding_rule(rate: float | None, crowded: float, extreme_neg: float) -> list[RuleHit]:
    if rate is None:
        return []
    if rate >= crowded:
        return [RuleHit("funding", f"SHORT eğilimi: kalabalık long (funding {rate:.4%})", 1.0)]
    if rate <= extreme_neg:
        return [RuleHit("funding", f"LONG eğilimi: aşırı negatif funding ({rate:.4%})", 1.0)]
    return []


def supertrend(df: pd.DataFrame, length: int, mult: float) -> tuple[int, float | None]:
    """(trend, stop): trend 1=yukarı, -1=aşağı. Pine'daki Supertrend motorunun eşdeğeri."""
    if len(df) < length + 2:
        return 0, None
    atr = AverageTrueRange(df["high"], df["low"], df["close"],
                           window=length).average_true_range()
    hl2 = (df["high"] + df["low"]) / 2
    upper = (hl2 + mult * atr).tolist()
    lower = (hl2 - mult * atr).tolist()
    closes = df["close"].tolist()
    trend = 1 if closes[length] > lower[length] else -1
    stop = lower[length] if trend == 1 else upper[length]
    for i in range(length + 1, len(df)):
        prev_stop = stop
        if closes[i] > prev_stop and closes[i - 1] <= prev_stop:
            trend = 1
        elif closes[i] < prev_stop and closes[i - 1] >= prev_stop:
            trend = -1
        if trend == 1:
            stop = max(lower[i], prev_stop) if not pd.isna(lower[i]) else prev_stop
        else:
            stop = min(upper[i], prev_stop) if not pd.isna(upper[i]) else prev_stop
    return trend, stop


def supertrend_rule(df: pd.DataFrame, length: int, mult: float) -> list[RuleHit]:
    """Supertrend yalnızca yön değiştirdiğinde ateşlenir (trend süren her barda değil)."""
    if len(df) < length + 3:
        return []
    prev, _ = supertrend(df.iloc[:-1], length, mult)
    trend, stop = supertrend(df, length, mult)
    if trend == prev:
        return []
    if trend == 1:
        return [RuleHit("supertrend", f"LONG: Supertrend yukarı döndü (stop {stop:.6g})", 2.0)]
    if trend == -1:
        return [RuleHit("supertrend", f"SHORT: Supertrend aşağı döndü (stop {stop:.6g})", 2.0)]
    return []


def volume_zscore(df: pd.DataFrame, z_limit: float) -> list[RuleHit]:
    v = df["quote_volume"]
    if len(v) < 50:
        return []
    sma = v.rolling(20).mean().iloc[-1]
    std = v.rolling(50).std().iloc[-1]
    if pd.isna(sma) or pd.isna(std) or std <= 0:
        return []
    z = (v.iloc[-1] - sma) / std
    if z >= z_limit:
        return [RuleHit("volume_zscore", f"Balina hacmi: Z-Score {z:.1f} (eşik {z_limit})", 2.0)]
    return []


def absorption(df: pd.DataFrame, body_pct: float, vol_mult: float) -> list[RuleHit]:
    o, h, l, c = (df[k].iloc[-1] for k in ("open", "high", "low", "close"))
    rng = h - l
    if rng <= 0:
        return []
    if abs(c - o) / rng * 100 > body_pct:
        return []
    v = df["quote_volume"]
    if len(v) < 21:
        return []
    avg = v.iloc[-21:-1].mean()
    if avg <= 0 or v.iloc[-1] < avg * vol_mult:
        return []
    side = "LONG" if c >= o else "SHORT"
    return [RuleHit("absorption",
                    f"{side}: emilim (gövde %{abs(c - o) / rng * 100:.0f}, hacim {v.iloc[-1] / avg:.1f}x)",
                    1.0)]


def ob_retest(df: pd.DataFrame, pivot_len: int, trend: int,
              atr: float | None) -> list[RuleHit]:
    """Pivot tabanlı Order Block bölgesine dönüş + tepki (retest) sinyali."""
    if atr is None or len(df) < pivot_len * 3 + 2:
        return []
    lows, highs = df["low"].tolist(), df["high"].tolist()
    n = len(df)
    p = n - pivot_len - 1  # pivot adayı (sağ bacak kadar geride)

    if trend == 1:
        if min(lows[p - pivot_len:p]) <= lows[p] or min(lows[p + 1:p + pivot_len + 1]) < lows[p]:
            return []
        ob_top = min(df["open"].iloc[p], df["close"].iloc[p])
        # son bar kutuya dokunup üstünde kapandı mı?
        if lows[-1] <= ob_top and df["close"].iloc[-1] > ob_top:
            return [RuleHit("ob_retest", "LONG: boğa OB bölgesinden sekme (retest)", 3.0)]
    elif trend == -1:
        if max(highs[p - pivot_len:p]) >= highs[p] or max(highs[p + 1:p + pivot_len + 1]) > highs[p]:
            return []
        ob_bot = max(df["open"].iloc[p], df["close"].iloc[p])
        if highs[-1] >= ob_bot and df["close"].iloc[-1] < ob_bot:
            return [RuleHit("ob_retest", "SHORT: ayı OB bölgesinden ret (retest)", 3.0)]
    return []


def oi_rule(oi_pct: float | None, price_chg_pct: float) -> list[RuleHit]:
    """oi_pct: son 5 dk OI değişimi; price_chg_pct: aynı aralıkta fiyat değişimi."""
    if oi_pct is None:
        return []
    if oi_pct > 1.0 and price_chg_pct > 0:
        return [RuleHit("oi_confirm", f"LONG teyit: OI +{oi_pct:.1f}% fiyatla aynı yön", 1.0)]
    if oi_pct > 1.0 and price_chg_pct < 0:
        return [RuleHit("oi_confirm", f"SHORT teyit: OI +{oi_pct:.1f}% fiyat düşerken", 1.0)]
    if oi_pct > 1.0 and abs(price_chg_pct) < 0.2:
        return [RuleHit("oi_confirm", f"Sıkışmış yay: OI +{oi_pct:.1f}%, fiyat yatay", 1.0)]
    return []


def rsi2_pullback(df: pd.DataFrame, trend: int, period: int = 2,
                  oversold: float = 10.0, overbought: float = 90.0) -> list[RuleHit]:
    """Connors tarzı: trend içindeki aşırı dip/pump'a karşı mean reversion.

    RSI(period) aşırı uçlara savrulduğunda ve Supertrend trendiyle hizalıysa
    dönüş vuruşu arar (uptrend'de LONG dip, downtrend'de SHORT pump).
    """
    r = RSIIndicator(df["close"], window=period).rsi()
    if len(df) < period + 2 or pd.isna(r.iloc[-1]):
        return []
    last = float(r.iloc[-1])
    if trend == 1 and last <= oversold:
        return [RuleHit("rsi2_pullback",
                        f"LONG: RSI({period}) dibi {last:.0f} (trend yukarı)", 3.0)]
    if trend == -1 and last >= overbought:
        return [RuleHit("rsi2_pullback",
                        f"SHORT: RSI({period}) pompası {last:.0f} (trend aşağı)", 3.0)]
    return []


def ema_pullback(df: pd.DataFrame, trend: int, period: int = 21,
                 tol_pct: float = 0.1) -> list[RuleHit]:
    """NFI tarzı: trend yönünde EMA{period}'a geri çekilme + tepki.

    Uptrend'de fiyat EMA'ya değip üstünde kapanırsa LONG; downtrend'de
    EMA'ya değip altında kapanırsa SHORT.
    """
    ema = EMAIndicator(df["close"], window=period).ema_indicator()
    if len(df) < period + 2 or pd.isna(ema.iloc[-1]):
        return []
    c = df["close"].iloc[-1]
    e = float(ema.iloc[-1])
    tol = tol_pct / 100
    if trend == 1 and df["low"].iloc[-1] <= e * (1 + tol) and c > e:
        return [RuleHit("ema_pullback",
                        f"LONG: EMA{period} dönüşü (dip {df['low'].iloc[-1]:.6g} / "
                        f"EMA {e:.6g})", 2.0)]
    if trend == -1 and df["high"].iloc[-1] >= e * (1 - tol) and c < e:
        return [RuleHit("ema_pullback",
                        f"SHORT: EMA{period} ret (tepe {df['high'].iloc[-1]:.6g} / "
                        f"EMA {e:.6g})", 2.0)]
    return []
