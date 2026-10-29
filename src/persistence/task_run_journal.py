"""Durable task receipts; short-lived SQLite transactions and bounded retention."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import RLock
import time
from typing import Any


class TaskRunJournal:
    def __init__(
        self, path: Path, *, max_records: int = 10000, retention_seconds: float = 604800
    ) -> None:
        self._path = path
        self._max_records = max_records
        self._retention = retention_seconds
        self._ready = False
        self._lock = RLock()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=3)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        if self._ready:
            return
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (principal TEXT, request_id TEXT, "
                "task TEXT, run_id TEXT UNIQUE, status TEXT, created REAL, updated REAL, "
                "result TEXT, PRIMARY KEY(principal, request_id))"
            )
            db.execute(
                "UPDATE runs SET status='unknown' WHERE status IN "
                "('accepted','running','paused','cancelling')"
            )
        self._ready = True

    def find_request(self, principal: str, request_id: str) -> dict[str, Any] | None:
        return self._find("principal=? AND request_id=?", (principal, request_id))

    def find_run(self, principal: str, run_id: str) -> dict[str, Any] | None:
        return self._find("principal=? AND run_id=?", (principal, run_id))

    def _find(self, condition: str, parameters: tuple[str, str]) -> dict[str, Any] | None:
        with self._lock:
            self._initialize()
            with self._connect() as db:
                row = db.execute("SELECT * FROM runs WHERE " + condition, parameters).fetchone()
            if row is None:
                return None
            return {
                "run_id": row["run_id"],
                "task": row["task"],
                "status": row["status"],
                "execution_request_id": row["request_id"],
                "created_at": row["created"],
                "updated_at": row["updated"],
                "result": json.loads(row["result"]),
            }

    def reserve(self, principal: str, request_id: str, task: str, run_id: str) -> None:
        with self._lock:
            self._initialize()
            now = time.time()
            with self._connect() as db:
                db.execute(
                    "DELETE FROM runs WHERE updated<? AND status NOT IN "
                    "('accepted','running','paused','cancelling')",
                    (now - self._retention,),
                )
                if db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= self._max_records:
                    raise OverflowError("运行记录已满，请归档过期记录后重试")
                db.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                    (principal, request_id, task, run_id, "accepted", now, now, "{}"),
                )

    def update(
        self,
        principal: str,
        request_id: str,
        status: str,
        result: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> None:
        with self._lock:
            self._initialize()
            with self._connect() as db:
                db.execute(
                    "UPDATE runs SET run_id=COALESCE(?,run_id), status=?, updated=?, result=? "
                    "WHERE principal=? AND request_id=?",
                    (
                        run_id,
                        status,
                        time.time(),
                        json.dumps(result, ensure_ascii=False),
                        principal,
                        request_id,
                    ),
                )
