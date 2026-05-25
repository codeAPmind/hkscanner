import sqlite3
from io import StringIO
from typing import Optional
import pandas as pd
from datetime import datetime, timedelta


class KlineCache:
    """每只股票每天只拉一次，节省历史K线月度 Quota"""

    def __init__(self, db_path="store/kline_cache.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                code TEXT, date TEXT, data_json TEXT,
                PRIMARY KEY (code, date)
            )
        """)
        self.conn.commit()

    def get(self, code: str) -> Optional[pd.DataFrame]:
        today = datetime.today().strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT data_json FROM cache WHERE code=? AND date=?",
            (code, today)
        ).fetchone()
        return pd.read_json(StringIO(row[0])) if row else None

    def set(self, code: str, df: pd.DataFrame):
        today = datetime.today().strftime("%Y-%m-%d")
        self.conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?)",
            (code, today, df.to_json())
        )
        self.conn.commit()

    def cleanup(self, keep_days=7):
        cutoff = (datetime.today() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        self.conn.execute("DELETE FROM cache WHERE date < ?", (cutoff,))
        self.conn.commit()
