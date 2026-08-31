import json
import sqlite3

from src.models import Signal


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY, ts INTEGER, symbol TEXT, timeframe TEXT,
            direction TEXT, strong INTEGER, score REAL, price REAL,
            stop REAL, hits_json TEXT);
        CREATE TABLE IF NOT EXISTS tg_queue (
            id INTEGER PRIMARY KEY, text TEXT, sent INTEGER DEFAULT 0);
        """)

    def save_signal(self, s: Signal):
        hits = [{"rule": h.rule, "detail": h.detail, "score": h.score} for h in s.hits]
        self.conn.execute(
            "INSERT INTO signals (ts, symbol, timeframe, direction, strong, score, price, stop, hits_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (s.ts, s.symbol, s.timeframe, s.direction, int(s.strong), s.score,
             s.price, s.stop, json.dumps(hits, ensure_ascii=False)))
        self.conn.commit()

    def recent_signals(self, limit: int = 50, symbol: str | None = None) -> list[dict]:
        q = "SELECT * FROM signals"
        args: list = []
        if symbol:
            q += " WHERE symbol = ?"
            args.append(symbol)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args)]

    def enqueue_message(self, text: str):
        self.conn.execute("INSERT INTO tg_queue (text) VALUES (?)", (text,))
        self.conn.commit()

    def next_pending_message(self) -> dict | None:
        r = self.conn.execute(
            "SELECT id, text FROM tg_queue WHERE sent = 0 ORDER BY id LIMIT 1").fetchone()
        return dict(r) if r else None

    def mark_message_sent(self, msg_id: int):
        self.conn.execute("UPDATE tg_queue SET sent = 1 WHERE id = ?", (msg_id,))
        self.conn.commit()
