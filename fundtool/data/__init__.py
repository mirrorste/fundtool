"""数据层入口：按 config.MOCK 选择真实 AKShare 客户端或内置样例客户端。"""
_client = None


def get_client():
    global _client
    if _client is None:
        from .. import config
        if config.MOCK:
            from .mock_data import MockClient
            _client = MockClient()
        else:
            from .akshare_client import AkshareClient
            _client = AkshareClient()
    return _client


def set_client(client) -> None:
    """测试注入用。"""
    global _client
    _client = client


def reset_client() -> None:
    global _client
    _client = None
