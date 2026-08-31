import asyncio

from src.db import Database
from src.models import RuleHit, Signal
from src.tracker import OutcomeTracker

CFG = {"tracking": {"horizon_minutes": 30, "poll_interval_s": 1}}
HITS = [RuleHit("ema_cross", "LONG: x", 3.0)]
NOW = 10_000_000


class FakeRest:
    def __init__(self, price):
        self.price = price

    async def last_price(self, symbol):
        return self.price


def open_db(tmp_path, sig: Signal):
    db = Database(str(tmp_path / "t.db"))
    db.save_signal(sig)
    return db


def long_signal(ts=NOW):
    return Signal("BTCUSDT", "1m", "LONG", False, 6.0, 100.0, 98.0, 106.0, ts, HITS)


def closed_row(db):
    return db.conn.execute("SELECT * FROM signals").fetchone()


def test_long_stopped(tmp_path):
    db = open_db(tmp_path, long_signal())
    t = OutcomeTracker(db, FakeRest(97.0), CFG)
    asyncio.run(t.check_once(now_ms=NOW + 1000))
    r = closed_row(db)
    assert r["status"] == "STOPPED" and r["exit_price"] == 98.0
    assert r["result_r"] == -1.0
    assert db.open_signals() == []


def test_long_target_hit(tmp_path):
    db = open_db(tmp_path, long_signal())
    t = OutcomeTracker(db, FakeRest(107.0), CFG)
    asyncio.run(t.check_once(now_ms=NOW + 1000))
    r = closed_row(db)
    assert r["status"] == "TARGET" and r["exit_price"] == 106.0
    assert abs(r["result_r"] - 3.0) < 1e-9


def test_expired_at_horizon(tmp_path):
    db = open_db(tmp_path, long_signal())
    t = OutcomeTracker(db, FakeRest(101.0), CFG)
    asyncio.run(t.check_once(now_ms=NOW + 30 * 60_000))
    r = closed_row(db)
    assert r["status"] == "EXPIRED" and r["exit_price"] == 101.0
    assert abs(r["result_r"] - 0.5) < 1e-9


def test_short_stopped(tmp_path):
    sig = Signal("ETHUSDT", "1m", "SHORT", False, 5.0, 100.0, 102.0, 94.0, NOW, HITS)
    db = open_db(tmp_path, sig)
    t = OutcomeTracker(db, FakeRest(103.0), CFG)
    asyncio.run(t.check_once(now_ms=NOW + 1000))
    r = closed_row(db)
    assert r["status"] == "STOPPED" and r["exit_price"] == 102.0
    assert r["result_r"] == -1.0


def test_no_stop_stays_open_until_horizon(tmp_path):
    db = open_db(tmp_path, Signal("BTCUSDT", "1m", "LONG", False, 6.0,
                                   100.0, None, None, NOW, HITS))
    t = OutcomeTracker(db, FakeRest(90.0), CFG)
    asyncio.run(t.check_once(now_ms=NOW + 1000))
    assert len(db.open_signals()) == 1
    asyncio.run(t.check_once(now_ms=NOW + 31 * 60_000))
    r = closed_row(db)
    assert r["status"] == "EXPIRED" and r["result_r"] is None
