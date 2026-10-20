"""内置样例数据（--mock / FUNDTOOL_MOCK 模式）。

离线演示用：用确定性伪随机游走生成各基金的净值序列，排行/详情/持仓
数据全部由该序列推导，保证各页面口径一致。样例含少量真实基金代码
（161725 招商中证白酒 等）与 9xxxxx 区间显式标注的"示例"基金，
数值均为虚构，不代表真实净值。
"""
import random
from datetime import date, timedelta

import pandas as pd

from .errors import FundNotAvailableError

# (code, name, type, 年化漂移, 年化波动, 起始净值)
MOCK_FUNDS = [
    # —— 真实代码（著名基金，数值为虚构）——
    ("000001", "华夏成长混合", "混合型", 0.03, 0.20, 1.35),
    ("000011", "华夏大盘精选", "混合型", 0.02, 0.18, 2.10),
    ("090010", "大成价值增长", "混合型", 0.015, 0.17, 1.10),
    ("110011", "易方达中小盘", "混合型", 0.04, 0.22, 1.60),
    ("110022", "易方达消费行业", "股票型", 0.03, 0.26, 3.20),
    ("161005", "富国天惠成长", "混合型", 0.035, 0.21, 2.80),
    ("161725", "招商中证白酒", "指数型", 0.05, 0.30, 1.55),
    ("260101", "景顺长城优选", "混合型", 0.025, 0.19, 1.90),
    ("519697", "交银先进制造", "股票型", 0.04, 0.27, 2.40),
    ("005827", "易方达蓝筹精选", "混合型", 0.03, 0.23, 2.30),
    ("003834", "兴全趋势投资", "混合型", 0.03, 0.20, 3.50),
    ("001102", "前海开源大盘蓝筹", "股票型", 0.02, 0.25, 1.80),
    # —— 显式示例（9xxxxx 区间）——
    ("900001", "示例股票基金A", "股票型", 0.06, 0.32, 1.00),
    ("900002", "示例股票基金B", "股票型", 0.01, 0.28, 1.00),
    ("900003", "示例股票基金C", "股票型", 0.045, 0.35, 1.00),
    ("900004", "示例混合基金A", "混合型", 0.035, 0.18, 1.00),
    ("900005", "示例混合基金B", "混合型", -0.02, 0.21, 1.00),
    ("900006", "示例债券基金A", "债券型", 0.03, 0.015, 1.00),
    ("900007", "示例债券基金B", "债券型", 0.025, 0.02, 1.00),
    ("900008", "示例短债基金", "债券型", 0.022, 0.008, 1.00),
    ("900009", "示例指数基金A", "指数型", 0.05, 0.24, 1.00),
    ("900010", "示例指数基金B", "指数型", -0.01, 0.29, 1.00),
    ("900011", "示例QDII基金A", "QDII", 0.0, 0.26, 1.00),
    ("900012", "示例FOF基金A", "FOF", 0.02, 0.12, 1.00),
    ("900013", "示例股票基金D", "股票型", 0.07, 0.40, 1.00),
    ("900014", "示例混合基金C", "混合型", 0.05, 0.16, 1.00),
    ("900015", "示例债券基金C", "债券型", 0.035, 0.012, 1.00),
]

# 默认演示持仓（仅 mock 模式且持仓表为空时写入）
DEFAULT_MOCK_HOLDINGS = [
    ("161725", "招商中证白酒", 10000, 1.65, "白酒赛道"),
    ("005827", "易方达蓝筹精选", 8000, 2.20, "核心持仓"),
    ("110011", "易方达中小盘", 15000, 1.50, ""),
    ("900006", "示例债券基金A", 50000, 1.02, "防守仓位"),
]

# 保留：供外部参考（services 层实际使用 config.NAV_RANGE_DAYS 截断）
_PERIOD_DAYS = {
    "近1月": 30,
    "近3月": 91,
    "近6月": 182,
    "近1年": 365,
    "近3年": 1096,
    "成立来": None,
}


def _trading_days(end_date: date, count: int) -> list:
    """返回截至 end_date（含）的最近 count 个工作日（仅去周末）。"""
    days, cur = [], end_date
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    return list(reversed(days))


def generate_nav_series(code: str, drift: float, vol: float,
                        start_nav: float, end_date: date,
                        n_days: int = 550) -> list:
    """确定性伪随机游走，返回 [(date, nav, cum_nav), ...] 升序。"""
    rng = random.Random(int(code))
    days = _trading_days(end_date, n_days)
    nav = start_nav
    rows = []
    for d in days:
        nav *= 1.0 + (drift + rng.gauss(0, vol)) / 252.0
        rows.append((d.isoformat(), round(nav, 4), round(nav, 4)))
    return rows


class MockClient:
    """与 AkshareClient 同接口的离线数据客户端。"""

    def __init__(self, end_date: date = None):
        self.end_date = end_date or date.today()
        self._series = {
            code: generate_nav_series(code, drift, vol, nav0, self.end_date)
            for code, _name, _type, drift, vol, nav0 in MOCK_FUNDS
        }

    # ------------------------------------------------------------------

    def _rank_rows(self) -> list:
        rows = []
        for code, name, ftype, _d, _v, _n in MOCK_FUNDS:
            s = self._series[code]
            navs = [r[1] for r in s]
            dates = [r[0] for r in s]
            last, prev = navs[-1], navs[-2]

            def ret(offset_bdays: int):
                # 约 offset 个工作日之前
                idx = max(0, len(navs) - 1 - offset_bdays)
                return (navs[-1] / navs[idx] - 1.0) * 100.0

            # 年初：上一年最后一个工作日
            ytd_base = navs[0]
            for i, dt in enumerate(dates):
                if dt[:4] == self.end_date.isoformat()[:4]:
                    ytd_base = navs[i - 1] if i > 0 else navs[0]
                    break
            row = {
                "code": code,
                "name": name,
                "date": self.end_date.isoformat(),
                "nav": last,
                "cum_nav": s[-1][2],
                "daily_pct": round((last / prev - 1.0) * 100.0, 2),
                "w1": round(ret(5), 2),
                "m1": round(ret(21), 2),
                "m3": round(ret(63), 2),
                "m6": round(ret(126), 2),
                "y1": round(ret(252), 2),
                "y2": round(ret(504), 2),
                "y3": round(ret(756), 2),
                "ytd": round((last / ytd_base - 1.0) * 100.0, 2),
                "since": round((last / navs[0] - 1.0) * 100.0, 2),
                "fee": "0.15",
            }
            rows.append(row)
        return rows

    _RANK_COLUMNS = ["code", "name", "date", "nav", "cum_nav", "daily_pct",
                     "w1", "m1", "m3", "m6", "y1", "y2", "y3", "ytd",
                     "since", "fee"]

    def get_open_fund_rank(self, fund_type: str = "全部") -> pd.DataFrame:
        rows = [r for r in self._rank_rows()
                if fund_type == "全部"
                or self._type_of(r["code"]) == fund_type]
        return pd.DataFrame(rows, columns=self._RANK_COLUMNS)

    def _type_of(self, code: str) -> str:
        return dict((c, t) for c, _n, t, *_ in MOCK_FUNDS)[code]

    def get_fund_names(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"code": c, "name": n, "type": t}
             for c, n, t, *_ in MOCK_FUNDS]
        )

    def get_fund_nav_history(self, code: str,
                             period: str = "成立来") -> pd.DataFrame:
        """与真实客户端口径一致：恒返回成立来全量序列（period 参数忽略）。"""
        if code not in self._series:
            raise FundNotAvailableError(f"样例库中没有基金 {code}")
        s = self._series[code]
        rows = []
        for i, (d, nav, cum) in enumerate(s):
            base = s[i - 1][1] if i > 0 else nav
            rows.append({
                "date": d,
                "nav": nav,
                "cum_nav": cum,
                "daily_pct": round((nav / base - 1.0) * 100.0, 2),
            })
        return pd.DataFrame(rows)

    def get_fund_purchase(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"code": c, "name": n, "fee_rate": "0.15%", "discount": "0.1"}
             for c, n, *_ in MOCK_FUNDS]
        )
