"""全局配置：路径、缓存 TTL、AKShare 列名常量。

路径与 MOCK 标志可在运行时被修改（测试用 tmp_path 覆盖、CLI --mock 置位）。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FUNDTOOL_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "fundtool.db"
STATIC_DIR = BASE_DIR / "static"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

# 缓存 TTL（秒）
RANK_TTL = 30 * 60        # 排行快照（按类型分片）
NAMES_TTL = 24 * 3600     # 全市场基金 代码/简称/类型

# 数据抓取
REQUEST_TIMEOUT = 20      # 单次请求超时（秒）
MAX_RETRIES = 3           # 失败重试次数
RETRY_BACKOFF = 1.5       # 指数退避底数

# 是否使用内置样例数据（离线演示）；由 --mock / FUNDTOOL_MOCK 置位
MOCK = bool(os.environ.get("FUNDTOOL_MOCK"))

# fund_open_fund_rank_em 返回列（AKShare 1.18.88 实测）
RANK_COLUMNS = [
    "序号", "基金代码", "基金简称", "日期", "单位净值", "累计净值",
    "日增长率", "近1周", "近1月", "近3月", "近6月", "近1年",
    "近2年", "近3年", "今年来", "成立来", "自定义", "手续费",
]

# 排行可排序字段（API 用英文 key → 数据列名）
SORT_FIELDS = {
    "daily": "日增长率",
    "1w": "近1周",
    "1m": "近1月",
    "3m": "近3月",
    "6m": "近6月",
    "1y": "近1年",
    "2y": "近2年",
    "3y": "近3年",
    "ytd": "今年来",
    "since": "成立来",
}

# 基金类型（fund_open_fund_rank_em 的 symbol 取值）
FUND_TYPES = ["全部", "股票型", "混合型", "债券型", "指数型", "QDII", "LOF", "FOF"]

# 历史净值区间：API key → 自然日跨度（None=全部）。
# 注意：AKShare 的 fund_open_fund_info_em(单位净值走势) 实际忽略 period 参数、
# 恒返回成立来全量数据，因此由 services 层本地按日跨度截断（真实/模拟口径一致）。
NAV_RANGE_DAYS = {
    "1m": 30,
    "3m": 91,
    "6m": 182,
    "1y": 365,
    "3y": 1096,
    "all": None,
}
