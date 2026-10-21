"""业务逻辑层：排行 / 搜索 / 详情 / 净值 / 持仓盈亏。

严格按 docs/API.md 实现：规范函数签名、缓存语义（kv 分片 + fund_nav 决策表）、
持仓净值解析顺序（排行快照→净值历史→DB 缓存→null）、mock 惰性播种。
数据源异常不在本层吞掉（portfolio 除外，见契约），由 api 层统一映射。
"""
import math
import re
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from . import config, data, db
from .data.errors import DataSourceError, FundNotAvailableError  # noqa: F401  (re-export)
from .data.mock_data import DEFAULT_MOCK_HOLDINGS

__all__ = [
    "DataSourceError", "FundNotAvailableError", "FundNotFound", "ConflictError",
    "get_rank", "rank_page", "get_fund_names", "search_funds", "fund_info",
    "fund_nav", "portfolio_view", "add_holding", "update_holding",
    "delete_holding",
]


class FundNotFound(RuntimeError):
    """基金代码不存在 / 无净值数据 / 持仓 id 不存在 → 404。"""


class ConflictError(RuntimeError):
    """持仓基金代码重复 → 409。"""


# ---------------------------------------------------------------- 工具

# API 排序 key → 归一化英文列。config.SORT_FIELDS 映射的是 AKShare 中文原始列，
# 客户端已归一化为英文列，这里按英文列排序。
_SORT_COLUMN = {
    "daily": "daily_pct", "1w": "w1", "1m": "m1", "3m": "m3", "6m": "m6",
    "1y": "y1", "2y": "y2", "3y": "y3", "ytd": "ytd", "since": "since",
}

_PERIOD_KEYS = ("daily_pct", "w1", "m1", "m3", "m6",
                "y1", "y2", "y3", "ytd", "since")

_RANK_COLUMNS = ["code", "name", "date", "nav", "cum_nav", "daily_pct",
                 "w1", "m1", "m3", "m6", "y1", "y2", "y3", "ytd",
                 "since", "fee"]

_CODE_RE = re.compile(r"^\d{6}$")

# fund_purchase_em 全表拉一次即可（接口不支持按代码查询）；
# 失败后置 _purchase_failed，进程内不再重试——申购费查不到不阻塞详情页。
_purchase_df: Optional[pd.DataFrame] = None
_purchase_failed = False


def _client():
    return data.get_client()


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_ts(epoch) -> str:
    return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")


def _py(v):
    """numpy 标量/NaN → JSON 安全的原生类型（NaN → None，避免非法 JSON）。"""
    if v is None:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _opt_float(v):
    """可空 float：None / NaN → None。

    真实客户端的 cum_nav 来自 pandas 左连接，缺失值是 float('nan') 而非
    None；NaN 一旦写进 DB 或响应体会让 allow_nan=False 的 JSON 序列化 500。
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _rank_df(rows) -> pd.DataFrame:
    """缓存/抓取结果统一成带完整列的 DataFrame（空结果也保留列，排序不报错）。"""
    return pd.DataFrame(rows if rows else [], columns=_RANK_COLUMNS)


# ---------------------------------------------------------------- 排行 / 名录

def get_rank(fund_type: str = "全部", force: bool = False):
    """排行快照：kv 按类型分片（rank:{type}），TTL 30 分钟；force 绕过缓存。

    返回 (英文列 DataFrame, fetched_at 字符串)；命中缓存时 fetched_at 为缓存时间。
    """
    key = f"rank:{fund_type}"
    if not force:
        cached = db.kv_get_fresh(key, config.RANK_TTL)
        if cached is not None:
            return _rank_df(cached), _fmt_ts(db.kv_get(key)[0])
    df = _client().get_open_fund_rank(fund_type)
    db.kv_set(key, df.to_dict("records"))
    return _rank_df(df.to_dict("records")), _fmt_ts(db.kv_get(key)[0])


def rank_page(fund_type: str = "全部", sort: str = "1m", order: str = "desc",
              kw: str = "", page: int = 1, size: int = 20,
              force: bool = False) -> dict:
    """GET /api/rank 响应体：服务端 kw 过滤 → 排序（NaN 最后）→ 分页。

    sort 取值与 config.SORT_FIELDS key 一致（daily|1w|1m|...），非法值按 "1m"
    处理；响应 "sort" 回填实际使用的值（契约 v0.1 修订）。
    """
    sort = sort if sort in _SORT_COLUMN else "1m"
    df, fetched_at = get_rank(fund_type, force=force)
    # kw：代码子串或名称包含（大小写不敏感，纯子串不走正则）
    if kw:
        k = kw.lower()
        mask = (df["code"].astype(str).str.contains(k, case=False, na=False, regex=False)
                | df["name"].astype(str).str.contains(k, case=False, na=False, regex=False))
        df = df[mask]
    # 稳定排序：同值保持抓取顺序，保证分页切片确定性
    df = df.sort_values(_SORT_COLUMN[sort], ascending=(order == "asc"),
                        na_position="last", kind="stable").reset_index(drop=True)
    size = max(10, min(100, int(size)))   # 契约：10–100 钳制
    page = max(1, int(page))
    total = len(df)
    chunk = df.iloc[(page - 1) * size:(page - 1) * size + size]
    return {
        "type": fund_type, "sort": sort, "order": order,
        "total": total, "page": page, "size": size,
        "fetched_at": fetched_at,
        "rows": [{k: _py(v) for k, v in rec.items()} for rec in chunk.to_dict("records")],
    }


def get_fund_names(force: bool = False) -> list:
    """全市场基金名录 [{code,name,type}]，kv key=fund_names，TTL 24 小时。"""
    key = "fund_names"
    if not force:
        cached = db.kv_get_fresh(key, config.NAMES_TTL)
        if cached is not None:
            return cached
    df = _client().get_fund_names()
    rows = df.to_dict("records")
    db.kv_set(key, rows)
    return rows


def search_funds(kw: str) -> list:
    """代码子串或名称子串（大小写不敏感），最多 10 条；无匹配返回 []。"""
    if not kw:
        return []
    k = kw.lower()
    out = []
    for r in get_fund_names():
        if k in str(r["code"]).lower() or k in str(r["name"]).lower():
            out.append({"code": str(r["code"]), "name": r["name"], "type": r["type"]})
        if len(out) >= 10:
            break
    return out


# ---------------------------------------------------------------- 详情

def _name_of(code: str) -> str:
    """名录查基金名；查不到/数据源故障返回 ""（不阻塞主流程）。"""
    try:
        for r in get_fund_names():
            if str(r["code"]) == str(code):
                return r["name"] or ""
    except DataSourceError:
        pass
    return ""


def _type_of(code: str):
    """名录查基金类型；查不到返回 None（契约：type 可为 null）。"""
    try:
        for r in get_fund_names():
            if str(r["code"]) == str(code):
                return r.get("type") or None
    except DataSourceError:
        pass
    return None


def _purchase_of(code: str):
    """申购费 {fee_rate, discount}；未拉取过/查不到/接口失败 → None（不重试不阻塞）。"""
    global _purchase_df, _purchase_failed
    if _purchase_df is None and not _purchase_failed:
        try:
            _purchase_df = _client().get_fund_purchase()
        except Exception:
            _purchase_failed = True
            return None
    if _purchase_df is None or len(_purchase_df) == 0:
        return None
    hit = _purchase_df[_purchase_df["code"].astype(str) == str(code)]
    if not len(hit):
        return None
    r = hit.iloc[0].to_dict()
    return {"fee_rate": _py(r.get("fee_rate")) or None,
            "discount": _py(r.get("discount")) or None}


def fund_info(code: str, force: bool = False) -> dict:
    """基金详情：先查排行“全部”快照（source=rank_cache），未命中再取净值历史末行
    （source=nav_hist）；两处都查不到 → FundNotFound。"""
    code = str(code)
    df, _ = get_rank("全部", force=force)
    hit = df[df["code"].astype(str) == code] if len(df) else df
    if len(hit):
        r = {k: _py(v) for k, v in hit.iloc[0].to_dict().items()}
        return {
            "code": code, "name": r.get("name"), "type": _type_of(code),
            "date": r.get("date"), "nav": r.get("nav"), "cum_nav": r.get("cum_nav"),
            "periods": {k: r.get(k) for k in _PERIOD_KEYS},
            "purchase": _purchase_of(code),
            "source": "rank_cache",
        }
    try:
        hist = _client().get_fund_nav_history(code)
    except FundNotAvailableError:
        raise FundNotFound(f"基金 {code} 不存在")
    # DataSourceError 继续上抛 → 503（网络故障 ≠ 基金不存在）
    last = hist.iloc[-1].to_dict()
    return {
        "code": code, "name": _name_of(code), "type": _type_of(code),
        "date": last.get("date"), "nav": _py(last.get("nav")),
        "cum_nav": _py(last.get("cum_nav")),
        "periods": {k: None for k in _PERIOD_KEYS},
        "purchase": _purchase_of(code),
        "source": "nav_hist",
    }


# ---------------------------------------------------------------- 历史净值

def _truncate(rows: list, days) -> list:
    """按自然日跨度本地截断。

    以数据中最新净值日期为基准（而非当天）：真实/模拟口径一致，且测试
    可完全确定（见 docs/API.md 实现说明）。
    """
    if not rows or days is None:
        return list(rows)
    max_date = datetime.strptime(rows[-1]["date"], "%Y-%m-%d").date()
    start = (max_date - timedelta(days=days)).isoformat()
    return [r for r in rows if r["date"] >= start]


def _nav_series(rows: list) -> list:
    """升序序列 + normalized（首点=100.0，保留 2 位小数）。"""
    base = float(rows[0]["nav"])
    out = []
    for r in rows:
        nav = float(r["nav"])
        out.append({
            "date": r["date"],
            "nav": nav,
            "cum_nav": _opt_float(r.get("cum_nav")),
            "normalized": round(nav / base * 100.0, 2),
        })
    return out


def fund_nav(code: str, range_key: str = "1y", force: bool = False):
    """返回 (响应体 dict, stale)。严格按契约“fund_nav 决策表”执行。"""
    code = str(code)
    days = config.NAV_RANGE_DAYS.get(range_key, 365)
    db_rows = db.nav_history(code)

    # ① 有 DB 缓存且非 force：直接用 DB，stale=True（不联网）
    if db_rows and not force:
        return {"code": code, "name": _name_of(code), "range": range_key,
                "series": _nav_series(_truncate(db_rows, days))}, True

    # ② 无缓存或 force：联网拉全量
    try:
        hist = _client().get_fund_nav_history(code)
        rows = [{"date": str(r["date"]), "nav": float(r["nav"]),
                 "cum_nav": _opt_float(r["cum_nav"])}
                for r in hist.to_dict("records")]
        db.nav_upsert(code, [(r["date"], r["nav"], r["cum_nav"]) for r in rows])
    except FundNotAvailableError:
        # ③ 基金不存在：DB 有历史数据则降级返回，否则 404
        if db_rows:
            return {"code": code, "name": _name_of(code), "range": range_key,
                    "series": _nav_series(_truncate(db_rows, days))}, True
        raise FundNotFound(f"基金 {code} 暂无净值数据")
    except DataSourceError:
        # ④ 网络故障：DB 有缓存 → 返回 stale；DB 空 → 上抛 → 503
        if db_rows:
            return {"code": code, "name": _name_of(code), "range": range_key,
                    "series": _nav_series(_truncate(db_rows, days))}, True
        raise

    # ② 成功：upsert 后本地截断，stale=False
    return {"code": code, "name": _name_of(code), "range": range_key,
            "series": _nav_series(_truncate(rows, days))}, False


# ---------------------------------------------------------------- 持仓

def _rank_nav_map() -> dict:
    """新鲜排行快照 → {code: (nav, date)}；数据源故障返回空表（契约：portfolio 照常返回）。"""
    m = {}
    try:
        df, _ = get_rank("全部")
    except DataSourceError:
        return m
    for rec in df.to_dict("records"):
        nav = _py(rec.get("nav"))
        if nav is not None and rec.get("date"):
            m[str(rec["code"])] = (nav, rec["date"])
    return m


def _resolve_holding_nav(code: str, rank_map: dict):
    """净值解析顺序（契约）：新鲜排行快照 → 客户端净值历史末行 → DB 末行(stale) → null(stale)。"""
    if code in rank_map:
        nav, d = rank_map[code]
        return nav, d, False
    try:
        hist = _client().get_fund_nav_history(code)
        last = hist.iloc[-1].to_dict()
        nav = _py(last.get("nav"))
        if nav is not None:
            return nav, last.get("date"), False
    except (FundNotAvailableError, DataSourceError):
        pass
    db_rows = db.nav_history(code)
    if db_rows:
        last = db_rows[-1]
        return float(last["nav"]), last["date"], True
    return None, None, True


def _holding_row(h: dict, nav, nav_date, stale: bool) -> dict:
    """持仓行 + 实时计算字段；金额 2 位小数，nav/cost_nav 4 位小数。"""
    shares = float(h["shares"])
    cost_nav = round(float(h["cost_nav"]), 4)
    if nav is None:
        market_value = cost_value = pnl = pnl_pct = None
        nav_out = None
    else:
        nav = float(nav)
        nav_out = round(nav, 4)
        market_value = round(shares * nav, 2)
        cost_value = round(shares * cost_nav, 2)
        # pnl 基于已舍入的 mv/cv，pnl_pct 基于 nav/cost_nav（与契约验算口径一致）
        pnl = round(market_value - cost_value, 2)
        pnl_pct = round((nav / cost_nav - 1.0) * 100.0, 2)
    return {
        "id": h["id"], "code": h["fund_code"],
        "name": h["fund_name"], "fund_name": h["fund_name"],
        "note": h["note"],
        "shares": shares, "cost_nav": cost_nav,
        "nav": nav_out, "nav_date": nav_date,
        "market_value": market_value, "cost_value": cost_value,
        "pnl": pnl, "pnl_pct": pnl_pct,
        "stale": bool(stale),
    }


def portfolio_view() -> dict:
    """GET /api/portfolio 响应体；mock 模式空持仓表先惰性播种演示数据。"""
    if config.MOCK and not db.list_holdings():
        for c, n, sh, cn, note in DEFAULT_MOCK_HOLDINGS:
            db.add_holding(c, n, sh, cn, note)

    holdings = db.list_holdings()
    rank_map = _rank_nav_map()
    rows = []
    for h in holdings:
        nav, nav_date, stale = _resolve_holding_nav(h["fund_code"], rank_map)
        rows.append(_holding_row(h, nav, nav_date, stale))

    # summary 只对有净值的行求和；全缺时各项 None；count 恒为持仓数
    valid = [r for r in rows if r["nav"] is not None]
    if valid:
        total_mv = round(sum(r["shares"] * r["nav"] for r in valid), 2)
        total_cost = round(sum(r["shares"] * r["cost_nav"] for r in valid), 2)
        total_pnl = round(total_mv - total_cost, 2)
        summary = {
            "count": len(rows), "total_mv": total_mv, "total_cost": total_cost,
            "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl / total_cost * 100.0, 2)
            if total_cost else None,
        }
    else:
        summary = {"count": len(rows), "total_mv": None, "total_cost": None,
                   "total_pnl": None, "total_pnl_pct": None}
    return {"updated_at": _now_str(), "holdings": rows, "summary": summary}


def _derive_shares_cost(code: str, market_value: float,
                        pnl_pct=None, pnl_amount=None):
    """按最新净值把「当前市值 + 持有盈亏」反推为 (shares, cost_nav)。

    - 份额：shares = 市值 / 最新净值（2 位小数）
    - 盈亏率口径：cost_nav = nav / (1 + pnl_pct/100)
    - 盈亏额口径：cost_value = 市值 - pnl_amount，cost_nav = cost_value / shares
    """
    if market_value <= 0:
        raise ValueError("market_value 必须大于 0")
    if pnl_pct is not None:
        pnl_pct = float(pnl_pct)
        if pnl_pct <= -100:
            raise ValueError("持有盈亏率必须大于 -100%")
    if pnl_amount is not None:
        pnl_amount = float(pnl_amount)
        if pnl_amount >= market_value:
            raise ValueError("持有盈亏额必须小于当前市值")
    nav, _, _ = _resolve_holding_nav(code, _rank_nav_map())
    if nav is None or float(nav) <= 0:
        raise DataSourceError("暂无法获取该基金最新净值，无法按市值反推份额")
    nav = float(nav)
    shares = round(market_value / nav, 2)
    if shares <= 0:
        raise ValueError("按最新净值计算得到的份额为 0，请检查当前市值")
    if pnl_pct is not None:
        cost_nav = nav / (1.0 + pnl_pct / 100.0)
    else:
        cost_nav = (market_value - float(pnl_amount)) / shares
    cost_nav = round(cost_nav, 4)
    if cost_nav <= 0:
        raise ValueError("按最新净值计算得到的成本净值必须大于 0")
    return shares, cost_nav


def add_holding(fund_code: str, shares: float = None, cost_nav: float = None,
                fund_name: str = "", note: str = "",
                market_value: float = None, pnl_pct: float = None,
                pnl_amount: float = None) -> dict:
    """新增持仓，返回含实时计算字段的完整行；重复 code 抛 ConflictError。

    两种录入方式（二选一）：
    - shares + cost_nav 直接录入
    - market_value + pnl_pct/pnl_amount 按最新净值反推份额与成本净值
    """
    fund_code = str(fund_code)
    if not _CODE_RE.match(fund_code):
        raise ValueError(f"fund_code 必须为 6 位数字：{fund_code}")

    by_value = (market_value is not None or pnl_pct is not None
                or pnl_amount is not None)
    if by_value:
        if shares is not None or cost_nav is not None:
            raise ValueError("份额/成本净值与市值/持有盈亏不能同时填写")
        if market_value is None:
            raise ValueError("按市值录入时必须提供 market_value")
        if (pnl_pct is None) == (pnl_amount is None):
            raise ValueError("持有盈亏率与持有盈亏额必须且只能填写一项")
    else:
        if shares is None or cost_nav is None:
            raise ValueError("需填写份额与成本净值，或当前市值与持有盈亏")
        shares, cost_nav = float(shares), float(cost_nav)
        if shares <= 0:
            raise ValueError("shares 必须大于 0")
        if cost_nav <= 0:
            raise ValueError("cost_nav 必须大于 0")

    if db.get_holding_by_code(fund_code):
        raise ConflictError("该基金已在持仓中")

    if by_value:
        shares, cost_nav = _derive_shares_cost(
            fund_code, float(market_value), pnl_pct, pnl_amount)

    if not fund_name:
        fund_name = _name_of(fund_code)   # 名录补全，补不到留空
    hid = db.add_holding(fund_code, fund_name, shares, cost_nav, note or "")
    h = db.get_holding(hid)
    nav, nav_date, stale = _resolve_holding_nav(fund_code, _rank_nav_map())
    return _holding_row(h, nav, nav_date, stale)


def update_holding(holding_id: int, **fields) -> dict:
    """部分更新持仓，返回更新后的完整行。

    两种更新方式（互斥）：
    - 直接：shares 和/或 cost_nav 改动（fund_code/fund_name/note 同以前）
    - 反推：market_value + pnl_pct/pnl_amount（复用 _derive_shares_cost 计算新的 shares/cost_nav）

    id 不存在抛 FundNotFound；code 与他人冲突抛 ConflictError。
    """
    # 分离：字段 → DB 直接更新；反推参数 → 先算 shares/cost_nav 再写 DB
    known = {"fund_code", "fund_name", "shares", "cost_nav", "note",
             "market_value", "pnl_pct", "pnl_amount"}
    unknown = set(fields) - known
    if unknown:
        raise ValueError(f"不支持的更新字段：{sorted(unknown)}")

    direct_db = {k: v for k, v in fields.items()
                 if k not in ("market_value", "pnl_pct", "pnl_amount")}
    mv = fields.get("market_value")
    pnl_pct = fields.get("pnl_pct")
    pnl_amount = fields.get("pnl_amount")
    has_direct = "shares" in direct_db or "cost_nav" in direct_db
    has_by_value = (mv is not None or pnl_pct is not None or pnl_amount is not None)

    if has_direct and has_by_value:
        raise ValueError("份额/成本净值与市值/持有盈亏不能同时填写")
    if has_by_value:
        if mv is None:
            raise ValueError("按市值更新时必须提供 market_value")
        if (pnl_pct is None) == (pnl_amount is None):
            raise ValueError("持有盈亏率与持有盈亏额必须且只能填写一项")

    # 先确保持仓存在（FundNotFound 早抛），并取得 fund_code
    before = db.get_holding(holding_id)
    if not before:
        raise FundNotFound(f"持仓 {holding_id} 不存在")

    # code 冲突检查
    fc = direct_db.get("fund_code")
    if fc is not None:
        fc = str(fc)
        direct_db["fund_code"] = fc
        if not _CODE_RE.match(fc):
            raise ValueError(f"fund_code 必须为 6 位数字：{fc}")
        other = db.get_holding_by_code(fc)
        if other and other["id"] != holding_id:
            raise ConflictError("该基金已在持仓中")

    # 份额/成本 直接校验
    if "shares" in direct_db and float(direct_db["shares"]) <= 0:
        raise ValueError("shares 必须大于 0")
    if "cost_nav" in direct_db and float(direct_db["cost_nav"]) <= 0:
        raise ValueError("cost_nav 必须大于 0")

    # 按市值反推 → 算新的 shares/cost_nav 合入 direct_db
    if has_by_value:
        target_code = fc if fc is not None else before["fund_code"]
        new_shares, new_cost = _derive_shares_cost(
            target_code, float(mv), pnl_pct, pnl_amount)
        direct_db["shares"] = new_shares
        direct_db["cost_nav"] = new_cost

    # 空 db 字段集合但有 by_value 也会走到这里（因为 has_by_value=True 时把 shares/cost_nav 加进 direct_db 了）
    if not direct_db:
        raise ValueError("至少提供一个待更新字段")

    if not db.update_holding(holding_id, **direct_db):
        raise FundNotFound(f"持仓 {holding_id} 不存在")
    h = db.get_holding(holding_id)

    # 名称为空（如刚改过代码）时从名录补全
    if not h["fund_name"]:
        filled = _name_of(h["fund_code"])
        if filled:
            db.update_holding(holding_id, fund_name=filled)
            h = db.get_holding(holding_id)
    nav, nav_date, stale = _resolve_holding_nav(h["fund_code"], _rank_nav_map())
    return _holding_row(h, nav, nav_date, stale)


def delete_holding(holding_id: int) -> None:
    """删除持仓；不存在抛 FundNotFound。"""
    if not db.delete_holding(holding_id):
        raise FundNotFound(f"持仓 {holding_id} 不存在")
