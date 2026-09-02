import pandas as pd

from src.config import tf_seconds
from src.models import Bar


def bars_to_df(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        [(b.open_ts, b.open, b.high, b.low, b.close, b.quote_volume) for b in bars],
        columns=["ts", "open", "high", "low", "close", "quote_volume"])


class BarAggregator:
    """Tick akışını sabit aralıklı barlara birleştirir."""

    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol
        self.timeframe = timeframe
        self.interval_ms = tf_seconds(timeframe) * 1000
        self.last_open_ts: int | None = None
        self._reset(0)

    def _bucket(self, ts_ms: int) -> int:
        return (ts_ms // self.interval_ms) * self.interval_ms

    def _reset(self, open_ts: int):
        self._open = self._high = self._low = self._close = 0.0
        self._vol = 0.0
        self._open_ts = open_ts
        self._seen = False

    def on_trade(self, ts_ms: int, price: float, quote_qty: float) -> Bar | None:
        bucket = self._bucket(ts_ms)
        closed: Bar | None = None
        if self._seen and bucket > self._open_ts:
            if bucket == self._open_ts + self.interval_ms:
                closed = Bar(self.symbol, self.timeframe, self._open_ts,
                             self._open_ts + self.interval_ms, self._open,
                             self._high, self._low, self._close, self._vol)
            self._reset(bucket)
        if not self._seen:
            self._open = self._high = self._low = self._close = price
            self._seen = True
        else:
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
        self._vol += quote_qty
        self.last_open_ts = self._open_ts
        return closed
