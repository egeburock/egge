"""İşlem maliyet sabitleri (Binance USDT-M Futures varsayılan) — tek kaynak."""

TAKER_FEE = 0.0004   # 0.04%
MAKER_FEE = 0.0002   # 0.02%
SLIPPAGE_BPS = 1     # 1 bps = 0.01% per taker fill

# piyasa girişi (taker) + limit çıkış (maker) + 2 slipaj
MARKET_RT_COST_PCT = TAKER_FEE + MAKER_FEE + 2 * SLIPPAGE_BPS / 10000   # ~0.08%
# limit giriş (maker, slipajsız) + limit çıkış + stop slipajı
LIMIT_RT_COST_PCT = MAKER_FEE + MAKER_FEE + SLIPPAGE_BPS / 10000        # ~0.05%
