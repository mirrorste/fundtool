"""AKShare 列名归一化回归测试（不联网，monkeypatch 数据源函数）。

背景：AKShare 1.18.88 的名录接口返回「基金类型」而非旧版「类型」，
申购接口返回「手续费」（数值百分比）而非旧版「申购费率」+「折扣率」；
列名不匹配曾导致 /api/fund/search、/info、/nav 全部 500。
"""
import pandas as pd

from fundtool.data.akshare_client import AkshareClient, _pct


def test_get_fund_names_maps_fund_type_column(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(ak, "fund_name_em", lambda: pd.DataFrame({
        "基金代码": ["000001"],
        "拼音缩写": ["HXCZHH"],
        "基金简称": ["华夏成长混合"],
        "基金类型": ["混合型-灵活"],
        "拼音全称": ["HUAXIACHENGZHANGHUNHE"],
    }))
    df = AkshareClient().get_fund_names()
    assert list(df.columns) == ["code", "name", "type"]
    assert df.iloc[0].to_dict() == {
        "code": "000001", "name": "华夏成长混合", "type": "混合型-灵活"}


def test_get_fund_names_supports_legacy_type_column(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(ak, "fund_name_em", lambda: pd.DataFrame({
        "基金代码": ["000001"],
        "基金简称": ["华夏成长混合"],
        "类型": ["混合型"],
    }))
    assert AkshareClient().get_fund_names().iloc[0]["type"] == "混合型"


def test_get_fund_names_raises_on_unexpected_columns(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(ak, "fund_name_em", lambda: pd.DataFrame({"foo": ["bar"]}))
    try:
        AkshareClient().get_fund_names()
    except Exception as exc:
        assert "名录接口返回列异常" in str(exc)
    else:
        raise AssertionError("列异常时应抛 DataSourceError")


def test_get_fund_purchase_uses_fee_column(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(ak, "fund_purchase_em", lambda: pd.DataFrame({
        "基金代码": ["000001", "000003"],
        "基金简称": ["华夏成长混合", "中海可转债债券A"],
        "手续费": [0.15, float("nan")],
    }))
    df = AkshareClient().get_fund_purchase()
    assert list(df.columns) == ["code", "name", "fee_rate", "discount"]
    assert df.iloc[0]["fee_rate"] == "0.15%"
    assert df.iloc[1]["fee_rate"] == ""
    assert df.iloc[0]["discount"] == ""


def test_pct_normalizes_numbers_and_keeps_percent_strings():
    assert list(_pct(pd.Series(["0.15%", 0.1, None, float("nan")]))) == [
        "0.15%", "0.1%", "", ""]
