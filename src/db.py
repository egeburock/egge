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
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT,
            entry_ts INTEGER, entry_price REAL, notional REAL,
            stop REAL, target REAL, deadline_ts INTEGER,
            entry_limit REAL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            exit_ts INTEGER, exit_price REAL,
            fees REAL DEFAULT 0, funding REAL DEFAULT 0,
            gross_pnl REAL, net_pnl REAL, equity_after REAL);
        CREATE TABLE IF NOT EXISTS paper_state (
            key TEXT PRIMARY KEY, value TEXT);
        """)
        self._migrate_signals()

    def _migrate_signals(self):
        for col, ddl in (("target", "target REAL"),
                         ("status", "status TEXT NOT NULL DEFAULT 'OPEN'"),
                         ("exit_price", "exit_price REAL"),
                         ("closed_ts", "closed_ts INTEGER"),
                         ("result_r", "result_r REAL"),
                         ("entry_limit", "entry_limit REAL"),
                         ("entry_deadline", "entry_deadline INTEGER")):
            try:
                self.conn.execute(f"ALTER TABLE signals ADD COLUMN {ddl}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

    def save_signal(self, s: Signal):
        hits = [{"rule": h.rule, "detail": h.detail, "score": h.score} for h in s.hits]
        status = "PENDING" if s.entry_limit is not None else "OPEN"
        self.conn.execute(
            "INSERT INTO signals (ts, symbol, timeframe, direction, strong, score, price,"
            " stop, target, hits_json, status, entry_limit, entry_deadline)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.ts, s.symbol, s.timeframe, s.direction, int(s.strong), s.score,
             s.price, s.stop, s.target, json.dumps(hits, ensure_ascii=False),
             status, s.entry_limit, s.entry_deadline))
        self.conn.commit()

    def open_signals(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE status = 'OPEN' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def pending_orders(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE status = 'PENDING' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def activate_order(self, sig_id: int, entry_price: float,
                       stop: float, target: float):
        self.conn.execute(
            "UPDATE signals SET status = 'OPEN', price = ?, stop = ?, target = ?"
            " WHERE id = ?", (entry_price, stop, target, sig_id))
        self.conn.commit()

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

    def save_paper_trade(self, symbol: str, direction: str, entry_ts: int,
                         entry_price: float, notional: float, stop: float,
                         target: float, deadline_ts: int, entry_limit: float):
        cur = self.conn.execute(
            "INSERT INTO paper_trades (symbol, direction, entry_ts, entry_price,"
            " notional, stop, target, deadline_ts, entry_limit)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (symbol, direction, entry_ts, entry_price, notional, stop, target,
             deadline_ts, entry_limit))
        self.conn.commit()
        return cur.lastrowid

    def paper_orders(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM paper_trades WHERE status = 'PENDING' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def paper_positions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def fill_paper_order(self, trade_id: int, entry_price: float):
        self.conn.execute(
            "UPDATE paper_trades SET status = 'OPEN', entry_price = ? WHERE id = ?",
            (entry_price, trade_id))
        self.conn.commit()

    def add_paper_funding(self, trade_id: int, amount: float):
        self.conn.execute(
            "UPDATE paper_trades SET funding = funding + ? WHERE id = ?",
            (amount, trade_id))
        self.conn.commit()

    def close_paper_trade(self, trade_id: int, status: str, exit_ts: int,
                          exit_price: float, fees: float, funding: float,
                          gross_pnl: float, net_pnl: float, equity_after: float):
        self.conn.execute(
            "UPDATE paper_trades SET status = ?, exit_ts = ?, exit_price = ?,"
            " fees = ?, funding = ?, gross_pnl = ?, net_pnl = ?, equity_after = ?"
            " WHERE id = ?",
            (status, exit_ts, exit_price, fees, funding, gross_pnl, net_pnl,
             equity_after, trade_id))
        self.conn.commit()

    def paper_history(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def paper_net_total(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) AS total FROM paper_trades"
            " WHERE status NOT IN ('PENDING', 'OPEN')").fetchone()
        return float(row["total"])

    def get_paper_state(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM paper_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_paper_state(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO paper_state (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        self.conn.commit()
