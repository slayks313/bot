import sqlite3
import time
from typing import List, Optional
from bot_config import DB_PATH


class Memory:
    """Простой sqlite-репозиторий для хранения оскорблений и состояния бота."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_tables()

    def _ensure_tables(self):
        c = self._conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS insults (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user TEXT,
                text TEXT,
                ts INTEGER
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        self._conn.commit()

    # Insults
    def add_insult(self, user: str, text: str):
        ts = int(time.time())
        c = self._conn.cursor()
        c.execute('INSERT INTO insults (user, text, ts) VALUES (?, ?, ?)', (user, text, ts))
        self._conn.commit()

    def get_insults(self, user: Optional[str] = None) -> List[sqlite3.Row]:
        c = self._conn.cursor()
        if user:
            c.execute('SELECT * FROM insults WHERE user = ? ORDER BY ts DESC', (user,))
        else:
            c.execute('SELECT * FROM insults ORDER BY ts DESC')
        return c.fetchall()

    def clear_insults(self, user: Optional[str] = None):
        c = self._conn.cursor()
        if user:
            c.execute('DELETE FROM insults WHERE user = ?', (user,))
        else:
            c.execute('DELETE FROM insults')
        self._conn.commit()

    # State helpers (simple key/value)
    def set_state(self, key: str, value: str):
        c = self._conn.cursor()
        c.execute('INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)', (key, value))
        self._conn.commit()

    def get_state(self, key: str) -> Optional[str]:
        c = self._conn.cursor()
        c.execute('SELECT value FROM state WHERE key = ?', (key,))
        row = c.fetchone()
        return row['value'] if row else None

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
