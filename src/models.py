from dataclasses import dataclass, field


@dataclass
class Bar:
    symbol: str
    timeframe: str
    open_ts: int          # unix ms
    close_ts: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float   # USDT ciro


@dataclass
class RuleHit:
    rule: str
    detail: str
    score: float


@dataclass
class Signal:
    symbol: str
    timeframe: str
    direction: str        # "LONG" | "SHORT"
    strong: bool
    score: float
    price: float
    stop: float | None
    target: float | None
    ts: int               # unix ms
    hits: list[RuleHit] = field(default_factory=list)
    entry_limit: float | None = None   # dolunca geçerli olan limit giriş
    entry_deadline: int | None = None  # dolmazsa iptal (unix ms)
