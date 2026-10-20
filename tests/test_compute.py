"""纯计算单测：直接调用 fundtool.services，不经过 HTTP。

函数签名依据 docs/API.md「services.py 规范函数签名」表（Lead 定稿）：
- rank_page(fund_type, sort, order, kw, page, size, force) -> dict
- add_holding(fund_code, shares, cost_nav, fund_name, note) -> dict（含实时计算字段）
- portfolio_view() -> dict
"""
import pandas as pd
import pytest

from conftest import MOCK_END_DATE, mock_last_nav
from fundtool import data
from fundtool.data.errors import FundNotAvailableError
from fundtool.data.mock_data import MOCK_FUNDS

N_FUNDS = len(MOCK_FUNDS)   # 当前样例库 27 只（12 真实代码 + 15 示例）

# 精度用例固定口径（任务指定）：shares=3333.33, cost_nav=1.11, nav=2.22
# 手工复算（10 位精度）：
#   market_value = 3333.33*2.22 = 7399.9926 → 7399.99
#   cost_value   = 3333.33*1.11 = 3699.9963 → 3700.00
#   pnl_pct      = (2.22/1.11-1)*100 = 100.0（nav/cost 与 mv/cv 两种口径同值）
#   pnl          = 7399.99-3700.00 = 3699.99（基于已舍入值）
#                  或 3333.33*(2.22-1.11)=3699.9963 → 3700.0（基于原始值）
# 契约只规定“四舍五入 2 位”，两种舍入次序都合法 → 断言二选一 + 一致性容差。
FIXED_CODE = "800001"
FIXED_NAV = 2.22
SH = 3333.33
COST = 1.11


def _rank_row(code, name, nav):
    return {
        "code": code, "name": name, "date": "2026-10-20",
        "nav": nav, "cum_nav": nav,
        "daily_pct": 0.5, "w1": 1.0, "m1": 2.0, "m3": 3.0, "m6": 4.0,
        "y1": 5.0, "y2": 6.0, "y3": 7.0, "ytd": 8.0, "since": 9.0,
        "fee": "0.15",
    }


class FixedNavClient:
    """固定净值数据源：rank 只返回指定行，供盈亏精度单测（净值完全确定）。"""

    def __init__(self, rows):
        self._rows = rows

    def get_open_fund_rank(self, fund_type="全部"):
        return pd.DataFrame(self._rows)

    def get_fund_names(self):
        return pd.DataFrame(
            [{"code": r["code"], "name": r["name"], "type": ""}
             for r in self._rows])

    def get_fund_nav_history(self, code, period="成立来"):
        raise FundNotAvailableError(f"样例库中没有基金 {code}")

    def get_fund_purchase(self):
        return pd.DataFrame(columns=["code", "name", "fee_rate", "discount"])


@pytest.fixture()
def services(env):
    from fundtool import services as _services
    return _services


class TestPnL:

    def test_positive_pnl(self, services):
        nav = mock_last_nav("161725")
        cost = round(nav * 0.8, 4)               # 成本低于现价 → 盈利
        h = services.add_holding("161725", 1000, cost)
        assert h["stale"] is False
        assert h["nav"] == nav
        assert h["nav_date"] == "2026-10-20"
        assert h["market_value"] == round(1000 * nav, 2)
        assert h["cost_value"] == round(1000 * cost, 2)
        assert h["pnl"] > 0
        assert h["pnl_pct"] > 0

    def test_negative_pnl(self, services):
        nav = mock_last_nav("900009")
        cost = round(nav * 1.2, 4)               # 成本高于现价 → 亏损
        h = services.add_holding("900009", 1000, cost)
        assert h["stale"] is False
        assert h["pnl"] < 0
        assert h["pnl_pct"] < 0
        assert h["cost_value"] > h["market_value"]

    def test_zero_pnl_nav_equals_cost(self, services):
        # nav == cost_nav → pnl / pnl_pct 均为 0
        data.set_client(FixedNavClient([_rank_row(FIXED_CODE, "精度测试基金", FIXED_NAV)]))
        h = services.add_holding(FIXED_CODE, SH, FIXED_NAV)
        assert h["market_value"] == 7399.99
        assert h["cost_value"] == 7399.99
        assert h["pnl"] == 0.0
        assert h["pnl_pct"] == 0.0

    def test_precision_rounding(self, services):
        data.set_client(FixedNavClient([_rank_row(FIXED_CODE, "精度测试基金", FIXED_NAV)]))
        h = services.add_holding(FIXED_CODE, SH, COST)
        assert h["market_value"] == 7399.99      # 7399.9926 → 2 位
        assert h["cost_value"] == 3700.0         # 3699.9963 → 2 位
        # pnl：两种合法舍入次序（基于已舍入值 3699.99 / 基于原始值 3700.0）
        assert h["pnl"] in (3699.99, 3700.0)
        assert abs(h["pnl"] - (h["market_value"] - h["cost_value"])) <= 0.01
        assert h["pnl_pct"] == 100.0
        # 所有金额/百分比字段均为 2 位小数
        for k in ("nav", "market_value", "cost_value", "pnl", "pnl_pct"):
            assert h[k] == round(h[k], 2), k


class TestNoNav:

    def test_stale_row_excluded_from_summary(self, services):
        services.add_holding("161725", 1000, 1.5)     # 有净值
        h = services.add_holding("123456", 100, 1.0)  # mock 名录中不存在
        assert h["stale"] is True
        assert h["nav"] is None
        assert h["nav_date"] is None
        assert h["market_value"] is None
        assert h["pnl"] is None
        assert h["pnl_pct"] is None
        assert h["fund_name"] == ""                    # 名录补不到 → 留空

        g = services.portfolio_view()
        assert g["summary"]["count"] == 2
        # summary 只统计有净值的行（161725）
        assert g["summary"]["total_mv"] == round(1000 * mock_last_nav("161725"), 2)
        assert g["summary"]["total_pnl"] is not None

    def test_all_holdings_no_nav_summary_none(self, services):
        services.add_holding("123456", 100, 1.0)
        services.add_holding("123457", 200, 2.0)
        g = services.portfolio_view()
        s = g["summary"]
        assert s["count"] == 2                          # count 恒为持仓数
        assert s["total_mv"] is None
        assert s["total_cost"] is None
        assert s["total_pnl"] is None
        assert s["total_pnl_pct"] is None


class TestRankPage:
    """rank_page 纯计算：排序 / kw 过滤 / 分页边界。"""

    def test_sort_1m_desc_default(self, services):
        b = services.rank_page()                  # 默认 sort=1m desc
        assert b["sort"] == "1m"
        assert b["total"] == N_FUNDS
        assert b["size"] == 20 and b["page"] == 1
        m1 = [r["m1"] for r in b["rows"]]
        assert m1 == sorted(m1, reverse=True)

    def test_sort_1m_asc(self, services):
        b = services.rank_page(sort="1m", order="asc")
        m1 = [r["m1"] for r in b["rows"]]
        assert m1 == sorted(m1)

    def test_sort_invalid_falls_back_to_1m(self, services):
        b = services.rank_page(sort="bogus")
        m1 = [r["m1"] for r in b["rows"]]
        assert m1 == sorted(m1, reverse=True)     # 非法值按 1m 处理

    def test_sort_ytd(self, services):
        b = services.rank_page(sort="ytd", order="desc", size=100)
        assert len(b["rows"]) == N_FUNDS
        ytd = [r["ytd"] for r in b["rows"]]
        assert ytd == sorted(ytd, reverse=True)

    def test_kw_code_substring(self, services):
        b = services.rank_page(kw="900")
        # 子串匹配：900001-900015 + 090010 = 16
        expected = sum(1 for c, *_ in MOCK_FUNDS if "900" in c)
        assert expected == 16
        assert b["total"] == expected
        assert all("900" in r["code"] for r in b["rows"])

    def test_kw_name_substring(self, services):
        b = services.rank_page(kw="白酒")
        assert b["total"] == 1
        assert b["rows"][0]["code"] == "161725"
        assert b["rows"][0]["name"] == "招商中证白酒"

    def test_kw_no_match(self, services):
        b = services.rank_page(kw="zzz-nomatch-xyz")
        assert b["total"] == 0
        assert b["rows"] == []

    def test_page_beyond_range(self, services):
        b = services.rank_page(page=99)
        assert b["total"] == N_FUNDS
        assert b["rows"] == []

    def test_size_clamp(self, services):
        # size 钳制 10–100
        b = services.rank_page(size=5)
        assert b["size"] == 10
        assert len(b["rows"]) == 10
        b2 = services.rank_page(size=1000)
        assert b2["size"] == 100
        assert len(b2["rows"]) == N_FUNDS

    def test_type_filter(self, services):
        b = services.rank_page(fund_type="指数型", size=100)
        assert b["total"] == 3
        assert {r["code"] for r in b["rows"]} == {"161725", "900009", "900010"}
