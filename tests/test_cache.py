"""db 层单测：kv 缓存 TTL / 历史净值 upsert 幂等 / 查询语义。

只依赖 fundtool.db（已完成），不经过 HTTP，不需要后端 services/api。
"""
import json
import time

from fundtool import db


# ---------------------------------------------------------------- kv 缓存

def test_kv_set_get(env):
    # 注意：kv_get 返回 (fetched_at, 原始 JSON 字符串)；反序列化是 kv_get_fresh 的职责
    payload = [{"code": "161725", "name": "招商中证白酒"}]
    db.kv_set("rank:全部", payload)
    got = db.kv_get("rank:全部")
    assert got is not None
    fetched_at, raw = got
    assert isinstance(fetched_at, int)
    assert json.loads(raw) == payload
    # 新鲜读取返回反序列化对象
    assert db.kv_get_fresh("rank:全部", 30 * 60) == payload


def test_kv_get_missing(env):
    assert db.kv_get("no-such-key") is None
    assert db.kv_get_fresh("no-such-key", 60) is None


def test_kv_get_fresh_within_ttl(env):
    db.kv_set("fund_names", [{"code": "1", "name": "a"}])
    assert db.kv_get_fresh("fund_names", 30 * 60) == [{"code": "1", "name": "a"}]


def test_kv_get_fresh_expired(env, monkeypatch):
    db.kv_set("rank:指数型", {"total": 3})
    # 时钟拨到 31 分钟后：超过 30 分钟 TTL
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 31 * 60)
    assert db.kv_get_fresh("rank:指数型", 30 * 60) is None
    # 过期只影响“新鲜读取”；kv_get 仍能取到原始数据（services 用于降级）
    got = db.kv_get("rank:指数型")
    assert got is not None and json.loads(got[1]) == {"total": 3}


def test_kv_set_overwrite(env):
    db.kv_set("k", "v1")
    db.kv_set("k", "v2")
    _fetched_at, raw = db.kv_get("k")
    assert json.loads(raw) == "v2"


# ---------------------------------------------------------------- 历史净值

def test_nav_upsert_idempotent(env):
    rows1 = [("2026-10-19", 1.00, 1.00), ("2026-10-20", 1.02, 1.02)]
    assert db.nav_upsert("161725", rows1) == 2
    # 同 code/date 二次写入：不产生重复行，值被更新
    assert db.nav_upsert("161725", [("2026-10-20", 1.03, 1.03)]) == 1
    hist = db.nav_history("161725")
    assert len(hist) == 2
    by_date = {r["date"]: r for r in hist}
    assert by_date["2026-10-20"]["nav"] == 1.03
    assert by_date["2026-10-19"]["nav"] == 1.00


def test_nav_history_asc_and_start_date(env):
    # 乱序写入，读取必须按日期升序
    db.nav_upsert("900009", [
        ("2026-10-20", 1.02, 1.02),
        ("2026-10-18", 1.01, 1.01),
        ("2026-10-19", 1.015, 1.015),
    ])
    hist = db.nav_history("900009")
    assert [r["date"] for r in hist] == ["2026-10-18", "2026-10-19", "2026-10-20"]
    # start_date 截断（含边界）
    sub = db.nav_history("900009", start_date="2026-10-19")
    assert [r["date"] for r in sub] == ["2026-10-19", "2026-10-20"]
    # 不同 code 互不干扰
    assert db.nav_history("161725") == []


def test_nav_max_date(env):
    assert db.nav_max_date("110011") is None
    db.nav_upsert("110011", [
        ("2026-10-19", 1.00, 1.00),
        ("2026-10-20", 1.01, 1.01),
    ])
    assert db.nav_max_date("110011") == "2026-10-20"
    # 补拉更旧的数据不影响 max
    db.nav_upsert("110011", [("2026-10-15", 0.99, 0.99)])
    assert db.nav_max_date("110011") == "2026-10-20"


def test_nav_upsert_empty(env):
    assert db.nav_upsert("161725", []) == 0
    assert db.nav_history("161725") == []
