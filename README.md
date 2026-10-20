# 财富终端 · 基金

基于 FastAPI + AKShare 的个人基金查询与持仓管理小工具：公募基金排行筛选、单基金详情与历史净值曲线、个人持仓盈亏跟踪。单 SQLite 文件存储，浏览器打开即用，无第三方服务依赖。

## 功能

- **基金排行**：开放式基金排行，支持按类型筛选（股票型/混合型/债券型/指数型/QDII/LOF/FOF）、多周期涨跌幅排序（日/周/月/季/半年/1年/2年/3年/年初来/成立来）、代码或名称模糊搜索、分页浏览
- **基金详情**：最新净值、各周期涨跌幅、申购费率与折扣；历史净值曲线（1m/3m/6m/1y/3y/all，自动归一化，首点=100）
- **持仓管理**：个人持仓增删改查，实时计算市值、成本、盈亏、盈亏比例与组合汇总

> 截图：暂缺，后续补充。

## 快速开始

需要 Python 3.9+（建议 3.10–3.12）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m fundtool --port 8080
```

浏览器打开 <http://127.0.0.1:8080>。首次访问排行页会触发一次实时抓取（约 5–20 秒，取决于网络），之后 30 分钟内走缓存。

## 离线演示（--mock）

```bash
.venv/bin/python -m fundtool --mock --port 8080
```

无需外网：使用内置确定性样例数据演示全部功能。其中 **9xxxxx 区间的“示例xx基金”为虚构基金**；即使是真实代码（如 161725），其数值也是虚构的——mock 模式仅用于功能演示，**不代表任何真实净值**。

## 环境自检

```bash
.venv/bin/python -m fundtool doctor
```

退出码 `0`=全部通过，`1`=存在失败项。示例输出：

```
财富终端 · 基金 — 环境自检
========================================================
✅ Python 版本：3.10.12
✅ 依赖 akshare：akshare 1.18.88
ℹ️  代理环境变量：未配置（HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 均未设置；如需代理请先 export 对应变量）
✅ 连通性：https://fund.eastmoney.com/data/rankhandler.aspx → 200 OK
✅ 连通性：https://fund.eastmoney.com/Fund_JJJZ_Data.aspx → 200 OK
========================================================
全部检查通过，可直接启动：python -m fundtool --port 8080
```

常见失败原因与处置：

- **连通性连接失败**（connection reset / timeout / refused）：公司内网/防火墙拦截 → 配置代理（见下节）
- **响应含“访问被拒绝”等关键词**：被网络策略拦截 → 换代理或网络环境
- **akshare 导入失败**：依赖未装完整 → `.venv/bin/pip install -r requirements.txt`
- 离线演示无需外网：`--mock` 模式下连通性失败只提示、不计失败

## 网络要求

- 需要能访问 `fund.eastmoney.com`（AKShare 基金接口的上游）
- 需要代理的环境：`requests` 会自动读取环境变量，无需改代码：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
.venv/bin/python -m fundtool --port 8080
```

## 数据与备份

全部数据存于单文件 `data/fundtool.db`（SQLite，WAL 模式）：排行/基金名录缓存、历史净值、个人持仓。

- **备份** = 复制该文件；**迁移** = 把文件复制到新机器项目的 `data/` 目录即可，持仓不丢
- 缓存策略：

| 缓存 | 存储位置 | TTL / 更新策略 |
| --- | --- | --- |
| 排行快照（按类型分片 `rank:{type}`） | kv_cache 表 | 30 分钟，`force=1` 强制刷新 |
| 基金名录（代码/简称/类型） | kv_cache 表 | 24 小时 |
| 历史净值 `fund_nav_hist` | 独立表 | 无 TTL；首次/`force=1` 时全量拉取后 upsert 覆盖，期间走本地缓存（响应头 `X-Nav-Stale: 1` 标注） |

> 备份前建议停掉服务或确保无写入，避免 WAL 文件未合并。

## 后台运行

nohup 一行（日志追加到 fundtool.log）：

```bash
nohup .venv/bin/python -m fundtool --host 127.0.0.1 --port 8080 >> fundtool.log 2>&1 &
```

systemd（`/etc/systemd/system/fundtool.service`）：

```ini
[Unit]
Description=财富终端 · 基金 (fundtool)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/fundtool
ExecStart=/opt/fundtool/.venv/bin/python -m fundtool --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fundtool
journalctl -u fundtool -f
```

## 安全提示

- 本工具**没有任何鉴权**，仅设计为本机/局域网个人自用，**请勿暴露公网**
- 持仓数据只保存在本地 SQLite，不会上传到任何第三方

## 技术栈与目录结构

Python 3.9+ · FastAPI · uvicorn · AKShare · pandas · SQLite（标准库）

```
├── fundtool/
│   ├── __main__.py      # CLI 入口（--port / --mock / doctor）
│   ├── api.py           # FastAPI 路由（/api/*）
│   ├── services.py      # 业务逻辑（排行分页、缓存、盈亏计算）
│   ├── doctor.py        # 环境自检
│   ├── config.py        # 路径 / TTL / 常量
│   ├── db.py            # SQLite 数据层
│   └── data/
│       ├── akshare_client.py  # 真实数据客户端（AKShare 薄封装 + 重试）
│       └── mock_data.py       # 离线确定性样例客户端
├── static/              # 前端页面
├── tests/               # pytest 测试
├── data/                # 运行时数据（fundtool.db，自动创建）
└── docs/API.md          # API 契约（前后端共同依据）
```

## 免责声明

行情数据来自东方财富公开接口（经 AKShare），可能存在延迟、缺失或错误。本项目仅供个人学习与资产管理使用，不构成任何投资建议。
