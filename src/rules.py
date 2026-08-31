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
