import json
import os
import sqlite3
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Storage:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        key_path = directory / "secret.key"
        try:
            with key_path.open("xb") as key_file:
                key_file.write(Fernet.generate_key())
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
        except FileExistsError:
            pass
        self.cipher = Fernet(key_path.read_bytes())
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(
            directory / "muse.sqlite3", check_same_thread=False
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, public TEXT NOT NULL, credentials BLOB)"
        )
        self.connection.commit()

    def save(self, public, credentials=None):
        sealed = (
            self.cipher.encrypt(json.dumps(credentials, ensure_ascii=False).encode())
            if credentials
            else None
        )
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET public=excluded.public, credentials=COALESCE(excluded.credentials, tasks.credentials)",
                (public["id"], json.dumps(public, ensure_ascii=False), sealed),
            )

    def history(self):
        with self.lock:
            rows = self.connection.execute(
                "SELECT public FROM tasks ORDER BY rowid DESC"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def credentials(self, task_id):
        with self.lock:
            row = self.connection.execute(
                "SELECT credentials FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        if not row or not row[0]:
            return None
        try:
            return json.loads(self.cipher.decrypt(row[0]))
        except InvalidToken:
            raise RuntimeError("本地密钥与数据库不匹配，无法解密凭据") from None

    def close(self):
        with self.lock:
            self.connection.close()
