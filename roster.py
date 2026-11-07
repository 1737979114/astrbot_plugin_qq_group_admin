# -*- coding: utf-8 -*-
"""群成员名册：把「见过的人」攒下来。

QQ 官方接口没有「获取群成员列表」权限（11253，内邀），后台想给人禁言时
连个可点的人都没有。这里换个思路：**每收到一条群消息就把发言人记一笔**，
攒成一份本地名册，界面上就能按昵称找人、直接点禁言/解禁/踢人。

局限也写清楚：只有发过言的人才会出现，从没说过话的拿不到。
"""

from __future__ import annotations

import sqlite3
import time


class RosterStore:
    """按群记录成员的 openid、昵称与最近出现时间。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS roster (
                scope      TEXT NOT NULL,
                openid     TEXT NOT NULL,
                name       TEXT,
                first_seen REAL NOT NULL DEFAULT 0,
                last_seen  REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (scope, openid)
            )
            """
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_roster_scope_seen "
            "ON roster(scope, last_seen DESC)"
        )
        self._db.commit()

    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ 写入
    def touch(
        self, scope: str, openid: str, name: str = "", ts: float | None = None
    ) -> None:
        """记一次「这个人出现了」；昵称以最新一次为准。"""
        if not scope or not openid:
            return
        moment = ts or time.time()
        self._db.execute(
            """
            INSERT INTO roster (scope, openid, name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, openid) DO UPDATE SET
                name      = COALESCE(NULLIF(excluded.name, ''), roster.name),
                last_seen = excluded.last_seen
            """,
            (scope, openid, (name or "").strip(), moment, moment),
        )
        self._db.commit()

    def rename(self, scope: str, openid: str, name: str) -> None:
        self._db.execute(
            "UPDATE roster SET name = ? WHERE scope = ? AND openid = ?",
            (name, scope, openid),
        )
        self._db.commit()

    def bulk_touch(self, rows: list[tuple[str, str, str, float, float]]) -> int:
        """批量写入 ``(scope, openid, name, first_seen, last_seen)``，用于从消息历史补数据。"""
        if not rows:
            return 0
        before = self.total()
        self._db.executemany(
            """
            INSERT INTO roster (scope, openid, name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, openid) DO UPDATE SET
                name       = COALESCE(NULLIF(excluded.name, ''), roster.name),
                first_seen = MIN(roster.first_seen, excluded.first_seen),
                last_seen  = MAX(roster.last_seen, excluded.last_seen)
            """,
            rows,
        )
        self._db.commit()
        return self.total() - before

    def total(self) -> int:
        row = self._db.execute("SELECT COUNT(1) FROM roster").fetchone()
        return int(row[0] or 0)

    # ------------------------------------------------------------------ 读取
    def count(self, scope: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(1) FROM roster WHERE scope = ?", (scope,)
        ).fetchone()
        return int(row[0] or 0)

    def scopes(self) -> list[str]:
        """名册里出现过的所有会话（群 openid）。"""
        rows = self._db.execute("SELECT DISTINCT scope FROM roster").fetchall()
        return [str(row[0]) for row in rows if row and row[0]]

    def page(
        self, scope: str, keyword: str = "", offset: int = 0, limit: int = 50
    ) -> tuple[list[dict], int]:
        """按最近出现时间倒序分页，可按昵称或 openid 模糊搜。"""
        where = "WHERE scope = ?"
        params: list[object] = [scope]
        if keyword:
            where += " AND (name LIKE ? OR openid LIKE ?)"
            like = f"%{keyword}%"
            params.extend([like, like])

        total = int(
            self._db.execute(
                f"SELECT COUNT(1) FROM roster {where}", params
            ).fetchone()[0]
            or 0
        )
        rows = self._db.execute(
            f"SELECT openid, name, first_seen, last_seen FROM roster {where} "
            "ORDER BY last_seen DESC LIMIT ? OFFSET ?",
            [*params, max(1, min(int(limit), 200)), max(0, int(offset))],
        ).fetchall()
        return [dict(row) for row in rows], total

    def all(self, scope: str, limit: int = 500) -> list[dict]:
        rows, _ = self.page(scope, "", 0, limit)
        return rows

    def get(self, scope: str, openid: str) -> dict | None:
        row = self._db.execute(
            "SELECT openid, name, first_seen, last_seen FROM roster "
            "WHERE scope = ? AND openid = ?",
            (scope, openid),
        ).fetchone()
        return dict(row) if row else None
