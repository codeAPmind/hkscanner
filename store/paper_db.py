"""hkscanner 模拟持仓账本。券商成交以 YiDong 共享 DB 对账，这里记策略口径。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


def weekdays_inclusive(start: date, end: date) -> int:
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


class PaperDB:
    def __init__(self, db_path: str | Path = "store/paper_positions.db"):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT NOT NULL,
                name        TEXT,
                scan_date   TEXT,
                entry_date  TEXT,
                qty         INTEGER,
                entry_px    REAL,
                status      TEXT,
                exit_date   TEXT,
                exit_px     REAL,
                pnl         REAL,
                buy_order   TEXT,
                sell_order  TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_paper_code_status
                ON positions(code, status);
        """)
        self.conn.commit()

    def open_positions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status='OPEN' ORDER BY entry_date, code"
        ).fetchall()
        return [dict(r) for r in rows]

    def is_open(self, code: str) -> bool:
        n = self.conn.execute(
            "SELECT COUNT(*) FROM positions WHERE code=? AND status='OPEN'",
            (code,),
        ).fetchone()[0]
        return n > 0

    def open_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status='OPEN'"
        ).fetchone()[0]

    def due_exits(self, today: date, hold_days: int) -> list[dict]:
        out = []
        for pos in self.open_positions():
            try:
                entry = date.fromisoformat(pos["entry_date"])
            except (TypeError, ValueError):
                continue
            held = weekdays_inclusive(entry, today)
            if held >= hold_days:
                pos = dict(pos)
                pos["held_days"] = held
                out.append(pos)
        return out

    def record_open(
        self,
        code: str,
        name: str,
        scan_date: str,
        entry_date: str,
        qty: int,
        entry_px: float,
        buy_order: str,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO positions(code,name,scan_date,entry_date,qty,entry_px,"
            "status,buy_order) VALUES(?,?,?,?,?,?, 'OPEN', ?)",
            (code, name, scan_date, entry_date, qty, entry_px, buy_order),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_close(
        self,
        pos_id: int,
        exit_date: str,
        exit_px: float,
        qty: int,
        sell_order: str,
    ) -> None:
        row = self.conn.execute(
            "SELECT entry_px FROM positions WHERE id=?", (pos_id,)
        ).fetchone()
        entry_px = float(row["entry_px"] if row else 0) or 0
        pnl = (exit_px - entry_px) * qty
        self.conn.execute(
            "UPDATE positions SET status='CLOSED', exit_date=?, exit_px=?,"
            " pnl=?, qty=?, sell_order=? WHERE id=?",
            (exit_date, exit_px, pnl, qty, sell_order, pos_id),
        )
        self.conn.commit()
