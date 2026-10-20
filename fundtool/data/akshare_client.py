"""真实数据客户端：AKShare 薄封装。

职责：调用 AKShare 基金接口 → 列名归一化（英文列）→ 重试/失败归一为
DataSourceError。业务层不感知 AKShare 的中文列名。
"""
import time

import pandas as pd

from .. import config
from .errors import DataSourceError, FundNotAvailableError  # noqa: F401  (re-export)


def _with_retries(fn, *args, **kwargs):
    last_exc = None
    for attempt in range(config.MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # 数据源异常种类多，统一收敛
            last_exc = exc
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(config.RETRY_BACKOFF ** (attempt + 1))
    raise DataSourceError(
        f"数据源请求失败（已重试 {config.MAX_RETRIES} 次）："
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.strftime("%Y-%m-%d")


class AkshareClient:

    def get_open_fund_rank(self, fund_type: str = "全部") -> pd.DataFrame:
        import akshare as ak
        raw = _with_retries(ak.fund_open_fund_rank_em, symbol=fund_type)
        return self._norm_rank(raw)

    @staticmethod
    def _norm_rank(df: pd.DataFrame) -> pd.DataFrame:
        if "基金代码" not in df.columns:
            raise DataSourceError(f"排行接口返回列异常：{list(df.columns)[:8]}")
        num = lambda col: pd.to_numeric(df[col], errors="coerce")  # noqa: E731
        return pd.DataFrame({
            "code": df["基金代码"].astype(str),
            "name": df["基金简称"].astype(str),
            "date": _to_date(df["日期"]),
            "nav": num("单位净值"),
            "cum_nav": num("累计净值"),
            "daily_pct": num("日增长率"),
            "w1": num("近1周"),
            "m1": num("近1月"),
            "m3": num("近3月"),
            "m6": num("近6月"),
            "y1": num("近1年"),
            "y2": num("近2年"),
            "y3": num("近3年"),
            "ytd": num("今年来"),
            "since": num("成立来"),
            "fee": df["手续费"].astype(str) if "手续费" in df.columns else "",
        })

    def get_fund_names(self) -> pd.DataFrame:
        import akshare as ak
        raw = _with_retries(ak.fund_name_em)
        return pd.DataFrame({
            "code": raw["基金代码"].astype(str),
            "name": raw["基金简称"].astype(str),
            "type": raw["类型"].astype(str),
        })

    def get_fund_nav_history(self, code: str, period: str = "成立来") -> pd.DataFrame:
        """恒返回成立来全量净值（升序）。

        真实接口的"单位净值走势"分支忽略 period 参数，只含
        净值日期/单位净值/日增长率 三列；累计净值另取"累计净值走势"
        指标按日期合并（失败时 cum_nav 为 None，不影响主流程）。
        """
        import akshare as ak
        raw = _with_retries(ak.fund_open_fund_info_em,
                            symbol=code, indicator="单位净值走势")
        if raw is None or len(raw) == 0:
            raise FundNotAvailableError(f"基金 {code} 未查询到净值数据（代码可能不存在）")
        date_col = "净值日期" if "净值日期" in raw.columns else "日期"
        out = pd.DataFrame({
            "date": _to_date(raw[date_col]),
            "nav": pd.to_numeric(raw["单位净值"], errors="coerce"),
            "daily_pct": (pd.to_numeric(raw["日增长率"], errors="coerce")
                          if "日增长率" in raw.columns else None),
        })
        out = out.dropna(subset=["date", "nav"])
        out = out.sort_values("date").reset_index(drop=True)
        try:
            cum_raw = _with_retries(ak.fund_open_fund_info_em,
                                    symbol=code, indicator="累计净值走势")
            if cum_raw is not None and len(cum_raw) > 0:
                cum = pd.DataFrame({
                    "date": _to_date(
                        cum_raw["净值日期"] if "净值日期" in cum_raw.columns
                        else cum_raw["日期"]),
                    "cum_nav": pd.to_numeric(cum_raw["累计净值"], errors="coerce"),
                }).dropna(subset=["date"])
                out = out.merge(cum, on="date", how="left")
        except DataSourceError:
            pass  # 累计净值取不到不致命
        if "cum_nav" not in out.columns:
            out["cum_nav"] = None
        return out

    def get_fund_purchase(self) -> pd.DataFrame:
        import akshare as ak
        raw = _with_retries(ak.fund_purchase_em)
        if raw is None or len(raw) == 0:
            return pd.DataFrame(columns=["code", "name", "fee_rate", "discount"])
        return pd.DataFrame({
            "code": raw["基金代码"].astype(str),
            "name": raw["基金简称"].astype(str),
            "fee_rate": raw["申购费率"].astype(str) if "申购费率" in raw.columns else "",
            "discount": raw["折扣率"].astype(str) if "折扣率" in raw.columns else "",
        })
