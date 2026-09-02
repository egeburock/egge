import pytest

from src.klines import BinanceRest, parse_klines

RAW = [[1700000000000, "100", "105", "99", "103", "10", 1700000059999,
        "1000.5", 50, "5", "500.2", "0"]]
ROW2 = [1700000060000, "103", "106", "102", "105", "8", 1700000119999,
        "840.0", 40, "4", "420.0", "0"]


def test_parse_klines():
    df = parse_klines(RAW, "BTCUSDT", "1m")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["close"] == 103.0 and row["quote_volume"] == 1000.5
    assert df.attrs["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_rate_limit_and_retry():
    calls = {"n": 0}

    async def fake_get(url, params=None):
        calls["n"] += 1

        class R:
            status = 429 if calls["n"] == 1 else 200

            def raise_for_status(self):
                pass

            async def json(self):
                return RAW + [ROW2]

        return R()

    br = BinanceRest(session=None)
    br._get = fake_get
    out = await br.klines("BTCUSDT", "1m", limit=2)
    assert len(out) == 1 and calls["n"] == 2


@pytest.mark.asyncio
async def test_all_prices_and_funding_batch():
    async def fake_json(path, params, retries=3):
        assert params == {}
        if path.endswith("/ticker/price"):
            return [{"symbol": "BTCUSDT", "price": "100.5"},
                    {"symbol": "ETHUSDT", "price": "200.25"}]
        return [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"},
                {"symbol": "ETHUSDT", "lastFundingRate": "-0.0002"}]

    br = BinanceRest(session=None)
    br._json = fake_json
    assert await br.all_prices() == {"BTCUSDT": 100.5, "ETHUSDT": 200.25}
    assert await br.all_funding() == {"BTCUSDT": 0.0001, "ETHUSDT": -0.0002}
