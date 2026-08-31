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
            stop REAL, hits_json TEXT, target REAL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_price REAL, closed_ts INTEGER, result_r REAL);
        CREATE TABLE IF NOT EXISTS tg_queue (
            id INTEGER PRIMARY KEY, text TEXT, sent INTEGER DEFAULT 0);
        """)
        self._migrate_signals()

    def _migrate_signals(self):
        for col, ddl in (("target", "target REAL"),
                         ("status", "status TEXT NOT NULL DEFAULT 'OPEN'"),
                         ("exit_price", "exit_price REAL"),
                         ("closed_ts", "closed_ts INTEGER"),
                         ("result_r", "result_r REAL")):
            try:
                self.conn.execute(f"ALTER TABLE signals ADD COLUMN {ddl}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

    def save_signal(self, s: Signal):
        hits = [{"rule": h.rule, "detail": h.detail, "score": h.score} for h in s.hits]
        self.conn.execute(
            "INSERT INTO signals (ts, symbol, timeframe, direction, strong, score, price,"
            " stop, target, hits_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (s.ts, s.symbol, s.timeframe, s.direction, int(s.strong), s.score,
             s.price, s.stop, s.target, json.dumps(hits, ensure_ascii=False)))
        self.conn.commit()

    def open_signals(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE status = 'OPEN' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def close_signal(self, sig_id: int, status: str, exit_price: float,
                     closed_ts: int, result_r: float | None):
        self.conn.execute(
            "UPDATE signals SET status = ?, exit_price = ?, closed_ts = ?, result_r = ?"
            " WHERE id = ?",
            (status, exit_price, closed_ts, result_r, sig_id))
        self.conn.commit()

    def signal_stats(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT direction, status, COUNT(*) AS n, AVG(result_r) AS avg_r"
            " FROM signals WHERE status != 'OPEN' GROUP BY direction, status"
            " ORDER BY direction, status").fetchall()
        return [dict(r) for r in rows]

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
