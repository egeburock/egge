# Binance Futures Sinyal Ajanı

Tüm Binance USDT-M Futures sembollerini gerçek zamanlı tarayan, çift yönlü
(LONG/SHORT) işlem sinyalleri üreten tek süreçli bir Python uygulaması.

- **Otomatik işlem açmaz** — yalnızca sinyal üretir.
- **API anahtarı gerekmez** — yalnızca public endpoint'ler.
- Sinyaller Telegram ve yerel web dashboard üzerinden iletilir.
- Her sinyal tetikleyen kuralları, skoru ve ATR bazlı stop/hedefi gösterir.

## Mimari

```
src/
  main.py         # orkestrasyon: WS akışı, poller'lar, dashboard
  ws_feed.py      # aggTrade WebSocket (combined stream, 200/parça)
  bars.py         # tick -> 5s/15s bar birleştirme + bellek-içi geçmiş
  klines.py       # REST: klines, funding, OI, fiyat (tekil + batch)
  rules.py        # sinyal kuralları (her kural çift yönlü)
  engine.py       # skorlama, eşik, cooldown, sinyal üretimi
  tracker.py      # sinyal sonuç takibi (STOP/TARGET/EXPIRED, R katsayısı)
  notify.py       # Telegram kuyruğu (SQLite destekli)
  web.py          # FastAPI dashboard
  db.py           # SQLite
config.toml       # tüm parametreler
scripts/
  backtest.py     # canlı kurallarla geçmiş veri testi
  optimize_rr.py  # stop/hedef/eşik grid araması
tests/
```

## Sinyal mantığı

Kurallar puanlıdır; yön bazında toplam skor eşik (varsayılan LONG=6, SHORT=4)
geçerse sinyal üretilir:

- **Trend/momentum:** EMA9/21, MACD, RSI dönüşü (ADX > 22 filtresi ile)
- **Anomali:** hacim patlaması (≥3x), balina hacmi (Z-Score ≥3), emilim,
  fiyat sıçraması (≥%2)
- **Pozisyon verisi:** funding kalabalığı/ekstremi, OI + fiyat teyidi,
  Supertrend, Order Block retesti
- **Teyit katmanı:** sinyaller 1h Supertrend yönüyle uyuşmak zorunda
  (`use_htf_filter`, `htf_timeframe` ayarlanabilir)
- Sembol+yön+dilim bazında cooldown; sadece kapanmış barlar.

## Kurulum ve çalıştırma

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install aiohttp pandas ta python-telegram-bot fastapi uvicorn
python -m src.main
```

- Dashboard: `http://127.0.0.1:8000`
- Loglar: `logs/agent.log` (günlük rotasyon, 7 gün)
- Veri: `signals.db` (SQLite)

`config.toml` içinde `dry_run = true` iken Telegram'a gönderim yapılmaz;
mesajlar kuyruğa yazılır. Canlıya geçiş: `dry_run = false` + bot token/chat_id.

## Testler

```bash
python -m pytest tests -q
```

## Kalibrasyon

Parametre/kural değişiklikleri için ölçüm kapıları, rollback kriterleri ve
reddedilen hipotezlerin kaydı: [CALIBRATION.md](CALIBRATION.md). Deney aracı:
`scripts/walk_forward.py` (out-of-sample, maliyet dahil).

## Scripts

```bash
python scripts/backtest.py      # geçmiş veride canlı kural setinin başarı oranı
python scripts/optimize_rr.py   # stop/hedef/eşik grid araması
python scripts/walk_forward.py  # out-of-sample validasyon (train/test fold'ları)
```

- Backtest komisyon + slippage içerir (~%0.08 round-trip; `optimize_rr.py`
  başındaki sabitlerden ayarlanır).
- Veri `.data_cache/` altında 6 saat cache'lenir; taze veri için klasörü silin.
- In-sample sonuçlara güvenmeyin: `walk_forward.py` out-of-sample koşusu
  olmadan hiçbir ayar değişikliğini kabul etmeyin.

## Paper-Trading Botu ($50 canlı test)

```bash
python -m src.paper
```

- Sinyal hattı canlı ajanla birebir aynı (3m, HTF teyidi, 0.5R limit giriş).
- `$50` sanal sermaye; gerçek emir GÖNDERİLMEZ (API anahtarı gerekmez).
- Muhasebe gerçekçi: maker/taker fee, slipaj, 8 saatlik funding ödemeleri,
  pozisyon boyutlama (riske %2 sermaye/işlem, işlem başına 1.5x,
  portföy 3x sermaye sınırı, en fazla 5 eşzamanlı pozisyon).
- Dashboard'da "Paper Hesap" paneli: sermaye, açık pozisyonlar, işlem geçmişi.
- Durum `signals.db`'de kalıcıdır (yeniden başlatmada sermaye korunur).

> Not: Bu araç kâr garantisi değil, ölçüm aracıdır. Stratejinin gerçek net
> performansı fee/funding/slippage dahil burada görülür.

## Uyarı

Bu araç yatırım tavsiyesi değildir. Sinyaller eğitim amaçlı kural setlerinin
çıktısıdır; kendi risk yönetiminizi yapın.
