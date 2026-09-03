import asyncio
import logging

import aiohttp
import pandas as pd

log = logging.getLogger(__name__)
BASE = "https://fapi.binance.com"


class BinanceBanError(RuntimeError):
    """418: Binance IP banı — beklemek gerekir."""


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
        self.used_weight = 0

    async def _get(self, url: str, params: dict | None = None):
        return await self.session.get(url, params=params)

    async def _json(self, path: str, params: dict, retries: int = 3):
        for i in range(retries):
            r = await self._get(BASE + path, params)
            try:
                w = r.headers.get("x-mbx-used-weight-1m")
                if w:
                    self.used_weight = int(w)
                if self.used_weight > 2000:
                    await asyncio.sleep(10)
                if r.status == 429:
                    await asyncio.sleep(2 ** i)
                    continue
                if r.status == 418:
                    raise BinanceBanError(
                        "Binance IP banı (418) — tüm botları durdurup bekleyin")
                r.raise_for_status()
                return await r.json()
            finally:
                close = getattr(r, "close", None)
                if close:
                    close()
        raise RuntimeError(f"{path} için denemeler tükendi")

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        try:
            raw = await self._json("/fapi/v1/klines",
                                   {"symbol": symbol, "interval": interval, "limit": limit})
        except RuntimeError as e:
            if "400" in str(e):
                log.warning("%s %s: sembol muhtemelen delisted/bozuk, atlanıyor", symbol, interval)
                return pd.DataFrame()
            raise
        return parse_klines(raw[:-1], symbol, interval)  # son mum açık -> atla

    async def premium_index(self, symbol: str) -> float | None:
        data = await self._json("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data["lastFundingRate"]) if data else None

    async def open_interest(self, symbol: str) -> float | None:
        data = await self._json("/fapi/v1/openInterest", {"symbol": symbol})
        return float(data["openInterest"]) if data else None

    async def last_price(self, symbol: str) -> float | None:
        data = await self._json("/fapi/v1/ticker/price", {"symbol": symbol})
        return float(data["price"]) if data else None

    async def all_prices(self) -> dict[str, float]:
        data = await self._json("/fapi/v1/ticker/price", {})
        return {d["symbol"]: float(d["price"]) for d in data}

    async def all_funding(self) -> dict[str, float]:
        data = await self._json("/fapi/v1/premiumIndex", {})
        return {d["symbol"]: float(d["lastFundingRate"]) for d in data}

    async def exchange_info(self) -> list[str]:
        data = await self._json("/fapi/v1/exchangeInfo", {})
        return [s["symbol"] for s in data["symbols"]
                if s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL"
                and s["status"] == "TRADING"]
