# Binance Futures Sinyal Ajanı — Tasarım Dokümanı

Tarih: 2026-08-31
Durum: Onaylandı (bölüm bölüm kullanıcı onayı alındı)

## 1. Amaç ve Kapsam

Tüm Binance USDT-M Futures sembollerini gerçek zamanlı tarayan, çift yönlü
(LONG ve SHORT) alım/satım sinyalleri üreten, sinyalleri Telegram ve yerel web
dashboard üzerinden ileten tek süreçli bir Python uygulaması.

- Otomatik işlem AÇILMAZ — yalnızca sinyal üretilir.
- API anahtarı gerekmez — yalnızca public endpoint'ler kullanılır.
- Çalışma ortamı: kullanıcının kendi Windows bilgisayarı.
- Sinyaller şeffaftır: her sinyal, tetikleyen kuralları ve değerlerini gösterir.

## 2. Mimari

Tek Python 3.12 asyncio uygulaması, tek süreç:

```
src/
  ws_feed.py      # aggTrade WebSocket stream + saniyelik bar birleştirme
  klines.py       # 1m/3m/5m kline + funding rate + open interest (REST)
  rules.py        # sinyal kuralları (her kural kendi fonksiyonu, çift yönlü)
  engine.py       # skorlama, cooldown, sinyal üretimi
  notify.py       # Telegram kuyruğu (SQLite destekli)
  web.py          # FastAPI dashboard (HTMX + Chart.js)
  db.py           # SQLite
config.toml       # tüm parametreler
tests/
```

Bağımlılıklar: aiohttp/httpx (REST), websockets veya aiohttp ws, pandas,
`ta` kütüphanesi (göstergeler), python-telegram-bot, FastAPI + uvicorn, SQLite
(stdlib).

## 3. Veri Katmanı

### 3.1 Saniyelik barlar (3s, 5s, 15s)
- Binance REST'te saniyelik bar yoktur; bu nedenle tüm sembollerin `aggTrade`
  WebSocket stream'ine bağlanılır (~300 sembol, bağlantı başına 200 stream
  sınırı nedeniyle 2-3 combined stream bağlantısı).
- Gelen her işlem bellekte toplanır; 3s/5s/15s barlar ajan tarafından
  birleştirilir (open/high/low/close/volume).
- Bar yalnızca kapandığında sinyale girer; açık bar ile sinyal üretilmez.
- WS kopukluğu sırasında oluşan bar boşlukları sinyale sokulmaz.

### 3.2 Dakikalık barlar (1m, 3m, 5m)
- Binance kline REST endpoint'inden çekilir (rate-limit'e saygılı, ~10 paralel
  istek).
- Yalnızca kapalı mumlar değerlendirilir.

### 3.3 Pozisyonlanma verisi
- Funding rate: `premiumIndex` / funding history endpoint'leri (periyodik,
  dakikada bir yeterli).
- Open interest: `openInterest` endpoint'i, sembol başına periyodik;
  OI değişimi yön bilgisiyle birlikte değerlendirilir.

### 3.4 Sembol keşfi
- `exchangeInfo` üzerinden tüm USDT-M perpetual semboller; saatte bir yenilenir;
  delist/bozuk semboller otomatik listeden düşer.

## 4. Zaman Dilimleri

| Bar | Kaynak | Varsayılan |
|---|---|---|
| 3s, 5s, 15s | WS aggTrade birleştirme | 5s + 15s açık |
| 1m, 3m, 5m | Binance kline | 1m açık |
| 15m teyit katmanı | Binance kline | kapalı (opsiyonel) |

- Tüm dilimler config.toml'den açılır/kapanır.
- Her dilimin kendi sinyal eşiği ve cooldown'u vardır (örn. 5s için 30 sn,
  1m için 2-3 dk).
- 3s barlarda EMA/ADX tipi kurallar gürültülü olduğundan bu dilimlerde motor
  ağırlıklı olarak momentum patlaması + hacim anomalisi kurallarına güvenir
  (otomatik ağırlıklandırma).

## 5. Sinyal Motoru (çift yönlü)

Açık kaynak araştırmasına dayalı (freqtrade, jesse örnek stratejileri, OI
tarayıcıları, funding-rate projeleri). Her kural LONG ve SHORT varyantı üretir.

### Katman 1 — Trend + momentum
- LONG: EMA9 > EMA21 kesişimi VEYA MACD sinyal kesişimi; RSI(14)'ün 30'dan
  yukarı dönüşü.
- SHORT: tam ayna (EMA9 < EMA21, RSI 70'ten aşağı dönüş).
- ADX(14) > 22 trend filtresi — trend yoksa bu katman sinyal vermez.

### Katman 2 — Anomali (yönü fiyat hareketi belirler)
- Hacim patlaması: bar hacmi ≥ 3x (20-bar ortalaması).
- Fiyat sıçraması: bar içi ≥ %2 hareket.
- OI değişimi: OI artışı fiyatla aynı yöndeyse teyit puanı; OI artarken fiyat
  yataysa "sıkışmış yay" uyarısı.

### Katman 3 — Kalabalık pozisyon (contrarian)
- Funding ≥ +0.05%/8s → aşırı kalabalık long → SHORT eğilimi.
- Funding aşırı negatif → squeeze riski → LONG eğilimi.
- Not: pozitif funding = long'lar short'lara öder (işaret yönü kritik).

### Skorlama
- Her kural ağırlıklı puan verir; toplam ≥ dilim eşiği → sinyal.
- İki katman birden tetiklenirse "GÜÇLÜ LONG/SHORT".
- Her sinyal hangi kuralın hangi değerle tetiklendiğini içerir.

### Kalite filtreleri
- Minimum hacim tabanı (düşük likit semboller elenir).
- Sembol + yön + dilim bazında cooldown.
- Yalnızca kapalı barlar.

## 6. AGRESİF Mod (küçük hesap ön-ayarları)

- Hedef: yüksek volatilite + likit çiftler (BTC, ETH, SOL, DOGE gibi ilk
  ~20-30 sembol); küçük altcoin'ler elenir.
- Saniyelik dilimler öncelikli; sinyal eşiği düşürülür, ADX filtresi kalır.
- Her sinyalde ATR bazlı dar stop önerisi (~%1-2) ve net giriş seviyesi.
- Ajan kaldıraç önermez, pozisyon açmaz; yalnızca fiyat/stop bilgisi verir.

## 7. Telegram

Mesaj formatı (örnek):

```
🟢 GÜÇLÜ LONG — SOLUSDT
Fiyat: 198.42 | Zaman dilimi: 15s
Tetikleyen kurallar:
• EMA9/21 yukarı kesişim (9>21)
• Hacim 4.2x (20-bar ortalaması)
• OI +2.1% son 5 dk (fiyatla aynı yön)
Skor: 7/9 | Funding: +0.003%
Stop önerisi: 196.10 (ATR bazlı) | 12:34:05
```

- python-telegram-bot ile gönderim; ~30 mesaj/dk limitinde kuyruk.
- Cooldown süresince aynı sembol+yön+dilim için tekrar yok.
- Gönderilemeyen mesaj SQLite'a yazılır, bağlantı gelince iletilir.
- Bot token + chat ID config.toml'den (kullanıcının hazır botu).

## 8. Web Dashboard

localhost:8000, FastAPI + HTMX + Chart.js (frontend build yok), koyu tema,
mobil uyumlu, giriş yok.

- Canlı sinyal akışı: son sinyaller tablosu, 3 sn'de bir yenilenir.
- Sembol detayı: mum grafiği + kuralların anlık değerleri.
- Durum paneli: taranan sembol sayısı, WS bağlantı durumu, son 1 saat sinyal
  sayısı.
- Geçmiş: SQLite'dan filtreli sinyal geçmişi (tarih/yön/sembol).

## 9. Hata Yönetimi

- WS kopması: üstel geri çekme ile otomatik yeniden bağlanma (1s → 2s → 4s...);
  kopukluk dashboard'da görünür; eksik veriyle sinyal üretilmez.
- REST hataları: 429'da otomatik yavaşlama, 3 deneme sonra sembol o tur
  atlanır, loglanır; uygulama çökmez.
- Bozuk/delist semboller listeden otomatik düşer (exchangeInfo saatte bir).
- Telegram hatası: SQLite kuyruğu + yeniden deneme.
- Loglar: `logs/agent.log`, günlük rotasyon.

## 10. Test Planı

- Birim testleri: bar birleştirme (tick → 3s/5s/15s doğruluğu), her sinyal
  kuralının LONG ve SHORT yönleri (sabit veri seti), skorlama ve cooldown.
- Bütünleşik test: kayıtlı WS veri fixture'ı ile uçtan uca çalıştırma,
  beklenen sinyallerin doğrulanması.
- Canlı prova (dry-run): gerçek veriye bağlanır, sinyaller yalnızca
  konsol/dashboard'a düşer, Telegram'a gönderilmez; kullanıcı onayı sonrası
  Telegram açılır.
- Kabul kriterleri: 1 saat kesintisiz ve hatasız tarama; dashboard'da sinyal
  görünürlüğü; dry-run'da en az bir çift yönlü sinyal üretimi.

## 11. Yapılandırma (config.toml)

- Sembol filtresi (tümü / agresif mod listesi)
- Aktif zaman dilimleri, dilim başına eşik ve cooldown
- Kural ağırlıkları ve parametreleri (EMA periyotları, RSI seviyeleri,
  ADX eşiği, hacim çarpanı, funding eşikleri)
- Telegram token/chat ID, dry-run bayrağı
- Dashboard portu

## 12. Açıkça Kapsam Dışı

- Otomatik işlem açma/kapama (yalnızca sinyal)
- Backtest motoru (ileride eklenebilir)
- Çoklu kullanıcı / kimlik doğrulama
- Saniyelik barlar için 1s (çıkarıldı)
