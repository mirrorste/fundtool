"""TestClient 全路由测试（MockClient end_date=2026-10-20，数据完全确定性）。

断言依据：docs/API.md 契约 v0.1。
注意：2026-10-20 为周二；3m 截断 = end_date - 91 自然日 = 2026-07-21（也是周二）。
"""
import math
import re
import time

from conftest import MOCK_END_DATE, mock_last_nav
from fundtool import data
from fundtool.data.errors import DataSourceError
from fundtool.data.mock_data import MOCK_FUNDS, MockClient

TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

MOCK_CODES = {c for c, _n, *_ in MOCK_FUNDS}
INDEX_CODES = {"161725", "900009", "900010"}
RANK_ROW_KEYS = ("code", "name", "date", "nav", "cum_nav", "daily_pct",
                 "w1", "m1", "m3", "m6", "y1", "y2", "y3", "ytd", "since", "fee")


def _clear_portfolio(client):
    """清空现有持仓（含 mock 启动时可能播种的演示持仓），保证起始状态确定。"""
    for h in client.get("/api/portfolio").json()["holdings"]:
        client.delete(f"/api/portfolio/{h['id']}")


class TestHealth:

    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        b = r.json()
        assert b["ok"] is True
        assert b["mode"] == "mock"          # env fixture 已置 config.MOCK=True
        assert b["version"] == "0.1.0"
        assert TIME_RE.match(b["now"])


class TestRank:

    def test_default(self, client):
        r = client.get("/api/rank")
        assert r.status_code == 200
        b = r.json()
        assert b["type"] == "全部"
        assert b["sort"] == "1m"                  # 契约默认 1m（SORT_FIELDS key）
        assert b["order"] == "desc"
        assert b["page"] == 1
        assert b["size"] == 20
        assert b["total"] == len(MOCK_FUNDS)          # 样例库全部基金
        assert len(b["rows"]) == 20
        assert TIME_RE.match(b["fetched_at"])
        for row in b["rows"]:
            for k in RANK_ROW_KEYS:
                assert k in row
            assert row["date"] == "2026-10-20"
        # 默认 sort=1m desc（m1 为行内数据字段名）
        m1 = [row["m1"] for row in b["rows"]]
        assert m1 == sorted(m1, reverse=True)

    def test_type_index_only(self, client):
        r = client.get("/api/rank", params={"type": "指数型"})
        assert r.status_code == 200
        b = r.json()
        assert b["total"] == 3
        assert {row["code"] for row in b["rows"]} == INDEX_CODES

    def test_sort_daily_asc(self, client):
        r = client.get("/api/rank", params={"sort": "daily", "order": "asc"})
        assert r.status_code == 200
        d = [row["daily_pct"] for row in r.json()["rows"]]
        assert d == sorted(d)

    def test_sort_1m_asc(self, client):
        r = client.get("/api/rank", params={"sort": "1m", "order": "asc"})
        m1 = [row["m1"] for row in r.json()["rows"]]
        assert m1 == sorted(m1)

    def test_sort_invalid_falls_back_to_1m(self, client):
        # 契约：sort 非法值按 1m 处理
        r = client.get("/api/rank", params={"sort": "bogus"})
        assert r.status_code == 200
        m1 = [row["m1"] for row in r.json()["rows"]]
        assert m1 == sorted(m1, reverse=True)     # 1m desc

    def test_kw_code_substring(self, client):
        # "900" 是子串匹配：900001-900015（15 只）+ 090010（代码中段含 900）= 16 只
        r = client.get("/api/rank", params={"kw": "900"})
        b = r.json()
        expected = sum(1 for c, *_ in MOCK_FUNDS if "900" in c)
        assert expected == 16
        assert b["total"] == expected
        assert all("900" in row["code"] for row in b["rows"])

    def test_kw_name_substring(self, client):
        r = client.get("/api/rank", params={"kw": "白酒"})
        b = r.json()
        assert b["total"] == 1
        assert b["rows"][0]["code"] == "161725"

    def test_kw_no_match(self, client):
        r = client.get("/api/rank", params={"kw": "不存在的xyz"})
        assert r.status_code == 200
        b = r.json()
        assert b["total"] == 0
        assert b["rows"] == []

    def test_size_100_boundary(self, client):
        r = client.get("/api/rank", params={"size": 100})
        assert r.status_code == 200
        b = r.json()
        assert b["size"] == 100
        assert b["total"] == len(MOCK_FUNDS)
        assert len(b["rows"]) == len(MOCK_FUNDS)     # 全部 < 100，单页返回

    def test_page_beyond_range(self, client):
        r = client.get("/api/rank", params={"page": 10, "size": 20})
        assert r.status_code == 200
        b = r.json()
        assert b["total"] == len(MOCK_FUNDS)
        assert b["rows"] == []

    def test_page_2_slice(self, client):
        b1 = client.get("/api/rank", params={"page": 1, "size": 10}).json()
        b2 = client.get("/api/rank", params={"page": 2, "size": 10}).json()
        assert len(b1["rows"]) == 10
        assert len(b2["rows"]) == 10
        # 同排序下两页无交集
        c1 = {row["code"] for row in b1["rows"]}
        c2 = {row["code"] for row in b2["rows"]}
        assert c1.isdisjoint(c2)

    def test_force_accepted(self, client):
        r = client.get("/api/rank", params={"force": 1})
        assert r.status_code == 200


class TestFundSearch:

    def test_search_code(self, client):
        r = client.get("/api/fund/search", params={"kw": "161725"})
        assert r.status_code == 200
        assert r.json()["results"] == [
            {"code": "161725", "name": "招商中证白酒", "type": "指数型"}]

    def test_search_name(self, client):
        r = client.get("/api/fund/search", params={"kw": "白酒"})
        assert r.status_code == 200
        assert [x["code"] for x in r.json()["results"]] == ["161725"]

    def test_search_no_match(self, client):
        r = client.get("/api/fund/search", params={"kw": "无此基金"})
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_search_cap_10(self, client):
        # "示例" 命中 15 只 9xxxxx 名称 → 最多返回 10 条
        r = client.get("/api/fund/search", params={"kw": "示例"})
        assert r.status_code == 200
        assert len(r.json()["results"]) == 10

    def test_search_kw_required(self, client):
        r = client.get("/api/fund/search")
        assert r.status_code == 422


class TestFundInfo:

    def test_info(self, client):
        r = client.get("/api/fund/161725/info")
        assert r.status_code == 200
        b = r.json()
        assert b["code"] == "161725"
        assert b["name"] == "招商中证白酒"
        assert b["type"] == "指数型"
        assert b["date"] == "2026-10-20"
        assert b["nav"] == mock_last_nav("161725")
        assert b["source"] in {"rank_cache", "nav_hist"}
        assert set(b["periods"]) == {"daily_pct", "w1", "m1", "m3", "m6",
                                     "y1", "y2", "y3", "ytd", "since"}
        # mock 的 purchase 全覆盖
        assert b["purchase"] is not None
        assert b["purchase"]["fee_rate"] == "0.15%"
        assert b["purchase"]["discount"] == "0.1"

    def test_info_unknown(self, client):
        r = client.get("/api/fund/999999/info")
        assert r.status_code == 404
        assert "error" in r.json()


class TestFundNav:

    def test_nav_3m(self, client):
        r = client.get("/api/fund/161725/nav", params={"range": "3m"})
        assert r.status_code == 200
        b = r.json()
        assert b["code"] == "161725"
        assert b["name"] == "招商中证白酒"
        assert b["range"] == "3m"
        series = b["series"]
        assert series
        dates = [p["date"] for p in series]
        # 升序
        assert dates == sorted(dates)
        # 3m = 91 自然日：下界 2026-07-21；上界 end_date
        assert all(d >= "2026-07-21" for d in dates)
        assert dates[-1] == "2026-10-20"
        # 首点 normalized=100.0
        assert series[0]["normalized"] == 100.0
        # 次点 normalized 手工复算
        n0, n1 = series[0]["nav"], series[1]["nav"]
        assert series[1]["normalized"] == round(n1 / n0 * 100, 2)
        # mock 数据 cum_nav 恒非 null
        assert all(p["cum_nav"] is not None for p in series)

    def test_nav_1y_more_points_than_3m(self, client):
        b1 = client.get("/api/fund/161725/nav", params={"range": "1y"}).json()
        b3 = client.get("/api/fund/161725/nav", params={"range": "3m"}).json()
        assert len(b1["series"]) > len(b3["series"])

    def test_nav_default_range_1y(self, client):
        r = client.get("/api/fund/161725/nav")
        assert r.status_code == 200
        assert r.json()["range"] == "1y"

    def test_nav_all(self, client):
        # mock 序列 550 个交易日；all 应包含全部
        r = client.get("/api/fund/161725/nav", params={"range": "all"})
        assert r.status_code == 200
        series = r.json()["series"]
        assert len(series) == 550
        assert series[0]["normalized"] == 100.0

    def test_nav_unknown(self, client):
        r = client.get("/api/fund/999999/nav")
        assert r.status_code == 404
        assert "error" in r.json()


class NaNCumClient(MockClient):
    """模拟真实接口口径：单位净值正常，cum_nav 因左连接缺失而为 NaN。"""

    def get_fund_nav_history(self, code, period="成立来"):
        import pandas as pd

        df = super().get_fund_nav_history(code, period)
        df["cum_nav"] = float("nan")
        return df


class TestNavNaN:
    """cum_nav NaN 回归（真实接口 pandas 左连接缺失值；Starlette JSON 不允许 NaN）。"""

    def test_nav_fetch_nan_cum_not_persisted(self, env, client):
        data.set_client(NaNCumClient(end_date=MOCK_END_DATE))
        r = client.get("/api/fund/161725/nav", params={"range": "1m"})
        assert r.status_code == 200
        series = r.json()["series"]
        assert series
        # 响应中 cum_nav 一律 null（不得出现 NaN）
        assert all(p["cum_nav"] is None for p in series)
        # NaN 不得写入 DB
        from fundtool import db
        raw = db.nav_history("161725")
        assert raw
        for row in raw:
            c = row["cum_nav"]
            assert not (isinstance(c, float) and math.isnan(c))

    def test_nav_db_row_with_nan_cum_still_200(self, env, client):
        # 历史遗留：DB 中已存在含 NaN cum_nav 的行 → GET 仍 200 且 cum_nav=null
        from fundtool import db
        db.nav_upsert("161725", [
            ("2026-10-19", 1.00, float("nan")),
            ("2026-10-20", 1.02, 1.02),
        ])
        r = client.get("/api/fund/161725/nav", params={"range": "1m"})
        assert r.status_code == 200
        by_date = {p["date"]: p for p in r.json()["series"]}
        assert by_date["2026-10-19"]["cum_nav"] is None
        assert by_date["2026-10-20"]["cum_nav"] == 1.02


HOLDING_ROW_KEYS = ("id", "code", "name", "note", "shares", "cost_nav",
                    "nav", "nav_date", "market_value", "cost_value",
                    "pnl", "pnl_pct", "stale")


class TestPortfolioCRUD:

    def test_full_chain(self, client):
        _clear_portfolio(client)
        nav = mock_last_nav("161725")
        shares, cost_nav = 10000, 1.65
        mv = round(shares * nav, 2)
        cv = round(shares * cost_nav, 2)

        # 1) POST 201，fund_name 自动补全
        r = client.post("/api/portfolio", json={
            "fund_code": "161725", "shares": shares, "cost_nav": cost_nav,
            "fund_name": "", "note": "白酒赛道"})
        assert r.status_code == 201
        row = r.json()
        hid = row["id"]
        assert row["code"] == "161725"
        assert row["fund_name"] == "招商中证白酒"
        assert row["shares"] == shares
        assert row["cost_nav"] == cost_nav
        assert row["nav"] == nav
        assert row["nav_date"] == "2026-10-20"
        assert row["market_value"] == mv
        assert row["cost_value"] == cv
        assert row["stale"] is False

        # 2) 重复 POST → 409
        r2 = client.post("/api/portfolio", json={
            "fund_code": "161725", "shares": shares, "cost_nav": cost_nav})
        assert r2.status_code == 409
        assert "error" in r2.json()

        # 3) GET：盈亏数值手工验算（mock 净值确定）
        g = client.get("/api/portfolio").json()
        for k in HOLDING_ROW_KEYS:
            assert k in g["holdings"][0]
        h = [x for x in g["holdings"] if x["code"] == "161725"][0]
        assert h["market_value"] == mv
        assert h["cost_value"] == cv
        # 10000 份 × 4 位小数净值 → mv 为整数，两种舍入口径结果一致
        assert h["pnl"] == round(mv - cv, 2)
        assert h["pnl_pct"] == round((nav / cost_nav - 1) * 100, 2)
        assert g["summary"]["count"] == 1
        assert g["summary"]["total_mv"] == mv

        # 4) PUT 改 shares → 200；市值翻倍
        r = client.put(f"/api/portfolio/{hid}", json={"shares": 20000})
        assert r.status_code == 200
        g2 = client.get("/api/portfolio").json()
        h2 = [x for x in g2["holdings"] if x["code"] == "161725"][0]
        assert h2["shares"] == 20000
        assert h2["market_value"] == round(20000 * nav, 2)
        assert abs(h2["market_value"] - 2 * mv) <= 0.01
        # 单位成本不变，总成本随份额翻倍
        assert h2["cost_nav"] == cost_nav
        assert h2["cost_value"] == round(20000 * cost_nav, 2)

        # 5) 非法入参 → 422
        assert client.post("/api/portfolio", json={
            "fund_code": "12345", "shares": 100, "cost_nav": 1.0
        }).status_code == 422                              # 5 位代码
        assert client.post("/api/portfolio", json={
            "fund_code": "000011", "shares": 0, "cost_nav": 1.0
        }).status_code == 422                              # shares=0
        assert client.post("/api/portfolio", json={
            "fund_code": "000011", "shares": 100, "cost_nav": 0
        }).status_code == 422                              # cost_nav=0

        # 6) DELETE 200 → 再 DELETE 404 → 持仓表清空
        r = client.delete(f"/api/portfolio/{hid}")
        assert r.status_code == 200
        assert r.json() == {"deleted": True}
        assert client.delete(f"/api/portfolio/{hid}").status_code == 404
        from fundtool import db
        assert db.list_holdings() == []
        # 契约：mock 模式 GET /api/portfolio 对空表惰性播种 → 重新出现 4 条演示持仓
        g3 = client.get("/api/portfolio").json()
        assert g3["summary"]["count"] == 4
        assert {h["code"] for h in g3["holdings"]} == \
            {"161725", "005827", "110011", "900006"}

    def test_unknown_holding_id(self, client):
        assert client.put("/api/portfolio/99999", json={"shares": 1}) \
            .status_code == 404
        assert client.delete("/api/portfolio/99999").status_code == 404


class TestPortfolioByValue:
    """按「当前市值 + 持有盈亏」反推份额与成本净值（新增持仓的第二种录入方式）。"""

    def test_by_pnl_pct(self, client):
        _clear_portfolio(client)
        nav = mock_last_nav("161725")
        mv, pct = 10000.0, 12.5
        r = client.post("/api/portfolio", json={
            "fund_code": "161725", "market_value": mv, "pnl_pct": pct})
        assert r.status_code == 201
        row = r.json()
        expected_shares = round(mv / nav, 2)
        expected_cost = round(nav / 1.125, 4)
        assert row["shares"] == expected_shares
        assert row["cost_nav"] == expected_cost
        # 反推出的市值贴近输入（份额 2 位小数舍入导致 ≤0.01 偏差）
        assert abs(row["market_value"] - mv) <= 0.01
        # 反推后盈亏率与输入值一致（成本净值 4 位小数舍入误差 <0.05pct）
        assert abs(row["pnl_pct"] - pct) < 0.05
        assert row["fund_name"] == "招商中证白酒"

    def test_by_pnl_amount(self, client):
        _clear_portfolio(client)
        nav = mock_last_nav("161725")
        mv, pnl = 10000.0, 1000.0
        r = client.post("/api/portfolio", json={
            "fund_code": "161725", "market_value": mv, "pnl_amount": pnl})
        assert r.status_code == 201
        row = r.json()
        expected_shares = round(mv / nav, 2)
        assert row["shares"] == expected_shares
        assert row["cost_nav"] == round((mv - pnl) / expected_shares, 4)
        assert abs(row["pnl"] - pnl) <= 0.5

    def test_loss_amount_accepted(self, client):
        _clear_portfolio(client)
        r = client.post("/api/portfolio", json={
            "fund_code": "005827", "market_value": 10000, "pnl_pct": -8})
        assert r.status_code == 201
        assert r.json()["pnl_pct"] == -8.0 or abs(r.json()["pnl_pct"] + 8) < 0.05

    def test_conflicting_modes_rejected(self, client):
        _clear_portfolio(client)
        cases = [
            {"fund_code": "000011", "shares": 100, "cost_nav": 1.0,
             "market_value": 1000, "pnl_pct": 5},            # 两种方式混填
            {"fund_code": "000011", "market_value": 1000,
             "pnl_pct": 5, "pnl_amount": 50},                # 盈亏率与额同时给
            {"fund_code": "000011", "market_value": 1000},    # 缺盈亏
            {"fund_code": "000011", "pnl_pct": 5},            # 缺市值
            {"fund_code": "000011", "shares": 100},           # 直接模式缺成本
        ]
        for body in cases:
            r = client.post("/api/portfolio", json=body)
            assert r.status_code == 422, (body, r.status_code, r.text)
            assert "detail" in r.json()
        # mock 模式 GET 会对空表惰性播种，故只校验被拒的代码没有落库
        codes = {h["code"] for h in client.get("/api/portfolio").json()["holdings"]}
        assert "000011" not in codes

    def test_invalid_pnl_values_rejected(self, client):
        _clear_portfolio(client)
        cases = [
            {"fund_code": "000011", "market_value": 1000, "pnl_pct": -100},
            {"fund_code": "000011", "market_value": 1000, "pnl_amount": 1000},
            {"fund_code": "000011", "market_value": 0, "pnl_pct": 5},
            {"fund_code": "000011", "market_value": -1, "pnl_amount": 10},
        ]
        for body in cases:
            assert client.post("/api/portfolio", json=body).status_code == 422, body

    def test_unknown_fund_by_value(self, client):
        _clear_portfolio(client)
        r = client.post("/api/portfolio", json={
            "fund_code": "999999", "market_value": 1000, "pnl_pct": 5})
        assert r.status_code in (404, 503)


class TestPortfolioNoNav:
    """净值解析失败（排行/历史都没有）→ stale 行与 summary 排除。"""

    def test_stale_row_and_summary(self, client):
        _clear_portfolio(client)
        # 123456 是合法 6 位代码但不在 mock 名录中 → 查不到净值
        r = client.post("/api/portfolio", json={
            "fund_code": "123456", "shares": 100, "cost_nav": 1.0})
        assert r.status_code == 201
        row = r.json()
        assert row["fund_name"] == ""            # 名录补不到 → 留空
        assert row["nav"] is None
        assert row["nav_date"] is None
        assert row["market_value"] is None
        assert row["pnl"] is None
        assert row["pnl_pct"] is None
        assert row["stale"] is True

        g = client.get("/api/portfolio").json()
        h = g["holdings"][0]
        assert h["stale"] is True and h["market_value"] is None
        s = g["summary"]
        assert s["count"] == 1                   # count 恒为持仓数
        # 全仓无净值 → summary 各项 None
        assert s["total_mv"] is None
        assert s["total_cost"] is None
        assert s["total_pnl"] is None
        assert s["total_pnl_pct"] is None

    def test_summary_excludes_stale_rows(self, client):
        _clear_portfolio(client)
        nav16 = mock_last_nav("161725")
        client.post("/api/portfolio", json={
            "fund_code": "161725", "shares": 1000, "cost_nav": round(nav16 * 1.1, 4)})
        client.post("/api/portfolio", json={
            "fund_code": "123456", "shares": 100, "cost_nav": 1.0})
        g = client.get("/api/portfolio").json()
        assert g["summary"]["count"] == 2
        # summary 只统计有净值的行（161725）
        assert g["summary"]["total_mv"] == round(1000 * nav16, 2)
        h16 = [x for x in g["holdings"] if x["code"] == "161725"][0]
        assert h16["pnl"] < 0                    # 成本高于现价 → 亏
        assert g["summary"]["total_pnl"] < 0


class BrokenClient:
    """模拟数据源故障：所有方法抛 DataSourceError。"""

    def get_open_fund_rank(self, fund_type="全部"):
        raise DataSourceError("数据源请求失败：网络不可达")

    def get_fund_names(self):
        raise DataSourceError("数据源请求失败：网络不可达")

    def get_fund_nav_history(self, code, period="成立来"):
        raise DataSourceError("数据源请求失败：网络不可达")

    def get_fund_purchase(self):
        raise DataSourceError("数据源请求失败：网络不可达")


class TestDegrade:

    def test_rank_503(self, env, client):
        data.set_client(BrokenClient())
        r = client.get("/api/rank")
        assert r.status_code == 503
        assert "数据源" in r.json()["error"]

    def test_search_503(self, env, client):
        data.set_client(BrokenClient())
        r = client.get("/api/fund/search", params={"kw": "161725"})
        assert r.status_code == 503
        assert "数据源" in r.json()["error"]

    def test_nav_fallback_to_db_cache(self, env, client):
        # 1) 正常客户端：先拉排行（暖名录缓存）+ 161725 净值（写入 DB）
        assert client.get("/api/rank").status_code == 200
        r = client.get("/api/fund/161725/nav", params={"range": "1y"})
        assert r.status_code == 200
        cached = r.json()["series"]
        assert cached

        # 2) 换坏客户端：有 DB 缓存 → 仍 200，X-Nav-Stale=1，数据来自缓存
        data.set_client(BrokenClient())
        r2 = client.get("/api/fund/161725/nav", params={"range": "1y"})
        assert r2.status_code == 200
        assert r2.headers.get("x-nav-stale") == "1"
        assert r2.json()["series"] == cached

        # 3) 无 DB 缓存（900009 从未拉过）+ 坏客户端 → 503
        r3 = client.get("/api/fund/900009/nav", params={"range": "1y"})
        assert r3.status_code == 503
        assert "数据源" in r3.json()["error"]

    def test_portfolio_fresh_cache_survives_broken_client(self, env, client):
        # 暖排行缓存（TTL 30 分钟内）后数据源故障：
        # 净值来自新鲜排行快照（不联网）→ 行非 stale
        _clear_portfolio(client)
        client.post("/api/portfolio", json={
            "fund_code": "161725", "shares": 1000, "cost_nav": 1.65})
        data.set_client(BrokenClient())
        r = client.get("/api/portfolio")
        assert r.status_code == 200
        h = r.json()["holdings"][0]
        assert h["stale"] is False
        assert h["nav"] == mock_last_nav("161725")

    def test_portfolio_stale_on_broken_client(self, env, client, monkeypatch):
        _clear_portfolio(client)
        client.post("/api/portfolio", json={
            "fund_code": "161725", "shares": 1000, "cost_nav": 1.65})
        data.set_client(BrokenClient())
        # 时钟拨到 31 分钟后：排行缓存过期 → 数据源故障且无新鲜缓存
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 31 * 60)
        # 契约：数据源抛 DataSourceError（无新鲜缓存）→ 照常返回，行 stale=true
        r = client.get("/api/portfolio")
        assert r.status_code == 200
        h = r.json()["holdings"][0]
        assert h["stale"] is True
        assert h["nav"] is None and h["market_value"] is None


class TestMockEndToEnd:
    """完整冒烟：排行 → 详情 → 净值 → 持仓（等价人工冒烟）。"""

    def test_smoke(self, client):
        _clear_portfolio(client)

        # 1) 排行：全部基金、1m 降序
        b = client.get("/api/rank", params={"sort": "1m"}).json()
        assert b["total"] == len(MOCK_FUNDS)
        assert {row["code"] for row in b["rows"]} <= MOCK_CODES

        # 2) 详情：161725 固定口径
        info = client.get("/api/fund/161725/info").json()
        assert info["nav"] == mock_last_nav("161725")
        assert info["type"] == "指数型"

        # 3) 净值：末点 = end_date
        nav_b = client.get("/api/fund/161725/nav", params={"range": "3m"}).json()
        assert nav_b["series"][-1]["date"] == "2026-10-20"

        # 4) 持仓：成本高于现价 → 亏损
        client.post("/api/portfolio", json={
            "fund_code": "161725", "shares": 1000,
            "cost_nav": round(info["nav"] * 1.1, 4)})
        g = client.get("/api/portfolio").json()
        assert g["summary"]["count"] == 1
        h = g["holdings"][0]
        assert h["stale"] is False
        assert h["pnl"] is not None and h["pnl"] < 0
        assert h["pnl_pct"] < 0
        assert g["summary"]["total_pnl"] < 0
