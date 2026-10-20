"""测试公共 fixture。

- env：临时 DB（monkeypatch config.DB_PATH 到 tmp_path）+ 固定 end_date 的
  MockClient 注入（data.set_client），测试结束 reset_client
- app：fundtool.api.create_app()（backend 文件未就位时此处报错，属预期）
- client：starlette TestClient（走 lifespan 启动事件）
"""
from datetime import date

import pytest

from fundtool import config, data, db
from fundtool.data.mock_data import MOCK_FUNDS, MockClient, generate_nav_series

MOCK_END_DATE = date(2026, 10, 20)

# code → (name, type, drift, vol, start_nav)
_MOCK_PARAMS = {c: (n, t, d, v, s) for c, n, t, d, v, s in MOCK_FUNDS}


def mock_last_nav(code: str, end_date: date = MOCK_END_DATE) -> float:
    """MockClient 中该基金最后一个交易日的单位净值（确定性，可直接复算）。"""
    _name, _type, drift, vol, nav0 = _MOCK_PARAMS[code]
    series = generate_nav_series(code, drift, vol, nav0, end_date)
    return series[-1][1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离环境：临时 SQLite + 固定日期的确定性 MockClient。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_fundtool.db")
    monkeypatch.setattr(config, "MOCK", True)
    db.init_db()
    data.set_client(MockClient(end_date=MOCK_END_DATE))
    yield
    data.reset_client()


@pytest.fixture()
def app(env):
    from fundtool.api import create_app

    return create_app()


@pytest.fixture()
def client(app):
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        yield c
