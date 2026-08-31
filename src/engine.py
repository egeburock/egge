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
        self.threshold = sig["threshold"]
        self.strong_threshold = sig["strong_threshold"]
        self.min_quote_volume = sig.get("min_quote_volume_usd", 0.0)
        self.cooldowns: dict[str, int] = dict(cfg["timeframes"]["cooldown_s"])
        self._last: dict[tuple[str, str, str], int] = {}

    def evaluate(self, bar: Bar, hits: list[RuleHit],
                 funding_rate: float | None, oi_pct: float | None) -> list[Signal]:
        if bar.quote_volume < self.min_quote_volume:
            return []
        long_score = sum(h.score for h in hits if _direction_of(h) == "LONG")
        short_score = sum(h.score for h in hits if _direction_of(h) == "SHORT")
        if long_score == 0 and short_score == 0:
            return []
        direction = "LONG" if long_score >= short_score else "SHORT"
        keep = [h for h in hits if _direction_of(h) in (direction, None)]
        score = sum(h.score for h in keep)
        if score < self.threshold:
            return []
        key = (bar.symbol, bar.timeframe, direction)
        cd_ms = self.cooldowns.get(bar.timeframe, 60) * 1000
        last = self._last.get(key)
        if last is not None and bar.close_ts - last <= cd_ms:
            return []
        self._last[key] = bar.close_ts
        return [Signal(bar.symbol, bar.timeframe, direction,
                       strong=score >= self.strong_threshold, score=score,
                       price=bar.close, stop=None, ts=bar.close_ts, hits=keep)]
