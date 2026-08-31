from src.models import Bar, RuleHit, Signal


def _direction_of(hit: RuleHit) -> str | None:
    if "LONG" in hit.detail or "Sıkışmış yay" in hit.detail:
        return "LONG"
    if "SHORT" in hit.detail:
        return "SHORT"
    return None


class SignalEngine:
    def __init__(self, cfg: dict):
        sig = cfg["signals"]
        self.long_threshold = sig["threshold_long"]
        self.short_threshold = sig["threshold_short"]
        self.strong_threshold = sig["strong_threshold"]
        self.min_quote_volume = sig.get("min_quote_volume_usd", 0.0)
        self.stop_atr_mult = sig.get("stop_atr_mult", 1.5)
        self.cooldowns: dict[str, int] = dict(cfg["timeframes"]["cooldown_s"])
        self._last: dict[tuple[str, str, str], int] = {}

    def evaluate(self, bar: Bar, hits: list[RuleHit],
                 funding_rate: float | None, oi_pct: float | None,
                 atr: float | None = None) -> list[Signal]:
        if bar.quote_volume < self.min_quote_volume:
            return []
        long_score = sum(h.score for h in hits if _direction_of(h) == "LONG")
        short_score = sum(h.score for h in hits if _direction_of(h) == "SHORT")
        if long_score == 0 and short_score == 0:
            return []
        direction = "LONG" if long_score >= short_score else "SHORT"
        keep = [h for h in hits if _direction_of(h) in (direction, None)]
        score = sum(h.score for h in keep)
        threshold = self.long_threshold if direction == "LONG" else self.short_threshold
        if score < threshold:
            return []
        key = (bar.symbol, bar.timeframe, direction)
        cd_ms = self.cooldowns.get(bar.timeframe, 60) * 1000
        last = self._last.get(key)
        if last is not None and bar.close_ts - last <= cd_ms:
            return []
        self._last[key] = bar.close_ts
        stop = None
        if atr is not None and atr > 0:
            dist = self.stop_atr_mult * atr
            stop = bar.close - dist if direction == "LONG" else bar.close + dist
        return [Signal(bar.symbol, bar.timeframe, direction,
                       strong=score >= self.strong_threshold, score=score,
                       price=bar.close, stop=stop, ts=bar.close_ts, hits=keep)]
