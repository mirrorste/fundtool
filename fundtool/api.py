"""FastAPI 路由层：/api/* + 静态页挂载 + 错误映射。

错误映射（docs/API.md）：FundNotFound→404 / ConflictError→409 /
DataSourceError→503（固定文案）/ 参数校验 422（FastAPI 默认格式）。
全部 JSON 响应由 Starlette JSONResponse 输出（ensure_ascii=False）。
"""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, config, db
from .services import (
    ConflictError, DataSourceError, FundNotAvailableError, FundNotFound,
    add_holding, delete_holding, fund_info, fund_nav, portfolio_view,
    rank_page, search_funds, update_holding,
)

# 契约固定的 503 文案（数据源失败统一归此）
ERR_DATA_SOURCE = ("数据源暂不可达（网络无法访问东方财富或需配置代理）。"
                   "请稍后重试，或先运行 `python -m fundtool doctor` 检查环境。")

FundType = Literal["全部", "股票型", "混合型", "债券型", "指数型", "QDII", "LOF", "FOF"]
OrderKey = Literal["asc", "desc"]
RangeKey = Literal["1m", "3m", "6m", "1y", "3y", "all"]


class HoldingIn(BaseModel):
    """POST /api/portfolio 请求体（6 位代码 / 份数>0 / 成本>0 / 名称备注≤50）。"""
    fund_code: str = Field(..., pattern=r"^\d{6}$")
    shares: float = Field(..., gt=0)
    cost_nav: float = Field(..., gt=0)
    fund_name: str = Field("", max_length=50)
    note: str = Field("", max_length=50)


class HoldingUpdate(BaseModel):
    """PUT /api/portfolio/{id} 部分更新（全 Optional，至少一项由路由校验）。"""
    fund_code: Optional[str] = Field(None, pattern=r"^\d{6}$")
    shares: Optional[float] = Field(None, gt=0)
    cost_nav: Optional[float] = Field(None, gt=0)
    fund_name: Optional[str] = Field(None, max_length=50)
    note: Optional[str] = Field(None, max_length=50)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init_db()   # 启动即建表（幂等），首次请求不阻塞在建表上
        yield

    app = FastAPI(title="财富终端 · 基金", version=__version__, lifespan=lifespan)

    # ---------------- 错误映射 ----------------

    @app.exception_handler(FundNotFound)
    async def _h_404(request: Request, exc: FundNotFound):
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.exception_handler(ConflictError)
    async def _h_409(request: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"error": str(exc)})

    # FundNotAvailableError 是 DataSourceError 子类：未兜住意味着“基金不存在”→ 404
    @app.exception_handler(FundNotAvailableError)
    async def _h_fna(request: Request, exc: FundNotAvailableError):
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.exception_handler(DataSourceError)
    async def _h_503(request: Request, exc: DataSourceError):
        return JSONResponse(status_code=503, content={"error": ERR_DATA_SOURCE})

    # services 防御性重校验抛 ValueError → 与 FastAPI 校验同语义（正常路径走不到）
    @app.exception_handler(ValueError)
    async def _h_422(request: Request, exc: ValueError):
        return JSONResponse(status_code=422,
                            content={"detail": [{"msg": str(exc)}]})

    # ---------------- 路由 ----------------
    # 注意：/api/fund/search 必须先于 /api/fund/{code}/... 注册，否则 "search"
    # 会被当作 fund code 吞掉。

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": "mock" if config.MOCK else "live",
                "version": __version__,
                "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    @app.get("/api/rank")
    def rank(type: FundType = Query("全部"),
             sort: str = Query("1m"),          # 非法值在 services 按 "1m" 处理
             order: OrderKey = Query("desc"),
             kw: str = Query(""),
             page: int = Query(1),
             size: int = Query(20),            # services 钳制 10–100（契约要求钳制而非 422）
             force: int = Query(0)):
        return rank_page(fund_type=type, sort=sort, order=order, kw=kw,
                         page=page, size=size, force=bool(force))

    @app.get("/api/fund/search")
    def fund_search(kw: str = Query(...)):
        return {"results": search_funds(kw)}

    @app.get("/api/fund/{code}/info")
    def fund_info_route(code: str, force: int = Query(0)):
        return fund_info(code, force=bool(force))

    @app.get("/api/fund/{code}/nav")
    def fund_nav_route(code: str,
                       range_key: RangeKey = Query("1y", alias="range"),
                       force: int = Query(0)):
        body, stale = fund_nav(code, range_key, force=bool(force))
        resp = JSONResponse(content=body)
        if stale:   # 契约：stale=True ⇒ 响应头 X-Nav-Stale: 1
            resp.headers["X-Nav-Stale"] = "1"
        return resp

    @app.get("/api/portfolio")
    def portfolio():
        return portfolio_view()

    @app.post("/api/portfolio", status_code=201)
    def portfolio_add(body: HoldingIn):
        return add_holding(body.fund_code, body.shares, body.cost_nav,
                           body.fund_name, body.note)

    @app.put("/api/portfolio/{holding_id}")
    def portfolio_update(holding_id: int, body: HoldingUpdate):
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if not fields:   # 契约：至少一项
            raise HTTPException(status_code=422, detail="至少提供一个待更新字段")
        return update_holding(holding_id, **fields)

    @app.delete("/api/portfolio/{holding_id}")
    def portfolio_delete(holding_id: int):
        delete_holding(holding_id)
        return {"deleted": True}

    # 静态单页前端（最后挂载，不吞上面的 /api 路由）
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True),
              name="static")
    return app


app = create_app()
