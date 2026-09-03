import asyncio

from src.db import Database
from src.paper import PaperBroker
from src.paper_costs import MAKER_FEE, SLIPPAGE_BPS, TAKER_FEE

NOW = 10_000_000_000  # 8h sınırına göre hizalanmış olmayan bir an

CANDLE = [0, "99.0", "100.5", "98.5", "100.0", 0]  # [ts, open, high, low, close, vol]


class FakeRest:
    def __init__(self, candle=None):
        self.candle = candle if candle is not None else CANDLE

    async def all_prices(self):
        return {}

    async def _json(self, path, params, retries=3):
        return [self.candle]


def make_broker(tmp_path, equity=50.0):
    db = Database(str(tmp_path / "p.db"))
    db.set_paper_state("equity", str(equity))
    cfg = {"paper": {"start_equity": equity, "risk_pct": 0.02,
                     "per_trade_cap_x": 10.0, "portfolio_cap_x": 100.0,
                     "min_notional": 5.0, "max_positions": 5},
           "tracking": {"horizon_minutes": 30},
           "signals": {"entry_window_bars": 8}}
    return PaperBroker(db, FakeRest(), cfg)


def make_signal(price=100.0, stop=97.0, target=106.0, limit=99.0,
                direction="LONG", symbol="BTCUSDT", ts=NOW):
    from src.models import RuleHit, Signal
    return Signal(symbol, "3m", direction, False, 7.0, price, stop, target, ts,
                  [RuleHit("x", "y", 2.0)], entry_limit=limit,
                  entry_deadline=ts + 8 * 180_000)


def test_open_sizes_by_risk_and_fills(tmp_path):
    b = make_broker(tmp_path)
    sig = make_signal()  # stop %3 uzakta -> notional = 1/0.03 = $33.3
    tid = b.on_signal(sig)
    assert tid is not None
    orders = b.db.paper_orders()
    assert len(orders) == 1
    assert abs(orders[0]["notional"] - 1.0 / 0.03) < 0.01

    asyncio.run(b.check_fill(orders[0], 99.0, NOW + 1000))  # mum low=98.5 limiti değdi
    pos = b.db.paper_positions()[0]
    assert pos["status"] == "OPEN" and pos["entry_price"] == 99.0


def test_target_exit_updates_equity(tmp_path):
    b = make_broker(tmp_path)
    b.on_signal(make_signal())
    order = b.db.paper_orders()[0]
    asyncio.run(b.check_fill(order, 99.0, NOW + 1000))
    pos = b.db.paper_positions()[0]
    notional = pos["notional"]
    b.manage_position(pos, 106.0, NOW + 2000)
    closed = b.db.paper_history(1)[0]
    assert closed["status"] == "TARGET"
    gross = (106.0 - 99.0) / 99.0 * notional
    fees = notional * (MAKER_FEE + MAKER_FEE)
    assert abs(closed["net_pnl"] - (gross - fees)) < 1e-9
    assert abs(b.equity - (50.0 + closed["net_pnl"])) < 1e-9


def test_stop_exit_charges_taker_and_slippage(tmp_path):
    b = make_broker(tmp_path)
    b.on_signal(make_signal())
    order = b.db.paper_orders()[0]
    asyncio.run(b.check_fill(order, 99.0, NOW + 1000))
    pos = b.db.paper_positions()[0]
    b.manage_position(pos, 97.0, NOW + 2000)
    closed = b.db.paper_history(1)[0]
    assert closed["status"] == "STOP"
    gross = (97.0 - 99.0) / 99.0 * pos["notional"]
    fees = pos["notional"] * (MAKER_FEE + TAKER_FEE + SLIPPAGE_BPS / 10000)
    assert abs(closed["net_pnl"] - (gross - fees)) < 1e-9
    assert b.equity < 50.0


def test_unfilled_order_expires_missed(tmp_path):
    b = make_broker(tmp_path)
    b.on_signal(make_signal())
    order = b.db.paper_orders()[0]
    # mum low=98.5 <= 99 -> dolur; dolmaması için candle'ı yüksek tut
    b.rest.candle = [0, "100.0", "100.6", "99.8", "100.4", 0]
    asyncio.run(b.check_fill(order, 100.5, order["deadline_ts"] + 1))
    closed = b.db.paper_history(1)[0]
    assert closed["status"] == "MISSED"
    assert closed["net_pnl"] == 0.0 and b.equity == 50.0


def test_funding_applied_at_boundary(tmp_path):
    b = make_broker(tmp_path)
    b.on_signal(make_signal())
    order = b.db.paper_orders()[0]
    asyncio.run(b.check_fill(order, 99.0, NOW + 1000))
    pos = b.db.paper_positions()[0]
    b.funding["BTCUSDT"] = 0.0001  # pozitif rate: long öder
    boundary = (NOW // 28_800_000 + 1) * 28_800_000 + 1000  # bir sonraki 8s sınırı
    b.apply_funding(pos, NOW + 1000, boundary)
    updated = b.db.paper_positions()[0]
    expected = -0.0001 * pos["notional"]
    assert abs(updated["funding"] - expected) < 1e-9


def test_skip_if_symbol_already_open(tmp_path):
    b = make_broker(tmp_path)
    assert b.on_signal(make_signal()) is not None
    asyncio.run(b.check_fill(b.db.paper_orders()[0], 99.0, NOW + 1000))
    assert b.on_signal(make_signal()) is None


def test_per_trade_notional_capped(tmp_path):
    b = make_broker(tmp_path)
    sig = make_signal(price=100.0, stop=99.9, limit=99.95)  # stop %0.1 -> dev notional
    b.on_signal(sig)
    order = b.db.paper_orders()[0]
    assert order["notional"] <= 50.0 * 10.0 + 1e-6  # per_trade_cap_x=10 testte


def test_equity_floor_blocks_new_trades(tmp_path):
    b = make_broker(tmp_path, equity=4.0)
    assert b.on_signal(make_signal()) is None
