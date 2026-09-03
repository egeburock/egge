from src.config import tf_seconds
from src.models import Bar, RuleHit, Signal


def direction_of(hit: RuleHit) -> str | None:
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
        self.target_atr_mult = sig.get("target_atr_mult", 3.0)
        self.cooldowns: dict[str, int] = dict(cfg["timeframes"]["cooldown_s"])
        self.entry_off_r = sig.get("entry_off_r", 0.0)
        self.entry_window_bars = sig.get("entry_window_bars", 8)
        self._last: dict[tuple[str, str, str], int] = {}

    def evaluate(self, bar: Bar, hits: list[RuleHit],
                 atr: float | None = None) -> list[Signal]:
        if bar.quote_volume < self.min_quote_volume:
            return []
        long_score = sum(h.score for h in hits if direction_of(h) == "LONG")
        short_score = sum(h.score for h in hits if direction_of(h) == "SHORT")
        if long_score == 0 and short_score == 0:
            return []
        direction = "LONG" if long_score >= short_score else "SHORT"
        keep = [h for h in hits if direction_of(h) in (direction, None)]
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
        stop = target = None
        entry_limit = None
        entry_deadline = None
        if atr is not None and atr > 0:
            stop_dist = self.stop_atr_mult * atr
            stop = bar.close - stop_dist if direction == "LONG" else bar.close + stop_dist
            tgt_dist = self.target_atr_mult * atr
            target = bar.close + tgt_dist if direction == "LONG" else bar.close - tgt_dist
            if self.entry_off_r > 0:
                off = self.entry_off_r * stop_dist
                entry_limit = (bar.close - off if direction == "LONG"
                               else bar.close + off)
                window_ms = self.entry_window_bars * tf_seconds(bar.timeframe) * 1000
                entry_deadline = bar.close_ts + window_ms
        return [Signal(bar.symbol, bar.timeframe, direction,
                       strong=score >= self.strong_threshold, score=score,
                       price=bar.close, stop=stop, target=target,
                       ts=bar.close_ts, hits=keep,
                       entry_limit=entry_limit, entry_deadline=entry_deadline)]
