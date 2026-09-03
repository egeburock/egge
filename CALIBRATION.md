# Kalibrasyon ve Rollout Rehberi — Sinyal Ajanı

Bu belge, kural/parametre değişikliklerinin kontrollü biçimde test edilmesini,
devreye alınmasını ve gerektiğinde geri alınmasını tanımlar. (Çerçeve:
features/copilot/plans şablonundan bu projeye uyarlanmıştır.)

## KPI'lar

| Metrik | Tanım | Mevcut baseline (3m, OOS 30g) | Kapı |
|---|---|---|---|
| PnL/işlem | maliyet dahil ort. kâr | +%0.082 | > 0 |
| Win rate | TARGET + EXP_WIN oranı | %48.4 | >= mevcut - 3 puan |
| Target oranı | TARGET / (TARGET+STOP) | %44 (32/72) | > geometri başabaşı |
| Sinyal/hafta | tazelik göstergesi | ~21 | >= 10 |
| Fold tutarlılığı | pozitif test fold sayısı | 3/3 (91 sinyal) | >= 2/3 |
| Maks. sembol bağımlılığı | en iyi sembolün toplam pnl payı | %59 (ARB) | < %50 (hedef) |

## Kalibrasyon döngüsü

1. **Hipotez** — tek değişkenli, önceden gerekçeli (mikro-yapı, açık kaynak
   desen, vb.). "Grid'de daha iyi görünen" yalnız hipotez değildir.
2. **Backtest** — `scripts/walk_forward.py` (10 gün+ veri, maliyet modeli
   dahil, kural paritesi `optimize_rr` üzerinden). In-sample `backtest.py`
   tek başına karar aracı DEĞİLDİR.
3. **Karar kapıları** — yukarıdaki tablo; A/B değil, walk-forward fold
   tutarlılığı kullanılır (tek varlık, çok pencere).
4. **Config'e al** — tek commit, commit mesajında ölçümler.
5. **Forward doğrulama** — `dry_run=true` ile en az 30 sinyal veya 2 hafta.
   Tracker sonuçları `signals.db`'ye yazar; dashboard izler.
6. **Promote / Rollback** — aşağıdaki kriterlere göre.

## Rollback kriterleri (forward)

- 30 sinyal sonunda forward PnL/işlem < -%0.10, **veya**
- forward WR < %35, **veya**
- WS/REST hata oranı artışı, dashboard'da sürekli KOPUK

İhlal durumunda: config'i önceki commit'e döndür, hipotezi "reddedildi"
olarak bu belgeye işle.

## Reddedilen hipotezler (kayıt)

| Hipotez | Sonuç | Tarih |
|---|---|---|
| ATR% taban filtresi (min_atr_pct) | OOS'ta her seviyede kötüleştirdi | 2026-09-02 |
| Breakeven-stop (be_r) | Train'de bile seçilmedi | 2026-09-02 |
| Stale-exit (zaman kesici) | WR -7 puan, gelecek kazananları da kesti | 2026-09-02 |
| Pullback kuralları (RSI2, EMA21) | Sinyal 2x, kalite yarıya; OOS negatif | 2026-09-02 |
| Derin limit offset (0.75-1.0R) | Dolma oranı çöktü, PnL düştü | 2026-09-03 |
| 1m canlı timeframe | 30g veride ters seçim (target oranı %17.2) | 2026-09-03 |

## Kabul edilen değişiklikler

| Değişiklik | Etki | Tarih |
|---|---|---|
| Horizon paritesi (backtest 30dk = canlı) | PnL -0.033 -> +0.004 | 2026-09-03 |
| Limit giriş (0.5R) + maker maliyet modeli | WR +4.9 puan | 2026-09-03 |
| 3m'e geçiş (1m yerine) | PnL +0.004 -> +0.082 | 2026-09-03 |

## Bilinen riskler

- **Kısa veri**: 30 gün; ayı-piyasası önyargısı (sinyallerin %91'i SHORT).
  Boğa dönüşünde LONG tarafı testsiz.
- **Sembol yoğunlaşması**: kazanç ARB/OP/SUI'da (yüksek-beta altcoin);
  majörler negatif. Evren kararı forward veriyle yeniden değerlendirilecek.
- **Çoklu-test bias**: bu repo üzerinde ~15 deney koşuldu; +0.082'nin bir
  kısmı şans olabilir. Forward doğrulama zorunlu.

## İzleme

- Dashboard (`/`): sonuç rozetleri (EMİR/AÇIK/HEDEF/STOP/SÜRE/DOLMADI), R istatistikleri
- `logs/agent.log`: günlük rotasyon, hata izleme
- `signals.db`: forward kalibrasyonun ham verisi (result_r, status)
