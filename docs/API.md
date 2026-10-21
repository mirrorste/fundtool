# 财富终端 · 基金 — API 契约 v0.1

本文件是前后端与测试的共同开发依据。实现与测试都以此为准；如需变更，
先改本文档再改代码，并在团队任务里说明。

## 通用约定

- 全部 JSON 响应，中文不转义（ensure_ascii=False）
- 时间戳格式：`YYYY-MM-DD HH:MM:SS`（本地时间）
- 错误响应：
  - `404` → `{"error": "..."}`（未知基金代码 / 未知持仓 id）
  - `409` → `{"error": "..."}`（持仓代码重复）
  - `422` → FastAPI 默认校验错误格式
  - `503` → `{"error": "数据源暂不可达（网络无法访问东方财富或需配置代理）。请稍后重试，或先运行 `python -m fundtool doctor` 检查环境。"}`（数据源失败统一归此）
- 分页参数：`page`（≥1，默认 1）、`size`（10–100，默认 20）

## 端点

### `GET /api/health`
```json
{"ok": true, "mode": "live|mock", "version": "0.1.0", "now": "2026-10-20 12:00:00"}
```

### `GET /api/rank`
查询参数：
- `type` ∈ 全部/股票型/混合型/债券型/指数型/QDII/LOF/FOF（默认 全部）
- `sort` ∈ daily|1w|1m|3m|6m|1y|2y|3y|ytd|since（默认 1m；与 config.SORT_FIELDS key 一致。注意：m1/w1 等是行内**数据字段名**，排序参数用 1m/w1 形式）
- `order` ∈ asc|desc（默认 desc）
- `kw`：代码或名称模糊匹配（可选）
- `force`：=1 时强制刷新排行缓存（可选）

响应：
```json
{
  "type": "全部", "sort": "1m", "order": "desc",
  "total": 24, "page": 1, "size": 20,
  "fetched_at": "2026-10-20 11:30:00",
  "rows": [
    {"code": "161725", "name": "招商中证白酒", "date": "2026-10-20",
     "nav": 1.2345, "cum_nav": 1.2345, "daily_pct": 0.52,
     "w1": 1.2, "m1": 2.3, "m3": 4.1, "m6": 5.5, "y1": 12.3,
     "y2": 20.1, "y3": 30.2, "ytd": 8.8, "since": 55.6, "fee": "0.15"}
  ]
}
```
`fetched_at` 为排行快照的抓取时间（缓存新鲜时返回缓存时间）。
`kw` 无匹配 → `total=0, rows=[]`（不是 404）。

### `GET /api/fund/search?kw=`
`kw` 必填，代码前缀 / 名称 / 拼音都不要求（仅代码子串或名称子串，大小写不敏感）。
```json
{"results": [{"code": "161725", "name": "招商中证白酒", "type": "指数型"}]}
```
最多 10 条；无匹配 `results=[]`。

### `GET /api/fund/{code}/info`
```json
{
  "code": "161725", "name": "招商中证白酒", "type": "指数型",
  "date": "2026-10-20", "nav": 1.2345, "cum_nav": 1.2345,
  "periods": {"daily_pct": 0.52, "w1": 1.2, "m1": 2.3, "m3": 4.1,
              "m6": 5.5, "y1": 12.3, "y2": 20.1, "y3": 30.2,
              "ytd": 8.8, "since": 55.6},
  "purchase": {"fee_rate": "0.15%", "discount": "0.1"} ,
  "source": "rank_cache"
}
```
- `type`：来自 fund_name_em 缓存；查不到为 `null`
- `purchase`：来自 fund_purchase_em；查不到为 `null`（不报错）
- `source`：`"rank_cache"`（净值取自排行快照）或 `"nav_hist"`（取自净值历史末行）
- 代码不存在（两处都查不到）→ 404

### `GET /api/fund/{code}/nav?range=&force=`
`range` ∈ 1m|3m|6m|1y|3y|all（默认 1y）；`force`=1 强制重拉。
实现说明：客户端恒拉取成立来全量净值（真实 AKShare 接口忽略 period 参数），
**services 层按 `config.NAV_RANGE_DAYS` 的自然日跨度本地截断**（真实/模拟口径一致）。
真实数据 `cum_nav` 来自"累计净值走势"指标合并，取不到时为 null（前端隐藏该列即可）。
```json
{
  "code": "161725", "name": "招商中证白酒", "range": "1y",
  "series": [
    {"date": "2025-10-21", "nav": 1.2, "cum_nav": 1.2, "normalized": 100.0},
    {"date": "2025-10-22", "nav": 1.21, "cum_nav": 1.21, "normalized": 100.83}
  ]
}
```
- `series` 按日期升序；`normalized = nav / nav[0] * 100`（保留 2 位小数）
- 无数据 → 404 `{"error": "基金 xxxxx 暂无净值数据"}`

### `GET /api/portfolio`
```json
{
  "updated_at": "2026-10-20 12:00:00",
  "holdings": [
    {"id": 1, "code": "161725", "name": "招商中证白酒", "note": "白酒赛道",
     "shares": 10000.0, "cost_nav": 1.65,
     "nav": 1.23, "nav_date": "2026-10-20",
     "market_value": 12300.0, "cost_value": 16500.0,
     "pnl": -4200.0, "pnl_pct": -25.45, "stale": false}
  ],
  "summary": {"count": 4, "total_mv": 35000.0, "total_cost": 42000.0,
              "total_pnl": -7000.0, "total_pnl_pct": -16.67}
}
```
- 金额/净值四舍五入 2 位小数；`pnl_pct` 为百分比数值（-25.45 表示 -25.45%）
- 净值解析顺序：排行快照（新鲜）→ 净值历史末行（拉取失败时用 DB 缓存）→ 都没有则
  `nav=null, nav_date=null, market_value=null, pnl=null, pnl_pct=null, stale=true`
- `summary` 只对有净值的行求和；若全部无净值则各项为 `null`；`count` 恒为持仓数
- `cost_nav=0` 不合法（写入时 422），计算上无需处理

### `POST /api/portfolio`
两种录入方式（二选一）：

```json
{"fund_code": "161725", "shares": 10000, "cost_nav": 1.65,
 "fund_name": "", "note": ""}
```

```json
{"fund_code": "161725", "market_value": 10000, "pnl_pct": 12.5,
 "fund_name": "", "note": ""}
```
- 直接录入：`shares>0`；`cost_nav>0`
- 按市值反推：`market_value>0` + `pnl_pct` 或 `pnl_amount`（二者只能给一个，亏损为负）
  - 服务端按最新净值计算：`shares = round(market_value / nav, 2)`；
    `pnl_pct` 口径 `cost_nav = round(nav / (1 + pnl_pct/100), 4)`；
    `pnl_amount` 口径 `cost_nav = round((market_value - pnl_amount) / shares, 4)`
  - 约束：`pnl_pct > -100`；`pnl_amount < market_value`（保证成本净值为正）
  - 取不到该基金最新净值 → `503`（无法反推）
- 校验：`fund_code` 6 位数字；`fund_name`/`note` 可选（≤50 字符）；两种方式混填、
  盈亏率与盈亏额同时给或都不给 → `422`
- `fund_name` 为空时服务端自动从基金名录缓存补全（补不到留空）
- 成功 `201` → `{"id": 1, ...}`（同 GET /api/portfolio 中一行，含实时计算字段）
- 代码已存在 → `409 {"error": "该基金已在持仓中"}`

### `PUT /api/portfolio/{id}`
部分更新：`{"fund_code"?, "shares"?, "cost_nav"?, "fund_name"?, "note"?"}`（至少一项）。
成功 `200` → 更新后的完整行；不存在 `404`；`fund_code` 与他人冲突 `409`。

### `DELETE /api/portfolio/{id}`
成功 `200 {"deleted": true}`；不存在 `404`。

## 数据层注入点（测试用）

- `fundtool.data.set_client(client)` / `reset_client()`：替换全局数据客户端
- `fundtool.data.mock_data.MockClient(end_date=None)`：确定性样例客户端，
  接口与 `AkshareClient` 完全一致（见 `fundtool/data/akshare_client.py`）
- 归一化列（客户端统一输出）：
  - rank：`code,name,date,nav,cum_nav,daily_pct,w1,m1,m3,m6,y1,y2,y3,ytd,since,fee`
  - names：`code,name,type`
  - nav_history：`date,nav,cum_nav,daily_pct`（升序）
  - purchase：`code,name,fee_rate,discount`

## services.py 规范函数签名（测试与内部调用依据，Lead 定稿）

模块 `fundtool.services`：

| 函数 | 签名 | 说明 |
|---|---|---|
| get_rank | `(fund_type="全部", force=False) -> (pd.DataFrame, fetched_at: str)` | 英文列；命中缓存时 fetched_at 为缓存时间 |
| rank_page | `(fund_type="全部", sort="1m", order="desc", kw="", page=1, size=20, force=False) -> dict` | 即 GET /api/rank 响应体；sort 非法值→按 1m 处理 |
| get_fund_names | `(force=False) -> list[dict]` | `[{code,name,type}]` |
| search_funds | `(kw) -> list[dict]` | 最多 10 条 |
| fund_info | `(code, force=False) -> dict` | 查不到抛 `FundNotFound` |
| fund_nav | `(code, range_key="1y", force=False) -> (dict, stale: bool)` | dict 即 GET nav 响应体；stale 决定响应头 |
| portfolio_view | `() -> dict` | 即 GET /api/portfolio 响应体 |
| add_holding | `(fund_code, shares=None, cost_nav=None, fund_name="", note="", market_value=None, pnl_pct=None, pnl_amount=None) -> dict` | 重复 code 抛 `ConflictError`；按市值反推时 nav 不可用抛 `DataSourceError` |
| update_holding | `(holding_id, **fields) -> dict` | fields ∈ fund_code/fund_name/shares/cost_nav/note |
| delete_holding | `(holding_id) -> None` | 不存在抛 `FundNotFound` |

异常（定义于 `fundtool.services`，api 层捕获映射）：
- `DataSourceError` / `FundNotAvailableError`：自 `fundtool.data.errors` re-export
- `FundNotFound(RuntimeError)` → 404（基金代码不存在 / 持仓 id 不存在）
- `ConflictError(RuntimeError)` → 409（持仓代码重复）

## 缓存语义（services 层行为，测试依据）

- 排行快照按 `type` 分片存 kv（key `rank:{type}`），TTL 30 分钟；`force=1` 绕过
- 基金名录 kv（key `fund_names`），TTL 24 小时
- 净值历史存表 `fund_nav_hist`（code,date 主键）；客户端恒返回全量历史，
  services 全量 upsert 后按 range 本地截断（`force=1` 时重新拉取覆盖）
- **fund_nav 决策表**（stale=True ⇒ 响应头 `X-Nav-Stale: 1`）：
  | 条件 | 行为 |
  |---|---|
  | 有 DB 缓存 且 非 force | 直接用 DB，stale=True（不联网） |
  | 无 DB 缓存 或 force | 联网拉取；成功→upsert、stale=False |
  | 拉取抛 `FundNotAvailableError` 且 DB 空 | 抛 `FundNotFound`（404） |
  | 拉取抛 `DataSourceError` 且 DB 有缓存 | 返回 DB，stale=True |
  | 拉取抛 `DataSourceError` 且 DB 空 | 向上抛 → 503 |
- 数据源抛 `DataSourceError` 时（其他端点）：
  - rank/search → 503
  - portfolio → 照常返回，对应行 `stale=true`
- **mock 持仓自动播种**：`GET /api/portfolio` 处理时，若 `config.MOCK` 且持仓表为空，
  先写入 `fundtool.data.mock_data.DEFAULT_MOCK_HOLDINGS`（4 条）再计算（惰性播种，
  便于 TestClient 测试；非 mock 模式不播种）
