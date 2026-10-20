"""数据层异常定义（真实/模拟客户端共用）。"""


class DataSourceError(RuntimeError):
    """数据源请求失败（网络不可达 / 接口异常 / 重试耗尽）。"""


class FundNotAvailableError(DataSourceError):
    """基金代码不存在或无净值数据（区别于网络故障）。"""
