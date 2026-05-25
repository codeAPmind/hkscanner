import sqlite3
from datetime import datetime, timedelta


class SignalDB:
    def __init__(self, db_path="store/signals.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT,
                name        TEXT,
                scan_date   TEXT,
                score       INTEGER,
                stage       TEXT,
                change_rate REAL,
                vol_ratio   REAL,
                ai_summary  TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_code_date ON signals(code, scan_date);
        """)
        self.conn.commit()

    def is_new_signal(self, code: str, lookback_days: int = 3) -> bool:
        cutoff = (datetime.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        count  = self.conn.execute(
            "SELECT COUNT(*) FROM signals WHERE code=? AND scan_date>=?",
            (code, cutoff)
        ).fetchone()[0]
        return count == 0

    def save(self, results: list, scan_date: str):
        rows = [
            (r['code'], r.get('name'), scan_date, r['score'],
             r['stage'], r['change'], r['vol_ratio'],
             r.get('ai_analysis', '')[:500])
            for r in results
        ]
        self.conn.executemany(
            "INSERT INTO signals(code,name,scan_date,score,stage,"
            "change_rate,vol_ratio,ai_summary) VALUES(?,?,?,?,?,?,?,?)",
            rows
        )
        self.conn.commit()
