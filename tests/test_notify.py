import asyncio

from src.db import Database
from src.models import RuleHit, Signal
from src.notify import Notifier, format_signal


def make_signal():
    return Signal("SOLUSDT", "15s", "LONG", True, 7.0, 198.42, 196.10, 201.50, 1000,
                  [RuleHit("ema_cross", "LONG: EMA9>EMA21 kesişim", 3.0),
                   RuleHit("volume_spike", "Hacim 4.2x (20-bar ort.)", 2.0),
                   RuleHit("oi_confirm", "LONG teyit: OI +2.1% fiyatla aynı yön", 1.0)])


def test_format_signal_contains_everything():
    text = format_signal(make_signal())
    assert "GÜÇLÜ LONG" in text and "SOLUSDT" in text
    assert "EMA9>EMA21" in text and "7.0" in text and "196.1" in text
    assert "Hedef: 201.5" in text


def test_format_signal_small_price_not_truncated():
    s = Signal("PEPEUSDT", "1m", "LONG", False, 6.0, 0.00001234,
               0.00001200, 0.00001350, 1000, [])
    text = format_signal(s)
    assert "1.2e-05" in text and "1.35e-05" in text


def test_dry_run_enqueues_instead_of_sending(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    n = Notifier(db, token="", chat_id="", dry_run=True)
    asyncio.run(n.send(make_signal()))
    assert db.next_pending_message() is not None
