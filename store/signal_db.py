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

    def latest_scan_before(self, today: str) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(scan_date) FROM signals WHERE scan_date < ?",
            (today,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def signals_on(self, scan_date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT code, name, scan_date, score, stage, change_rate, vol_ratio "
            "FROM signals WHERE scan_date=? ORDER BY score DESC",
            (scan_date,),
        ).fetchall()
        return [
            {
                "code": r[0],
                "name": r[1] or r[0],
                "scan_date": r[2],
                "score": r[3],
                "stage": r[4],
                "change": r[5],
                "vol_ratio": r[6],
            }
            for r in rows
        ]

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
