"""SQLite 数据层：缓存（kv）+ 历史净值 + 持仓。

所有函数按需开短连接（WAL 模式），单文件 data/fundtool.db，备份即复制该文件。
"""
import json
import os
import sqlite3
import time
from typing import Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_cache (
    key        TEXT PRIMARY KEY,
    fetched_at INTEGER NOT NULL,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fund_nav_hist (
    code    TEXT NOT NULL,
    date    TEXT NOT NULL,
    nav     REAL NOT NULL,
    cum_nav REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_nav_hist_code_date ON fund_nav_hist (code, date);
CREATE TABLE IF NOT EXISTS holdings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_code  TEXT NOT NULL UNIQUE,
    fund_name  TEXT NOT NULL DEFAULT '',
    shares     REAL NOT NULL,
    cost_nav   REAL NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    path = config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # 每次连接幂等建表：服务长期运行期间若 DB 文件被外部删除/替换，
    # 下次连接自动恢复 schema（CREATE ... IF NOT EXISTS，开销可忽略）
    conn.executescript(_SCHEMA)
    return conn


def init_db() -> None:
    with _connect() as conn:
        pass


# ---------------------------------------------------------------- kv 缓存

def kv_get(key: str) -> Optional[tuple]:
    """返回 (fetched_at, data)；不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT fetched_at, data FROM kv_cache WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    return row["fetched_at"], row["data"]


def kv_set(key: str, data) -> None:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kv_cache (key, fetched_at, data) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET fetched_at=excluded.fetched_at, "
            "data=excluded.data",
            (key, int(time.time()), payload),
        )


def kv_get_fresh(key: str, ttl: int) -> Optional[object]:
    """TTL 内命中返回反序列化数据，否则 None。"""
    got = kv_get(key)
    if got is None:
        return None
    fetched_at, data = got
    if time.time() - fetched_at > ttl:
        return None
    return json.loads(data)


# ---------------------------------------------------------------- 历史净值

def nav_upsert(code: str, rows) -> int:
    """rows: [(date, nav, cum_nav), ...]；返回新增/更新条数。"""
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO fund_nav_hist (code, date, nav, cum_nav) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(code, date) DO UPDATE SET nav=excluded.nav, "
            "cum_nav=excluded.cum_nav",
            [(code, d, nav, cum) for d, nav, cum in rows],
        )
    return len(rows)


def nav_history(code: str, start_date: Optional[str] = None) -> list:
    """按日期升序返回 [{'date','nav','cum_nav'}, ...]。"""
    sql = "SELECT date, nav, cum_nav FROM fund_nav_hist WHERE code = ?"
    params = [code]
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    sql += " ORDER BY date ASC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def nav_max_date(code: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM fund_nav_hist WHERE code = ?", (code,)
        ).fetchone()
    return row["d"] if row else None


# ---------------------------------------------------------------- 持仓

def add_holding(fund_code: str, fund_name: str, shares: float,
                cost_nav: float, note: str = "") -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO holdings (fund_code, fund_name, shares, cost_nav, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))",
            (fund_code, fund_name, shares, cost_nav, note),
        )
        return int(cur.lastrowid)


def update_holding(holding_id: int, fund_code: Optional[str] = None,
                   fund_name: Optional[str] = None, shares: Optional[float] = None,
                   cost_nav: Optional[float] = None, note: Optional[str] = None) -> bool:
    fields, params = [], []
    for col, val in (("fund_code", fund_code), ("fund_name", fund_name),
                     ("shares", shares), ("cost_nav", cost_nav), ("note", note)):
        if val is not None:
            fields.append(f"{col} = ?")
            params.append(val)
    if not fields:
        return True
    params.append(holding_id)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE holdings SET {', '.join(fields)} WHERE id = ?", params
        )
        return cur.rowcount > 0


def delete_holding(holding_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
        return cur.rowcount > 0


def get_holding(holding_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM holdings WHERE id = ?", (holding_id,)
        ).fetchone()
    return dict(row) if row else None


def get_holding_by_code(fund_code: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM holdings WHERE fund_code = ?", (fund_code,)
        ).fetchone()
    return dict(row) if row else None


def list_holdings() -> list:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM holdings ORDER BY id ASC").fetchall()
    return [dict(r) for r in rows]
