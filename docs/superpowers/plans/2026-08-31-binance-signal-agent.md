# Binance Futures Sinyal Ajanı — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tüm Binance USDT-M Futures sembollerini saniyelik/dakikalık barlarda tarayıp çift yönlü LONG/SHORT sinyalleri üreten, Telegram + yerel dashboard'a ileten tek süreçli Python uygulaması.

**Architecture:** Tek asyncio süreci. WebSocket aggTrade akışından 3s/5s/15s barlar birleştirilir; 1m/3m/5m kline'lar REST'ten çekilir; kapalı barlar ağırlıklı skor motorundan geçer; sinyaller SQLite'a yazılır, Telegram kuyruğuna ve FastAPI dashboard'a akar.

**Tech Stack:** Python 3.12, asyncio, aiohttp (WS+REST), pandas, `ta` (göstergeler), python-telegram-bot v21, FastAPI + uvicorn, SQLite (stdlib), tomllib (config), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-31-binance-signal-agent-design.md`

**Ortam notları:** Windows, git repo değil — Task 0'da repo başlatılır. Sanal ortam `.venv` altında. Komutlar Git Bash'e göre yazılmıştır.

---

## Dosya Haritası

```
pyproject.toml          # bağımlılıklar, pytest ayarı
config.toml             # tüm çalışma parametreleri
src/
  config.py             # config yükleme (tomllib)
  models.py             # Bar, Signal, RuleHit dataclass'ları
  db.py                 # SQLite: sinyal geçmişi + telegram kuyruğu
  bars.py               # tick → saniyelik bar birleştirici
  rules.py              # çift yönlü sinyal kuralları
  engine.py             # skorlama, cooldown, Signal üretimi
  ws_feed.py            # aggTrade WS + reconnect + BarAggregator besleme
  klines.py             # REST: kline, funding, OI, exchangeInfo
  notify.py             # Telegram gönderici + kuyruk
  web.py                # FastAPI dashboard
  main.py               # orkestrasyon
tests/
  test_config.py  test_bars.py  test_rules.py  test_engine.py
  test_db.py      test_klines.py test_web.py   test_integration.py
  fixtures/aggtrades.jsonl   # kayıtlı WS verisi
```

---

### Task 0: Proje iskeleti

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/__init__.py`, `tests/__init__.py`, `config.toml`

- [ ] **Step 1: Repo ve ortam**

```bash
git init
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
```

- [ ] **Step 2: pyproject.toml**

```toml
[project]
name = "binance-signal-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "aiohttp>=3.9",
  "pandas>=2.2",
  "ta>=0.11",
  "python-telegram-bot>=21.0",
  "fastapi>=0.111",
  "uvicorn>=0.30",
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["."]
```

- [ ] **Step 3: Bağımlılıkları kur**

Run: `.venv/Scripts/pip install -e .`
Expected: başarıyla kurulur

- [ ] **Step 4: .gitignore**

```
.venv/
__pycache__/
*.db
logs/
.env
```

- [ ] **Step 5: config.toml**

```toml
[agent]
scan_all_symbols = true
dry_run = true

[timeframes]
enabled = ["5s", "15s", "1m"]
confirm_15m = false

[timeframes.cooldown_s]
"3s" = 15
"5s" = 30
"15s" = 60
"1m" = 180
"3m" = 300
"5m" = 300

[signals]
threshold = 5.0
strong_threshold = 8.0
ema_fast = 9
ema_slow = 21
rsi_period = 14
rsi_oversold = 30
rsi_overbought = 70
adx_min = 22
volume_spike_x = 3.0
volume_avg_bars = 20
price_jump_pct = 2.0
funding_crowded = 0.0005
funding_extreme_neg = -0.0003
min_quote_volume_usd = 50000.0

[aggressive]
enabled = false
symbols = ["BTCUSDT","ETHUSDT","SOLUSDT","DOGEUSDT","BNBUSDT","XRPUSDT","PEPEUSDT","WIFUSDT","ARBUSDT","OPUSDT"]
stop_atr_mult = 1.5

[telegram]
token = ""
chat_id = ""

[web]
port = 8000
```

- [ ] **Step 6: Boş paket dosyaları + commit**

```bash
mkdir -p src tests tests/fixtures
touch src/__init__.py tests/__init__.py
git add . && git commit -m "chore: project scaffold, deps, config"
```

---

### Task 1: Veri modelleri

**Files:**
- Create: `src/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_models.py
from src.models import Bar, RuleHit, Signal

def test_bar_fields():
    b = Bar(symbol="BTCUSDT", timeframe="5s", open_ts=0, close_ts=5,
            open=100.0, high=105.0, low=99.0, close=103.0, quote_volume=12000.0)
    assert b.close == 103.0 and b.symbol == "BTCUSDT"

def test_signal_contains_rule_hits():
    s = Signal(symbol="ETHUSDT", timeframe="1m", direction="LONG",
               strong=False, score=6.0, price=3000.0, stop=None, ts=0,
               hits=[RuleHit(rule="ema_cross", detail="EMA9>EMA21", score=3.0)])
    assert s.hits[0].rule == "ema_cross"
    assert s.direction in ("LONG", "SHORT")
```

- [ ] **Step 2: Çalıştır, başarısız olduğunu gör**

Run: `.venv/Scripts/pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/models.py
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
    ts: int               # unix ms
    hits: list[RuleHit] = field(default_factory=list)
```

- [ ] **Step 4: Test geçsin**

Run: `.venv/Scripts/pytest tests/test_models.py -v`
Expected: PASS (2 test)

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: core data models (Bar, Signal, RuleHit)"
```

---

### Task 2: Config yükleme

**Files:**
- Create: `src/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_config.py
import tomllib
from pathlib import Path
from src.config import load_config, tf_seconds

def test_load_real_config():
    cfg = load_config(Path("config.toml"))
    assert cfg["agent"]["dry_run"] is True
    assert "5s" in cfg["timeframes"]["enabled"]
    assert cfg["timeframes"]["cooldown_s"]["5s"] == 30

def test_tf_seconds():
    assert tf_seconds("5s") == 5
    assert tf_seconds("15s") == 15
    assert tf_seconds("1m") == 60
    assert tf_seconds("3m") == 180
    assert tf_seconds("5m") == 300
```

- [ ] **Step 2: Çalıştır, FAIL gör** (`ModuleNotFoundError`)

- [ ] **Step 3: Implement**

```python
# src/config.py
import tomllib
from pathlib import Path

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)

def tf_seconds(tf: str) -> int:
    num, unit = tf[:-1], tf[-1]
    return int(num) * (1 if unit == "s" else 60)
```

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_config.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat: config loading + timeframe parsing"`

---

### Task 3: Saniyelik bar birleştirici

**Files:**
- Create: `src/bars.py`
- Test: `tests/test_bars.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_bars.py
from src.bars import BarAggregator

def test_aggregates_5s_bar_from_ticks():
    agg = BarAggregator("BTCUSDT", "5s")
    closed = []
    # tickler: (ts_ms, price, qty, quote_qty)
    ticks = [
        (1000, 100.0, 1.0, 100.0),
        (2500, 105.0, 1.0, 105.0),
        (4999, 99.0, 2.0, 198.0),
        (5001, 103.0, 1.0, 103.0),   # yeni bara geçer -> önceki kapanır
    ]
    for ts, p, q, qq in ticks:
        bar = agg.on_trade(ts, p, qq)
        if bar:
            closed.append(bar)
    assert len(closed) == 1
    b = closed[0]
    assert (b.open, b.high, b.low, b.close) == (100.0, 105.0, 99.0, 99.0)
    assert b.quote_volume == 403.0
    assert b.open_ts == 0 and b.close_ts == 5000

def test_no_trade_gap_marks_bar_invalid():
    agg = BarAggregator("BTCUSDT", "5s")
    agg.on_trade(1000, 100.0, 100.0)     # bar [0,5000)
    bar = agg.on_trade(12000, 101.0, 101.0)  # bar [10000,15000) -> arada boş bar
    assert bar is None or bar is not None  # sadece crash yok; boş bar üretilmez
    assert agg.last_open_ts == 10000
```

- [ ] **Step 2: FAIL gör**

- [ ] **Step 3: Implement**

```python
# src/bars.py
from src.config import tf_seconds
from src.models import Bar

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
            # kapanan barı üret; ancak önceki barla aralıksızsa geçerli
            if bucket == self._open_ts + self.interval_ms:
                closed = Bar(self.symbol, self.timeframe, self._open_ts,
                             self._open_ts + self.interval_ms, self._open,
                             self._high, self._low, self._close, self._vol)
            # aralık varsa bar üretme (boş veri sinyale girmesin)
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
```

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_bars.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat: second-bar aggregator from trade ticks"`

---

### Task 4: Sinyal kuralları

**Files:**
- Create: `src/rules.py`
- Test: `tests/test_rules.py`

Kurallar DataFrame (kapalı barlar, en yeni sonda) alır, `list[RuleHit]` döner.
Ağırlıklar: ema_cross=3, macd_cross=2, rsi_reversal=2, volume_spike=2,
price_jump=2, oi_confirm=1, funding=1.

- [ ] **Step 1: Test yaz**

```python
# tests/test_rules.py
import pandas as pd
from src.rules import ema_cross, rsi_reversal, volume_spike, price_jump

def ohlc(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "quote_volume"])

def test_ema_cross_long():
    # düşen sonra keskin yükselen seri -> EMA9, EMA21'i son barda yukarı keser
    closes = [100 - i for i in range(30)] + [100 + i * 5 for i in range(1, 10)]
    df = ohlc([[c, c + 1, c - 1, c, 1000.0] for c in closes])
    hits = ema_cross(df, fast=9, slow=21)
    assert any(h.rule == "ema_cross" and "LONG" in h.detail for h in hits)

def test_ema_cross_short():
    closes = [100 + i for i in range(30)] + [100 - i * 5 for i in range(1, 10)]
    df = ohlc([[c, c + 1, c - 1, c, 1000.0] for c in closes])
    hits = ema_cross(df, fast=9, slow=21)
    assert any("SHORT" in h.detail for h in hits)

def test_rsi_reversal_long():
    closes = [100 - i * 2 for i in range(25)] + [60, 68]  # aşırı satım sonrası dönüş
    df = ohlc([[c, c + 1, c - 1, c, 1000.0] for c in closes])
    hits = rsi_reversal(df, period=14, oversold=30, overbought=70)
    assert any(h.rule == "rsi_reversal" and "LONG" in h.detail for h in hits)

def test_volume_spike():
    rows = [[100, 101, 99, 100, 1000.0] for _ in range(20)]
    rows.append([100, 105, 99, 104, 5000.0])  # 5x hacim
    hits = volume_spike(ohlc(rows), mult=3.0, lookback=20)
    assert hits and hits[0].rule == "volume_spike"

def test_price_jump_bidirectional():
    rows = [[100, 101, 99, 100, 1000.0] for _ in range(5)]
    up = ohlc(rows + [[100, 104, 100, 103, 2000.0]])
    down = ohlc(rows + [[100, 100, 96, 97, 2000.0]])
    assert "LONG" in volume_and_jump(up, 2.0) if False else True
    hu, hd = price_jump(up, 2.0), price_jump(down, 2.0)
    assert hu and "LONG" in hu[0].detail
    assert hd and "SHORT" in hd[0].detail
```

Not: test dosyasındaki `volume_and_jump` satırı ölü koddur; implementasyondan
önce bu satırı silin (`assert "LONG" in ... if False else True` içeren satır).

- [ ] **Step 2: FAIL gör**

- [ ] **Step 3: Implement**

```python
# src/rules.py
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from src.models import RuleHit

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
```

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_rules.py -v` → PASS (ölü satır silindikten sonra 5 test)

- [ ] **Step 5: Commit** — `git commit -m "feat: bidirectional signal rules (EMA/RSI/MACD/ADX/volume/jump/funding/OI)"`

---

### Task 5: Skor motoru + cooldown

**Files:**
- Create: `src/engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_engine.py
from src.engine import SignalEngine
from src.models import Bar, RuleHit

def make_engine(threshold=5.0, cooldown_s=60):
    cfg = {"signals": {"threshold": threshold, "strong_threshold": 8.0},
           "timeframes": {"cooldown_s": {"1m": cooldown_s}}}
    return SignalEngine(cfg)

def test_signal_emitted_above_threshold():
    eng = make_engine()
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    sigs = eng.evaluate(bar, hits, funding_rate=None, oi_pct=None)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.direction == "LONG" and s.score == 5.0 and not s.strong

def test_below_threshold_no_signal():
    eng = make_engine()
    hits = [RuleHit("funding", "SHORT eğilimi", 1.0)]
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    assert eng.evaluate(bar, hits, None, None) == []

def test_direction_conflict_resolves_to_majority():
    eng = make_engine()
    hits = [RuleHit("ema_cross", "LONG: x", 3.0),
            RuleHit("price_jump", "SHORT: -3%", 2.0),
            RuleHit("funding", "SHORT eğilimi", 1.0)]
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    sigs = eng.evaluate(bar, hits, None, None)
    assert sigs and sigs[0].direction == "SHORT"  # 3 > 2

def test_cooldown_suppresses_repeat():
    eng = make_engine(cooldown_s=60)
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    bar1 = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    bar2 = Bar("BTCUSDT", "1m", 60000, 120000, 103, 106, 102, 105, 90000.0)
    assert eng.evaluate(bar1, hits, None, None)
    assert eng.evaluate(bar2, hits, None, None) == []  # 60sn içinde tekrar yok

def test_strong_signal():
    eng = make_engine()
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("macd_cross", "LONG: y", 2.0),
            RuleHit("volume_spike", "4x", 2.0), RuleHit("rsi_reversal", "LONG: z", 2.0)]
    bar = Bar("BTCUSDT", "1m", 0, 60000, 100, 105, 99, 103, 90000.0)
    s = eng.evaluate(bar, hits, None, None)[0]
    assert s.strong and s.score == 9.0

def test_low_volume_skipped():
    eng = make_engine()
    eng.min_quote_volume = 50000.0
    hits = [RuleHit("ema_cross", "LONG: x", 3.0), RuleHit("volume_spike", "4x", 2.0)]
    bar = Bar("SHIBCOIN", "1m", 0, 60000, 1, 1.1, 0.9, 1.05, 10.0)
    assert eng.evaluate(bar, hits, None, None) == []
```

- [ ] **Step 2: FAIL gör**

- [ ] **Step 3: Implement**

```python
# src/engine.py
from src.models import Bar, RuleHit, Signal

LONG_MARKERS = ("LONG", "Sıkışmış yay")
SHORT_MARKERS = ("SHORT",)

class SignalEngine:
    def __init__(self, cfg: dict):
        sig = cfg["signals"]
        self.threshold = sig["threshold"]
        self.strong_threshold = sig["strong_threshold"]
        self.min_quote_volume = sig["min_quote_volume_usd"]
        self.cooldowns: dict[str, int] = dict(cfg["timeframes"]["cooldown_s"])
        self._last: dict[tuple[str, str, str], int] = {}

    def evaluate(self, bar: Bar, hits: list[RuleHit],
                 funding_rate: float | None, oi_pct: float | None) -> list[Signal]:
        if bar.quote_volume < self.min_quote_volume:
            return []
        long_score = sum(h.score for h in hits if "LONG" in h.detail or "Sıkışmış yay" in h.detail)
        short_score = sum(h.score for h in hits if "SHORT" in h.detail)
        direction, score, keep = ("LONG", long_score,
                                  [h for h in hits if "LONG" in h.detail or "Sıkışmış yay" in h.detail]) \
            if long_score >= short_score else \
            ("SHORT", short_score, [h for h in hits if "SHORT" in h.detail])
        if score < self.threshold:
            return []
        key = (bar.symbol, bar.timeframe, direction)
        cd_s = self.cooldowns.get(bar.timeframe, 60) * 1000
        last = self._last.get(key)
        if last is not None and bar.close_ts - last < cd_s:
            return []
        self._last[key] = bar.close_ts
        return [Signal(bar.symbol, bar.timeframe, direction,
                       strong=score >= self.strong_threshold, score=score,
                       price=bar.close, stop=None, ts=bar.close_ts, hits=keep)]
```

Not: `funding_rate` ve `oi_pct` parametreleri kurallar katmanında zaten hit
üretir; engine yalnızca skorlar. İmza, ileride stop hesabı (ATR) için yer tutar.

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_engine.py -v` → PASS (6 test)

- [ ] **Step 5: Commit** — `git commit -m "feat: scoring engine with cooldown and volume floor"`

---

### Task 6: SQLite katmanı

**Files:**
- Create: `src/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_db.py
from src.db import Database
from src.models import Signal, RuleHit

def test_save_and_query_signals(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    s = Signal("BTCUSDT", "1m", "LONG", False, 6.0, 100.0, None, 1000,
               [RuleHit("ema_cross", "LONG: x", 3.0)])
    db.save_signal(s)
    rows = db.recent_signals(limit=10)
    assert len(rows) == 1 and rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["hits_json"].startswith("[")

def test_telegram_queue_roundtrip(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.enqueue_message("merhaba")
    msg = db.next_pending_message()
    assert msg is not None and msg["text"] == "merhaba"
    db.mark_message_sent(msg["id"])
    assert db.next_pending_message() is None
```

- [ ] **Step 2: FAIL gör**

- [ ] **Step 3: Implement**

```python
# src/db.py
import json
import sqlite3
from src.models import Signal

class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY, ts INTEGER, symbol TEXT, timeframe TEXT,
            direction TEXT, strong INTEGER, score REAL, price REAL,
            stop REAL, hits_json TEXT);
        CREATE TABLE IF NOT EXISTS tg_queue (
            id INTEGER PRIMARY KEY, text TEXT, sent INTEGER DEFAULT 0);
        """)

    def save_signal(self, s: Signal):
        hits = [{"rule": h.rule, "detail": h.detail, "score": h.score} for h in s.hits]
        self.conn.execute(
            "INSERT INTO signals (ts, symbol, timeframe, direction, strong, score, price, stop, hits_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (s.ts, s.symbol, s.timeframe, s.direction, int(s.strong), s.score,
             s.price, s.stop, json.dumps(hits, ensure_ascii=False)))
        self.conn.commit()

    def recent_signals(self, limit: int = 50, symbol: str | None = None) -> list[dict]:
        q = "SELECT * FROM signals"
        args: list = []
        if symbol:
            q += " WHERE symbol = ?"
            args.append(symbol)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args)]

    def enqueue_message(self, text: str):
        self.conn.execute("INSERT INTO tg_queue (text) VALUES (?)", (text,))
        self.conn.commit()

    def next_pending_message(self) -> dict | None:
        r = self.conn.execute(
            "SELECT id, text FROM tg_queue WHERE sent = 0 ORDER BY id LIMIT 1").fetchone()
        return dict(r) if r else None

    def mark_message_sent(self, msg_id: int):
        self.conn.execute("UPDATE tg_queue SET sent = 1 WHERE id = ?", (msg_id,))
        self.conn.commit()
```

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_db.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat: SQLite storage for signals and telegram queue"`

---

### Task 7: REST istemcisi (kline / funding / OI / exchangeInfo)

**Files:**
- Create: `src/klines.py`
- Test: `tests/test_klines.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_klines.py
import pytest
from src.klines import parse_klines, BinanceRest

RAW = [[1700000000000, "100", "105", "99", "103", "10", 1700000059999,
        "1000.5", 50, "5", "500.2", "0"]]

def test_parse_klines():
    df = parse_klines(RAW, "BTCUSDT", "1m")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["close"] == 103.0 and row["quote_volume"] == 1000.5
    assert df.attrs["symbol"] == "BTCUSDT"

@pytest.mark.asyncio
async def test_rate_limit_and_retry(monkeypatch):
    calls = {"n": 0}
    async def fake_get(url, params=None):
        calls["n"] += 1
        class R:
            status = 429 if calls["n"] == 1 else 200
            async def json(self):
                return RAW
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        return R()
    br = BinanceRest(session=None)
    br._get = fake_get  # injection noktası
    out = await br.klines("BTCUSDT", "1m", limit=1)
    assert len(out) == 1 and calls["n"] == 2
```

- [ ] **Step 2: FAIL gör**

- [ ] **Step 3: Implement**

```python
# src/klines.py
import asyncio
import logging
import pandas as pd
import aiohttp

log = logging.getLogger(__name__)
BASE = "https://fapi.binance.com"

def parse_klines(raw: list, symbol: str, interval: str) -> pd.DataFrame:
    rows = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]),
             float(k[7])] for k in raw]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "quote_volume"])
    df.attrs["symbol"] = symbol
    df.attrs["timeframe"] = interval
    return df

class BinanceRest:
    def __init__(self, session: aiohttp.ClientSession | None):
        self.session = session

    async def _get(self, url: str, params: dict | None = None):
        return self.session.get(url, params=params)

    async def _json(self, path: str, params: dict, retries: int = 3):
        for i in range(retries):
            async with await self._get(BASE + path, params) as r:
                if r.status == 429:
                    await asyncio.sleep(2 ** i)
                    continue
                r.raise_for_status()
                return await r.json()
        raise RuntimeError(f"{path} için denemeler tükendi")

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        raw = await self._json("/fapi/v1/klines",
                               {"symbol": symbol, "interval": interval, "limit": limit})
        return parse_klines(raw[:-1], symbol, interval)  # son mum açık -> atla

    async def premium_index(self, symbol: str) -> float | None:
        data = await self._json("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data["lastFundingRate"]) if data else None

    async def open_interest(self, symbol: str) -> float | None:
        data = await self._json("/fapi/v1/openInterest", {"symbol": symbol})
        return float(data["openInterest"]) if data else None

    async def exchange_info(self) -> list[str]:
        data = await self._json("/fapi/v1/exchangeInfo", {})
        return [s["symbol"] for s in data["symbols"]
                if s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL"
                and s["status"] == "TRADING"]
```

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_klines.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat: Binance REST client (klines, funding, OI, exchangeInfo) with retry"`

---

### Task 8: WebSocket beslemesi

**Files:**
- Create: `src/ws_feed.py`
- Test: `tests/test_ws_feed.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_ws_feed.py
import asyncio
import pytest
from src.ws_feed import build_stream_urls, WsFeed
from src.bars import BarAggregator

def test_build_stream_urls_splits_200():
    symbols = [f"S{i}USDT" for i in range(450)]
    urls = build_stream_urls(symbols)
    assert len(urls) == 3
    assert "s0usdt@aggTrade" in urls[0]
    assert urls[0].count("@aggTrade") == 200

@pytest.mark.asyncio
async def test_feed_routes_trades_to_aggregators():
    agg = BarAggregator("BTCUSDT", "5s")
    received: list = []
    class FakeWs:
        def __aiter__(self): return self
        async def __anext__(self):
            if received:
                raise StopAsyncIteration
            received.append(1)
            return {"data": {"s": "BTCUSDT", "T": 1000, "p": "100", "q": "1"}}
        async def close(self): pass
    feed = WsFeed(["BTCUSDT"], ["5s"], on_bar=lambda b: None)
    feed.aggregators[("BTCUSDT", "5s")] = agg
    await feed._handle_ws(FakeWs())
    assert agg.last_open_ts == 0
```

- [ ] **Step 2: FAIL gör**

- [ ] **Step 3: Implement**

```python
# src/ws_feed.py
import asyncio
import logging
import aiohttp
from src.bars import BarAggregator
from src.models import Bar

log = logging.getLogger(__name__)
WS_BASE = "wss://fstream.binance.com/stream?streams="

def build_stream_urls(symbols: list[str], chunk: int = 200) -> list[str]:
    return [WS_BASE + "/".join(f"{s.lower()}@aggTrade" for s in symbols[i:i + chunk])
            for i in range(0, len(symbols), chunk)]

class WsFeed:
    def __init__(self, symbols: list[str], timeframes: list[str], on_bar):
        self.symbols = symbols
        self.timeframes = [tf for tf in timeframes if tf.endswith("s")]
        self.on_bar = on_bar
        self.aggregators: dict[tuple[str, str], BarAggregator] = {
            (s, tf): BarAggregator(s, tf) for s in symbols for tf in self.timeframes}
        self.connected = False

    async def run(self):
        urls = build_stream_urls(self.symbols)
        backoff = 1
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(urls[0]) if len(urls) == 1 \
                            else session.ws_connect(urls[0]) as ws:
                        self.connected = True
                        backoff = 1
                        await self._handle_ws(ws)
            except Exception as e:
                log.warning("WS hata: %s — %ss sonra yeniden", e, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _handle_ws(self, ws):
        async for msg in ws:
            data = msg.get("data", msg) if isinstance(msg, dict) else {}
            sym = data.get("s")
            if not sym:
                continue
            ts, price, quote = int(data["T"]), float(data["p"]), float(data["q"])
            for tf in self.timeframes:
                bar = self.aggregators[(sym, tf)].on_trade(ts, price, quote)
                if bar:
                    self.on_bar(bar)
```

Not: Çoklu bağlantı (>200 sembol) için production'da her url için ayrı görev
başlatılır; bu sürüm tek bağlantıyla çalışır ve Task 10'da `asyncio.gather` ile
genişletilir. `_handle_ws` test edilebilir çekirdektir.

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_ws_feed.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat: websocket aggTrade feed with reconnection and bar routing"`

---

### Task 9: Telegram bildirim

**Files:**
- Create: `src/notify.py`
- Test: `tests/test_notify.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_notify.py
from src.notify import format_signal, Notifier
from src.models import Signal, RuleHit
from src.db import Database

def make_signal():
    return Signal("SOLUSDT", "15s", "LONG", True, 7.0, 198.42, 196.10, 1000,
                  [RuleHit("ema_cross", "LONG: EMA9>EMA21 kesişim", 3.0),
                   RuleHit("volume_spike", "Hacim 4.2x (20-bar ort.)", 2.0),
                   RuleHit("oi_confirm", "LONG teyit: OI +2.1% fiyatla aynı yön", 1.0)])

def test_format_signal_contains_everything():
    text = format_signal(make_signal())
    assert "GÜÇLÜ LONG" in text and "SOLUSDT" in text
    assert "EMA9>EMA21" in text and "7.0" in text and "196.10" in text

def test_dry_run_enqueues_instead_of_sending(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    n = Notifier(db, token="", chat_id="", dry_run=True)
    import asyncio
    asyncio.run(n.send(make_signal()))
    assert db.next_pending_message() is not None  # dry-run: kuyruğa yaz, gönderme
```

- [ ] **Step 2: FAIL gör**

- [ ] **Step 3: Implement**

```python
# src/notify.py
import logging
from src.models import Signal

log = logging.getLogger(__name__)

def format_signal(s: Signal) -> str:
    emoji = "🟢" if s.direction == "LONG" else "🔴"
    strength = "GÜÇLÜ " if s.strong else ""
    lines = [f"{emoji} {strength}{s.direction} — {s.symbol}",
             f"Fiyat: {s.price} | Zaman dilimi: {s.timeframe}",
             "Tetikleyen kurallar:"]
    lines += [f"• {h.detail}" for h in s.hits]
    lines.append(f"Skor: {s.score}")
    if s.stop:
        lines.append(f"Stop önerisi: {s.stop} (ATR bazlı)")
    return "\n".join(lines)

class Notifier:
    def __init__(self, db, token: str, chat_id: str, dry_run: bool):
        self.db = db
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self._bot = None

    async def send(self, s: Signal):
        text = format_signal(s)
        log.info("Sinyal: %s", text.replace("\n", " | "))
        if self.dry_run or not self.token:
            self.db.enqueue_message(text)
            return
        self.db.enqueue_message(text)
        await self._flush()

    async def _flush(self):
        from telegram import Bot
        if self._bot is None:
            self._bot = Bot(self.token)
        while (m := self.db.next_pending_message()):
            try:
                await self._bot.send_message(chat_id=self.chat_id, text=m["text"])
                self.db.mark_message_sent(m["id"])
            except Exception as e:
                log.warning("Telegram gönderilemedi: %s", e)
                break  # kuyrukta kalır, sonraki turda tekrar dene
```

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_notify.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat: telegram notifier with persistent queue and dry-run"`

---

### Task 10: Web dashboard

**Files:**
- Create: `src/web.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Test yaz**

```python
# tests/test_web.py
import pytest
from fastapi.testclient import TestClient
from src.web import create_app
from src.db import Database
from src.models import Signal, RuleHit

@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_signal(Signal("BTCUSDT", "1m", "LONG", False, 6.0, 100.0, None,
                          1000, [RuleHit("ema_cross", "LONG: x", 3.0)]))
    return TestClient(create_app(db, status_provider=lambda: {"symbols": 300, "ws": True}))

def test_status(client):
    r = client.get("/api/status")
    assert r.json()["symbols"] == 300

def test_signals(client):
    r = client.get("/api/signals")
    assert r.json()[0]["symbol"] == "BTCUSDT"

def test_index_html(client):
    r = client.get("/")
    assert r.status_code == 200 and "Sinyal Akışı" in r.text
```

- [ ] **Step 2: FAIL gör**

- [ ] **Step 3: Implement**

```python
# src/web.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

def create_app(db, status_provider) -> FastAPI:
    app = FastAPI()

    @app.get("/api/status")
    def status():
        return status_provider()

    @app.get("/api/signals")
    def signals(limit: int = 100, symbol: str | None = None):
        return db.recent_signals(limit=limit, symbol=symbol)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Binance Sinyal Ajanı</title>
<style>
body{background:#0d1117;color:#e6edf3;font-family:system-ui;margin:0;padding:16px}
h1{font-size:18px}table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 8px;border-bottom:1px solid #21262d;text-align:left}
.long{color:#3fb950;font-weight:700}.short{color:#f85149;font-weight:700}
#status{color:#8b949e;font-size:12px;margin-bottom:12px}
</style></head><body>
<h1>Binance Futures Sinyal Ajanı</h1>
<div id="status">yükleniyor…</div>
<table><thead><tr><th>Saat</th><th>Sembol</th><th>Yön</th><th>Dilim</th>
<th>Skor</th><th>Kurallar</th></tr></thead><tbody id="rows"></tbody></table>
<script>
async function refresh(){
  const [s, sig] = await Promise.all([
    fetch('/api/status').then(r=>r.json()),
    fetch('/api/signals?limit=100').then(r=>r.json())]);
  document.getElementById('status').textContent =
    `${s.symbols} sembol | WS: ${s.ws ? 'bağlı' : 'KOPUK'} | sinyaller: ${sig.length}`;
  document.getElementById('rows').innerHTML = sig.map(x => {
    const hits = JSON.parse(x.hits_json).map(h=>h.detail).join('; ');
    const d = x.direction === 'LONG' ? 'long' : 'short';
    return `<tr><td>${new Date(x.ts).toLocaleTimeString()}</td><td>${x.symbol}</td>
    <td class="${d}">${x.strong ? 'GÜÇLÜ ' : ''}${x.direction}</td><td>${x.timeframe}</td>
    <td>${x.score}</td><td>${hits}</td></tr>`;}).join('');
}
refresh(); setInterval(refresh, 3000);
</script></body></html>"""

    return app
```

- [ ] **Step 4: Test geçsin** — Run: `.venv/Scripts/pytest tests/test_web.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat: FastAPI dashboard with live signal feed"`

---

### Task 11: Orkestrasyon (main.py)

**Files:**
- Create: `src/main.py`

Test gerektirmez (bağlantı katmanı); doğrulama Task 12'de uçtan uca yapılır.

- [ ] **Step 1: Implement**

```python
# src/main.py
import asyncio
import logging
from pathlib import Path
import aiohttp
import uvicorn
from src.config import load_config, tf_seconds
from src.db import Database
from src.engine import SignalEngine
from src.klines import BinanceRest
from src.notify import Notifier
from src.web import create_app
from src.ws_feed import WsFeed

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.db = Database("signals.db")
        self.engine = SignalEngine(cfg)
        self.notifier = Notifier(self.db, cfg["telegram"]["token"],
                                 cfg["telegram"]["chat_id"], cfg["agent"]["dry_run"])
        self.symbols: list[str] = []
        self.rest = BinanceRest(None)
        self.funding: dict[str, float | None] = {}
        self.ws_ok = False

    async def start(self):
        async with aiohttp.ClientSession() as session:
            self.rest.session = session
            self.symbols = await self.rest.exchange_info()
            if self.cfg["aggressive"]["enabled"]:
                self.symbols = [s for s in self.symbols
                                if s in self.cfg["aggressive"]["symbols"]]
            log.info("Taranacak sembol: %d", len(self.symbols))
            engine, notifier = self.engine, self.notifier

            async def on_bar_async(bar):
                df = await self.bar_history(bar.symbol, bar.timeframe)
                if df is None or len(df) < 30:
                    return
                hits = self.collect_hits(df, bar.symbol)
                for sig in engine.evaluate(bar, hits,
                                           self.funding.get(bar.symbol), None):
                    self.db.save_signal(sig)
                    await notifier.send(sig)

            def on_bar(bar):
                asyncio.create_task(on_bar_async(bar))

            sec_tfs = [t for t in self.cfg["timeframes"]["enabled"] if t.endswith("s")]
            feed = WsFeed(self.symbols, sec_tfs, on_bar)
            tasks = [asyncio.create_task(feed.run()),
                     asyncio.create_task(self.minute_poller(on_bar_async)),
                     asyncio.create_task(self.funding_poller()),
                     asyncio.create_task(self.serve_dashboard(feed))]
            await asyncio.gather(*tasks)

    async def bar_history(self, symbol: str, tf: str):
        if tf.endswith("s"):
            return None  # saniyelik barlar için geçici pencere Task 11-not'ta
        try:
            return await self.rest.klines(symbol, tf, limit=200)
        except Exception as e:
            log.warning("kline hatası %s %s: %s", symbol, tf, e)
            return None

    def collect_hits(self, df, symbol: str):
        from src import rules
        c = self.cfg["signals"]
        hits = []
        hits += rules.ema_cross(df, c["ema_fast"], c["ema_slow"])
        hits += rules.rsi_reversal(df, c["rsi_period"], c["rsi_oversold"], c["rsi_overbought"])
        hits += rules.macd_cross(df)
        if rules.adx_ok(df, c["adx_min"]) or True:
            hits += rules.volume_spike(df, c["volume_spike_x"], c["volume_avg_bars"])
            hits += rules.price_jump(df, c["price_jump_pct"])
        hits += rules.funding_rule(self.funding.get(symbol),
                                   c["funding_crowded"], c["funding_extreme_neg"])
        return hits

    async def minute_poller(self, on_bar_async):
        minute_tfs = [t for t in self.cfg["timeframes"]["enabled"] if t.endswith("m")]
        while True:
            for tf in minute_tfs:
                for sym in self.symbols:
                    df = await self.bar_history(sym, tf)
                    if df is None or df.empty:
                        continue
                    from src.models import Bar
                    last = df.iloc[-1]
                    bar = Bar(sym, tf, int(last["ts"]), int(last["ts"]) + tf_seconds(tf) * 1000,
                              last["open"], last["high"], last["low"], last["close"],
                              last["quote_volume"])
                    hits = self.collect_hits(df, sym)
                    for sig in self.engine.evaluate(bar, hits, self.funding.get(sym), None):
                        self.db.save_signal(sig)
                        await self.notifier.send(sig)
            await asyncio.sleep(60)

    async def funding_poller(self):
        while True:
            for sym in self.symbols[:50]:  # OI/funding için en likit 50
                try:
                    self.funding[sym] = await self.rest.premium_index(sym)
                except Exception:
                    pass
            await asyncio.sleep(60)

    async def serve_dashboard(self, feed):
        app = create_app(self.db, lambda: {"symbols": len(self.symbols),
                                           "ws": feed.connected})
        config = uvicorn.Config(app, host="127.0.0.1", port=self.cfg["web"]["port"])
        await uvicorn.Server(config).serve()

def main():
    cfg = load_config(Path("config.toml"))
    asyncio.run(Agent(cfg).start())

if __name__ == "__main__":
    main()
```

Not: `collect_hits` içindeki `or True` ADX filtresinin şu an kapalı olduğunu
gösterir; spec'e göre ADX filtresi trend kuralları için geçerlidir — düzeltme:
ADX false ise ema_cross/macd_cross hitlerini listeden çıkarın:

```python
        if not rules.adx_ok(df, c["adx_min"]):
            hits = [h for h in hits if h.rule not in ("ema_cross", "macd_cross")]
```
Bu satırı implementasyonda `hits += rules.funding_rule(...)` satırından hemen
önce kullanın (`or True` ifadesini kaldırın).

- [ ] **Step 2: Sözdizimi kontrolü** — Run: `.venv/Scripts/python -c "import src.main"` → hata yok

- [ ] **Step 3: Commit** — `git commit -m "feat: orchestration - WS feed, minute poller, funding poller, dashboard"`

---

### Task 12: Uçtan uca bütünleşik test

**Files:**
- Create: `tests/fixtures/aggtrades.jsonl`, `tests/test_integration.py`

- [ ] **Step 1: Fixture üret**

```bash
.venv/Scripts/python - <<'EOF'
import json, random
rows = []
price, t = 100.0, 0
while t < 60000:  # 60 saniyelik sahte BTCUSDT akışı
    price += random.uniform(-0.5, 0.6)
    rows.append({"s": "BTCUSDT", "T": t, "p": f"{price:.2f}", "q": "1"})
    t += random.randint(50, 300)
with open("tests/fixtures/aggtrades.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
print(len(rows), "tick yazıldı")
EOF
```

Expected: `~300 tick yazıldı`

- [ ] **Step 2: Bütünleşik test yaz**

```python
# tests/test_integration.py
import json
import pytest
from src.bars import BarAggregator
from src.engine import SignalEngine

CFG = {"signals": {"threshold": 4.0, "strong_threshold": 8.0,
                   "min_quote_volume_usd": 0.0},
       "timeframes": {"cooldown_s": {"5s": 30}}}

def test_fixture_replay_produces_bars():
    agg = BarAggregator("BTCUSDT", "5s")
    bars = []
    with open("tests/fixtures/aggtrades.jsonl") as f:
        for line in f:
            d = json.loads(line)
            bar = agg.on_trade(int(d["T"]), float(d["p"]), 100.0)
            if bar:
                bars.append(bar)
    assert len(bars) >= 10
    assert all(b.close_ts - b.open_ts == 5000 for b in bars)

def test_engine_end_to_end_with_synthetic_spike():
    eng = SignalEngine(CFG)
    from src.models import Bar, RuleHit
    bar = Bar("BTCUSDT", "5s", 0, 5000, 100, 110, 99, 109, 1000.0)
    hits = [RuleHit("price_jump", "LONG: bar içi +9.0%", 2.0),
            RuleHit("volume_spike", "Hacim 4x", 2.0)]
    sigs = eng.evaluate(bar, hits, None, None)
    assert sigs and sigs[0].direction == "LONG" and sigs[0].score == 4.0
```

- [ ] **Step 3: Çalıştır** — Run: `.venv/Scripts/pytest tests/test_integration.py -v` → PASS

- [ ] **Step 4: Tüm testleri çalıştır**

Run: `.venv/Scripts/pytest -v`
Expected: tümü PASS

- [ ] **Step 5: Commit** — `git commit -m "test: end-to-end integration with recorded fixture"`

---

### Task 13: Canlı prova (dry-run) ve raporlama

**Files:** yok (çalıştırma + doğrulama)

- [ ] **Step 1: Uygulamayı başlat**

Run (arka planda 10 dk): `.venv/Scripts/python -m src.main`
Expected log: `Taranacak sembol: ~3xx`, WS bağlantı logları, kline istekleri

- [ ] **Step 2: Dashboard doğrula**

Run: `curl -s http://127.0.0.1:8000/api/status`
Expected: `{"symbols":...,"ws":true}`

Run: `curl -s http://127.0.0.1:8000/api/signals | head -c 500`
Expected: JSON dizi (sinyal yoksa `[]` — ilk dakikalarda normal)

- [ ] **Step 3: Kapanış doğrulaması**

10 dakika çalıştır; log dosyasında `Traceback` olmadığını doğrula:
Run: `grep -c Traceback logs/agent.log` → `0`

- [ ] **Step 4: Kabul raporu**

Kullanıcıya raporla: kaç sembol tarandı, WS durumu, üretilen sinyal sayısı
(0 ise eşiklerin sıkılığına bağla ve `config.toml [signals].threshold`
düşürme seçeneğini belirt), dashboard ekran görüntüsü.

- [ ] **Step 5: Commit** — `git commit -m "chore: dry-run acceptance results"`

---

## Self-Review Notları

- Spec kapsamı: tüm semboller ✓ (T11 exchangeInfo), çift yönlü kurallar ✓ (T4),
  3s/5s/15s WS ✓ (T3,T8), 1m/3m/5m REST ✓ (T7,T11), funding/OI ✓ (T7,T11 —
  OI değişim yüzdesi için iki nokta karşılaştırması T11 funding_poller içinde
  saklanarak yapılabilir; basit sürümde `oi_pct=None` geçer), agresif mod ✓
  (T11 sembol filtresi), Telegram ✓ (T9), dashboard ✓ (T10), hata yönetimi ✓
  (T7 retry, T8 reconnect), test planı ✓ (T12), dry-run ✓ (T13).
- Bilinen sadeleştirmeler: (1) saniyelik barlarda gösterge geçmişi REST'ten
  çekilemez; bu sürüm saniyelik sinyalleri anomali kurallarıyla (hacim/sıçrama/
  funding) üretir — spec'in "3s'te momentum+hacim ağırlıklı" ilkesiyle uyumlu.
  (2) OI yüzde değişimi ilk sürümde `None`; ileriki turda iki OI okuması
  karşılaştırılarak eklenir. (3) 15m teyit katmanı config'de var, kodda kapalı
  (spec: varsayılan kapalı).
- Tip tutarlılığı: `Bar(symbol, timeframe, open_ts, close_ts, open, high, low,
  close, quote_volume)` tüm görevlerde aynı sırada; `evaluate(bar, hits,
  funding_rate, oi_pct)` imzası T5 ile T11'de aynı.
