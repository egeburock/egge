# Dry-Run Kabul Raporu — 2026-08-31

**Süre:** ~10 dakika canlı çalışma (kuru mod, `dry_run=true`)

## Sonuçlar

| Kontrol | Sonuç | Beklenti |
|---|---|---|
| Taranan sembol | **525** (tüm USDT-M futures) | ~3xx+ ✓ |
| WebSocket durumu | `ws: true` (bağlı, kesintisiz) | true ✓ |
| `/api/status` | `{"symbols":525,"ws":true}` | ✓ |
| `/api/signals` | `[]` (geçerli JSON dizi) | ✓ |
| Dashboard (127.0.0.1:8000) | 200 OK, HTML sayfa servis edildi | ✓ |
| `Traceback` sayısı (logs/agent.log) | **0** | 0 ✓ |
| `WARNING/ERROR` sayısı | **0** | 0 ✓ |
| Test paketi | 30/30 geçti (pytest) | ✓ |

## Üretilen sinyal: 0

10 dakikalık pencerede sinyal üretilmedi. Nedeni eşiklerin sıkılığı:

- `threshold = 5.0` (en az 2 güçlü kuralın aynı kapalı barda çakışması gerekir)
- ADX filtresi (`adx_min = 22`) trend kurallarını (EMA/MACD kesişim) daraltıyor
- `min_quote_volume_usd = 50000` düşük hacimli sembolleri eledi
- Saniyelik barlar (5s/15s) için gösterge geçmişi REST'ten alınamadığından
  bu dilimler yalnızca anomali kurallarıyla (hacim sıçraması ≥3x, fiyat ≥%2)
  sinyal üretebilir — 10 dakikada bu tür bir olay yaşanmadı.

## Daha fazla sinyal için (kullanıcı kararı)

`config.toml` içinde:

- `[signals] threshold = 3.0` (daha gevşek, daha çok sinyal)
- `[signals] adx_min = 15` (trend kuralları daha sık geçer)
- `[signals] min_quote_volume_usd = 10000`

## Notlar

- Telegram gerçek gönderimi kuru modda kapalı; mesajlar `signals.db` kuyruğuna
  yazılmaya hazır. Canlı moda geçiş: `config.toml [agent] dry_run=false`.
- Boru hattının uçtan ucu doğrulaması `tests/test_integration.py` (30 test
  paketinin parçası) ile sentetik veri üzerinden yapıldı.
