from src.db import Database
from src.models import RuleHit, Signal


def test_save_and_query_signals(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    s = Signal("BTCUSDT", "1m", "LONG", False, 6.0, 100.0, None, None, 1000,
               [RuleHit("ema_cross", "LONG: x", 3.0)])
    db.save_signal(s)
    rows = db.recent_signals(limit=10)
    assert len(rows) == 1 and rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["hits_json"].startswith("[")


def test_telegram_queue_roundtrip(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.enqueue_message("merhaba")
    msg = db.next_pending_message()
    assert msg is not None and msg["text"] == "merhaba"
    db.mark_message_sent(msg["id"])
    assert db.next_pending_message() is None
