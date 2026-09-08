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
| Geniş stop ailesi (stop 4.5-6×ATR) | OOS -0.047%, fold'lar negatif | 2026-09-07 |
| Drift hasadı (stop 10×, hedef yok) | Tüm semboller kırmızı, güçlü negatif | 2026-09-07 |
| stop3/tgt6 geometrisi (LIVE) | 158 canlı işlemde target-oranı %5.9, stop kütlesi -21.2 | 2026-09-07 |
| Funding-carry (naif sıralama, N=5/10/20) | -%0.85..-%2.3/gün — carry, fiyat dominant anti-momentum'a yenik | 2026-09-07 |
| Funding-carry (momentum-hizalı) | Yine negatif; tek pozitif hücre 2 örneklem (gürültü) | 2026-09-07 |
| 15m timeframe | -%0.047 (3m ile aynı; frekans indirimi edge kurtarmadı) | 2026-09-07 |
| 1h timeframe | Sinyalsiz — kural seti 1h ölçeğinde ateşlenmiyor | 2026-09-07 |

### Arama kapanışı (2026-09-07)

Mevcut kural ailesi içinde sistematik arama tükenmiştir: 1m/3m/15m/1h
zaman dilimleri, 8 geometri kombinasyonu, 6 çıkış varyantı, 2 kural
ailesi ilavesi, funding-carry 2 varyant — hepsi OOS (maliyet dahil)
[-%2.3, +%0.08] bandında. Kanıt (Zhu & Cai 2026 ile uyumlu): bu sinyal
sınıfı perakende maliyet yapısında yüksek frekansta robust net edge
taşımıyor. Pozitif beklenti için ya yeni sinyal ailesi (ayrı araştırma
projesi) ya da yapısal maliyet avantajı gerekir. Paper bot, forward
veriyi biriktirmeye devam eder — mevcut konfigürasyon en az kötü
(kanıtlanmış) noktadır.

## Forward test sonuçları (gerçek $50 döngüsü, 3-7 Eylül, 4 gün)

- 158 dolu işlem + 153 MISSED; net **-$3.37** (sermaye $50 → $46.50)
- EXPIRED +%0.106 ort (107 işlem) — **entry drift pozitif ve gerçek**
- STOP -%0.442 ort (48 işlem) — geometri kanaması
- Fee toplamı $6.45 = sermayenin %12.9'u — **yüksek frekansta fee yapısal engel**
- Target-oranı %5.9 (başabaş %33.3) — 6×ATR hedef 30 dk'da vurulmuyor
- Canlı evren mikro-cap ağırlıklıydı → slipaj modeli gerçek spread'in altında
- Arşiv: paperbot/signals_archive_50USD.db

## Yeni döngü (7 Eylül): $25 sermaye

- start_equity 25, stop 2×ATR / hedef 2×ATR (kanıtlı en az kötü geometri)
- min_quote_volume_usd 50k → 2M: mikro-caplar (spread riski) evren dışı
- Altyapı: tek-instance mutex (8019), süreç supervisor'u, PowerShell watchdog

## Sonraki araştırma yönleri (sıradaki döngü adayı)

1. **Funding-carry / term structure**: pozitif funding ödeyen-kazanan tarzı
   market-nötr pozisyonlar; /fapi/v1/fundingRate geçmişi ile backtest
2. **Düşük frekans (15m-1h)**: fee/edge oranını 5-10× iyileştirir
3. **OI term structure**: openInterestHist ile pozisyonlanma akışı sinyali

## Kaynak taraması (2026-09-07)

**Zhu & Cai (2026), "AI in Equity and Crypto Markets", arXiv:2609.04917**
(32 sayfa literatür taraması, kesim 31 Ağustos 2026) — projemizin
ampirik bulgularıyla bağımsız örtüşme:

- **C2**: Maliyet-farkındasız optimizasyon net cephe üretmez (Jensen et al.
  2026; Novy-Marx & Velikov 2016 turnover-kost erosyonu) ↔ bizim fee
  sürüklenmesi bulgusu (%12.9/4 gün)
- **C4**: Kamu kanıtı kalıcı, rejimler-arası, maliyet-ayarlı net alpha'yı
  kurmaya yetersiz — "yetersiz" = "imkansız" değil
- **C5**: Temporal contamination birinci sınıf tehdit; look-ahead düzeltmesi
  raporlanan ML alpha'sını silebilir ↔ bizim walk-forward disiplini
- **C8/C9**: Crypto tek test yatağı değil; perp/spot/on-chain ayrıştırılmalı;
  mikro-cap'ta wash-trading/uygulama riski ↔ 2M hacim filtresi kararı
- **C11**: Kanıtlanmış reçete değil araştırma programı: veri-hedef-portföy-
  uygulama-uyum-yönetişim birleşik programı ↔ CALIBRATION.md çerçevesi

**Stratejik sonuç**: Mevcut kural ailesi yüksek frekansta maliyet sonrası
robust edge taşımıyor (bu taramayla uyumlu). Bir sonraki döngü: (a) funding
term-structure carry (düşük frekans, market-nötr), (b) 15m-1h momentum —
her ikisi de fee/edge oranını yapısal iyileştirir. Kâr garantisi yoktur;
adil yoklama süreci işletilir.

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
